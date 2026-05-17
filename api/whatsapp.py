"""Endpoints pra clínica conectar/desconectar/checar WhatsApp.

Autenticado via JWT da clínica. Dashboard mostra QR Code pra dona escanear.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config import settings
from database import get_db_dependency
from models import AcaoAudit, Clinica
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core import audit
from services.whatsapp import WhatsAppService

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


@router.post("/conectar")
def conectar(
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Cria instância no Evolution, configura webhook automático (G5), retorna QR Code."""
    ws = WhatsAppService()
    ws.criar_instancia(clinica.evolution_instance_name)

    # G5: aponta o webhook do Evolution pra Recepia ANTES da clínica conectar.
    # Sem isso, mensagens recebidas nunca chegam no servidor.
    if settings.PUBLIC_WEBHOOK_URL:
        webhook_url = settings.PUBLIC_WEBHOOK_URL.rstrip("/") + "/api/webhook/evolution"
        ws.configurar_webhook(clinica.evolution_instance_name, webhook_url)

    qr = ws.obter_qrcode(clinica.evolution_instance_name)
    if not qr.get("success"):
        from fastapi import HTTPException
        raise HTTPException(502, f"Evolution não devolveu QR Code: {qr.get('error')}")

    audit.log(db, **ctx, acao=AcaoAudit.SETUP, recurso="whatsapp",
              recurso_id=clinica.evolution_instance_name,
              detalhes={"acao": "conectar", "webhook_configurado": bool(settings.PUBLIC_WEBHOOK_URL)})
    db.commit()

    return {
        "instance_name": clinica.evolution_instance_name,
        "qrcode_base64": qr.get("base64"),
        "pairing_code": qr.get("pairing_code"),
    }


@router.get("/status")
def status(
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    ws = WhatsAppService()
    resultado = ws.status_instancia(clinica.evolution_instance_name)
    # Atualiza flag no banco se mudou.
    # Race condition: /status concorrente com /desconectar ou com webhook do Evolution
    # podia gerar last-write-wins inconsistente. SELECT ... FOR UPDATE serializa as escritas.
    conectado = resultado.get("conectado", False)
    if clinica.evolution_conectado != conectado:
        clinica_lock = db.query(Clinica).filter(Clinica.id == clinica.id).with_for_update().first()
        if clinica_lock and clinica_lock.evolution_conectado != conectado:
            clinica_lock.evolution_conectado = conectado
            db.commit()
    return {
        "conectado": conectado,
        "estado": resultado.get("estado"),
    }


@router.post("/desconectar")
def desconectar(
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    ws = WhatsAppService()
    resultado = ws.desconectar(clinica.evolution_instance_name)
    # Lock pra evitar race com /status concorrente sobrescrevendo evolution_conectado.
    clinica_lock = db.query(Clinica).filter(Clinica.id == clinica.id).with_for_update().first()
    if clinica_lock:
        clinica_lock.evolution_conectado = False
    audit.log(db, **ctx, acao=AcaoAudit.SETUP, recurso="whatsapp",
              recurso_id=clinica.evolution_instance_name,
              detalhes={"acao": "desconectar"})
    db.commit()
    return resultado
