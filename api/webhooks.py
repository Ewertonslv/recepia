"""Webhook callback da Evolution API.

Autenticado via HMAC-SHA256 sobre o body (F2).
Idempotente por message_id (G9).
Schema validado (F14).
"""
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from core.limiter import limiter
from core.phones import tenta_normalizar as normalizar_telefone
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


def _validar_assinatura_hmac(body_bytes: bytes, signature_header: str | None) -> bool:
    """F2: valida assinatura HMAC-SHA256 do body com EVOLUTION_WEBHOOK_SECRET.

    Em produção o secret é obrigatório (config.py falha no boot sem ele quando
    DEBUG=false). Só é permitido rodar sem assinatura em DEBUG — e mesmo assim
    apenas quando o secret está de fato vazio.
    """
    if not settings.EVOLUTION_WEBHOOK_SECRET:
        # Sem secret só acontece em DEBUG (o boot bloqueia em prod). Aceita local.
        if settings.DEBUG:
            return True
        # Cinto e suspensório: se por algum motivo chegou aqui sem secret fora
        # de DEBUG, rejeita em vez de abrir o endpoint.
        log.error("Webhook sem EVOLUTION_WEBHOOK_SECRET fora de DEBUG — rejeitando.")
        return False
    if not signature_header:
        return False
    expected = hmac.new(
        settings.EVOLUTION_WEBHOOK_SECRET.encode(),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()
    # Aceita formatos: "sha256=<hex>" ou "<hex>"
    received = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, received)


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/evolution")
@limiter.limit("120/minute")
async def webhook_evolution(
    request: Request,
    x_webhook_signature: str | None = Header(None),
    db: Session = Depends(get_db_dependency),
):
    # F14: limite de body antes de parsear
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Body grande demais")

    # F2: valida HMAC
    if not _validar_assinatura_hmac(body_bytes, x_webhook_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Assinatura inválida")

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
        texto = (
            (data.message.conversation if data.message else None)
            or ((data.message.extendedTextMessage or {}).get("text") if data.message else None)
            or ""
        )
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
        scheduler.processar_resposta_paciente(
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
