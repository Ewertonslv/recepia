"""Endpoints de foto avatar do paciente (Sprint 2).

Diferente das fotos de prontuário (sensível, biométrico, audit READ obrigatório),
o avatar é dado de UX — recepcionista reconhecer rápido. Logo:
- SEM feature gate (libera no Essencial)
- Audit só em CREATE/DELETE (não em READ — exagero pra avatar)
- 1 foto por paciente; upload novo sobrescreve atomicamente
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core import audit
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core.foto_storage import (
    MAX_UPLOAD_BYTES,
    FotoError,
    deletar_avatar,
    ler_avatar,
    salvar_avatar,
)
from database import get_db_dependency
from models import AcaoAudit, Clinica, Paciente

router = APIRouter(prefix="/api/pacientes/{paciente_id}/foto", tags=["paciente-foto"])


def _carregar_paciente(db: Session, paciente_id: str, clinica_id: str) -> Paciente:
    p = (
        db.query(Paciente)
        .filter(Paciente.id == paciente_id, Paciente.clinica_id == clinica_id)
        .first()
    )
    if not p:
        raise HTTPException(404, "Paciente não encontrado")
    return p


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_foto(
    paciente_id: str,
    arquivo: UploadFile = File(...),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    raw = await arquivo.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Arquivo maior que {MAX_UPLOAD_BYTES // (1024*1024)}MB")
    p = _carregar_paciente(db, paciente_id, clinica.id)
    try:
        meta = salvar_avatar(clinica.id, paciente_id, raw)
    except FotoError as e:
        raise HTTPException(422, str(e))
    p.foto_key = meta.key
    audit.log(
        db, **ctx, acao=AcaoAudit.CREATE, recurso="paciente_foto",
        recurso_id=paciente_id,
        detalhes={"tamanho_bytes": meta.tamanho_bytes},
    )
    db.commit()
    return {"foto_key": meta.key, "tamanho_bytes": meta.tamanho_bytes}


@router.get("")
def baixar_foto(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    # confirma ownership antes de tocar FS
    p = _carregar_paciente(db, paciente_id, clinica.id)
    if not p.foto_key:
        raise HTTPException(404, "Paciente sem foto")
    try:
        bytes_ = ler_avatar(clinica.id, paciente_id)
    except FotoError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=bytes_,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=600",
            "X-Content-Type-Options": "nosniff",
            "Vary": "Authorization",
        },
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remover_foto(
    paciente_id: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _carregar_paciente(db, paciente_id, clinica.id)
    if not p.foto_key:
        raise HTTPException(404, "Paciente sem foto")
    deletar_avatar(clinica.id, paciente_id)
    p.foto_key = None
    audit.log(
        db, **ctx, acao=AcaoAudit.DELETE, recurso="paciente_foto",
        recurso_id=paciente_id, detalhes={},
    )
    db.commit()
