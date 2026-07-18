"""Webhook callback da Evolution API.

Autenticado via token estático compartilhado no header X-Webhook-Token (F2).
Idempotente por message_id (G9).
Schema validado (F14).
"""
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from config import settings
from core.limiter import limiter
from core.phones import tenta_normalizar as normalizar_telefone
from core.trial import trial_expirado
from database import get_db_dependency
from models import Clinica, Interacao
from services.scheduler import SchedulerService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhook", tags=["webhook"])

MAX_BODY_BYTES = 65536  # 64 KB (F14)


# ============================================================================
# Pydantic schema (F14) — restringe formato do body
# ============================================================================

class EvolutionMessageKey(BaseModel):
    remoteJid: str = Field(..., max_length=200)
    fromMe: bool = False
    id: str | None = Field(None, max_length=200)


class EvolutionMessageContent(BaseModel):
    conversation: str | None = Field(None, max_length=4000)
    extendedTextMessage: dict | None = None


class EvolutionMessageData(BaseModel):
    key: EvolutionMessageKey | None = None
    message: EvolutionMessageContent | None = None
    state: str | None = None
    pushName: str | None = Field(None, max_length=200)


class EvolutionWebhookIn(BaseModel):
    event: str = Field(..., max_length=100)
    instance: str = Field(..., max_length=100)
    data: EvolutionMessageData | dict | None = None


# ============================================================================
# Helpers
# ============================================================================

def _extrair_telefone(remote_jid: str) -> str:
    """`5511999999999@s.whatsapp.net` → `5511999999999`. Group JIDs filtrados antes."""
    if "@" not in remote_jid:
        return remote_jid
    return remote_jid.split("@")[0]


def _validar_token(token_header: str | None) -> bool:
    """F2: autentica o webhook por token estático compartilhado.

    O Evolution API não assina o corpo (não faz HMAC por requisição); ele só
    reenvia headers estáticos configurados em `webhook.headers`. Por isso a
    autenticação é um segredo fixo (EVOLUTION_WEBHOOK_SECRET) comparado em tempo
    constante — o mesmo valor que `configurar_webhook` injeta no X-Webhook-Token.
    Sobre HTTPS o segredo não trafega em claro.

    Em produção o secret é obrigatório (config.py falha no boot sem ele quando
    DEBUG=false). Só é permitido rodar sem token em DEBUG — e mesmo assim apenas
    quando o secret está de fato vazio.
    """
    secret = settings.EVOLUTION_WEBHOOK_SECRET
    if not secret:
        # Sem secret só acontece em DEBUG (o boot bloqueia em prod). Aceita local.
        if settings.DEBUG:
            return True
        # Cinto e suspensório: se por algum motivo chegou aqui sem secret fora
        # de DEBUG, rejeita em vez de abrir o endpoint.
        log.error("Webhook sem EVOLUTION_WEBHOOK_SECRET fora de DEBUG — rejeitando.")
        return False
    if not token_header:
        return False
    # Aceita "Bearer <token>" ou o token puro.
    recebido = token_header.removeprefix("Bearer ").strip()
    return hmac.compare_digest(secret, recebido)


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/evolution")
@limiter.limit("120/minute")
async def webhook_evolution(
    request: Request,
    x_webhook_token: str | None = Header(None),
    db: Session = Depends(get_db_dependency),
):
    # F14: limite de body antes de parsear
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Body grande demais")

    # F2: valida token estático compartilhado
    if not _validar_token(x_webhook_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")

    # F14: parse com schema (rejeita junk).
    # Sprint 6: log.exception + 400 (em vez de silenciar) — schema da Evolution muda, queremos saber.
    import json
    try:
        body_dict = json.loads(body_bytes)
        payload = EvolutionWebhookIn(**body_dict)
    except Exception:
        log.exception("Webhook Evolution: payload inválido — schema mudou?")
        raise HTTPException(400, "Payload inválido")

    event = payload.event
    instance_name = payload.instance
    data = payload.data if isinstance(payload.data, EvolutionMessageData) else None

    clinica = db.query(Clinica).filter(
        Clinica.evolution_instance_name == instance_name,
    ).first()
    if not clinica:
        # Não vaza se clínica existe — sempre 200 silent (B8)
        return {"status": "ok"}

    # connection.update: atualiza estado de conexão
    if event.lower() in ("connection.update", "connection_update"):
        estado = (payload.data.get("state") if isinstance(payload.data, dict) else (data.state if data else None)) or ""
        clinica.evolution_conectado = estado.lower() == "open"
        db.commit()
        return {"status": "ok"}

    # messages.upsert: mensagem recebida
    if event.lower() in ("messages.upsert", "messages_upsert"):
        # Clínica desativada (churn) ou trial vencido não processam mensagens:
        # o bot é o core do produto — sem isso, uso gratuito perpétuo. 200
        # silencioso, mesmo contrato do "instância não existe" (B8).
        if not clinica.ativo or trial_expirado(clinica):
            return {"status": "ok"}

        if not data or not data.key or data.key.fromMe:
            return {"status": "ok"}

        remote_jid = data.key.remoteJid
        if "@g.us" in remote_jid:
            return {"status": "ok"}  # grupos ignorados

        telefone_raw = _extrair_telefone(remote_jid)
        telefone = normalizar_telefone(telefone_raw)
        if not telefone:
            return {"status": "ok"}

        message_id = data.key.id  # G9: usado pra dedup
        # extendedTextMessage é dict livre — "text" precisa ser string, senão
        # .strip() estoura AttributeError → 500 → Evolution retenta pra sempre.
        ext_text = (data.message.extendedTextMessage or {}).get("text") if data.message else None
        if not isinstance(ext_text, str):
            ext_text = None
        texto = (
            (data.message.conversation if data.message else None)
            or ext_text
            or ""
        )[:4000]
        if not texto.strip():
            return {"status": "ok"}

        # G9: dedup por message_id — se já processamos esta mensagem, ignora.
        # Nota: mensagens SEM message_id não são deduplicadas (raro na Evolution;
        # se a Evolution reentregar sem id, pode haver reprocessamento).
        if message_id:
            ja_processada = db.query(Interacao).filter(
                Interacao.evolution_message_id == message_id,
            ).first()
            if ja_processada:
                return {"status": "ok"}

        scheduler = SchedulerService(db)
        # Pipeline é síncrono (Groq SDK + httpx bloqueantes). No event loop ele
        # congelaria a API inteira; no threadpool só ocupa um worker thread.
        await run_in_threadpool(
            scheduler.processar_resposta_paciente,
            clinica=clinica,
            telefone=telefone,
            mensagem=texto,
            evolution_message_id=message_id,
            push_name=data.pushName,
        )
        # B8: sempre retorna shape mínimo, sem vazar agendamento_id/intencao
        return {"status": "ok"}

    return {"status": "ok"}


@router.get("/status")
def webhook_status():
    return {"status": "ok"}
