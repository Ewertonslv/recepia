"""Endpoints de upload/download/delete de fotos de prontuário.

Sprint 1 D4 — LGPD biométrico (Art. 11 + Art. 7º §3º).

Hardenings:
- Feature gate FEATURE_PRONTUARIO via dependency.
- Cross-tenant: prontuário sempre carregado com filter clinica_id do JWT (404 se não pertence).
- SELECT FOR UPDATE no prontuário antes de mutar JSON `fotos` (evita perda em uploads concorrentes).
- Cap 50 fotos/prontuário (anti DoS fill-disk).
- Audit READ/CREATE/DELETE individual por foto.
- Headers privados (Cache-Control private, X-Content-Type-Options, CSP) ao servir.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from core import audit
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core.foto_storage import (
    MAX_FOTOS_POR_PRONTUARIO,
    MAX_UPLOAD_BYTES,
    FotoError,
    deletar as fs_deletar,
    ler as fs_ler,
    processar_upload,
    salvar as fs_salvar,
)
from core.planos import FEATURE_PRONTUARIO, requer_feature
from database import get_db_dependency
from models import AcaoAudit, Clinica, Prontuario

router = APIRouter(
    prefix="/api/prontuarios/{prontuario_id}/fotos",
    tags=["prontuarios-fotos"],
    dependencies=[Depends(requer_feature(FEATURE_PRONTUARIO))],
)


class FotoOut(BaseModel):
    key: str
    sha256: str
    mime: str
    tamanho_bytes: int
    descricao: Optional[str] = None
    tipo: Optional[str] = None  # antes | depois | evolucao
    criado_em: str


class UploadResposta(BaseModel):
    foto: FotoOut
    total_fotos: int


TIPOS_VALIDOS = {"antes", "depois", "evolucao", "raio_x", "exame", "documento"}


def _carregar_prontuario_lock(db: Session, prontuario_id: str, clinica_id: str) -> Prontuario:
    """Carrega prontuário com SELECT FOR UPDATE — evita race em uploads concorrentes."""
    p = (
        db.query(Prontuario)
        .filter(Prontuario.id == prontuario_id, Prontuario.clinica_id == clinica_id)
        .with_for_update()
        .first()
    )
    if not p:
        raise HTTPException(404, "Prontuário não encontrado")
    return p


@router.post("", response_model=UploadResposta, status_code=status.HTTP_201_CREATED)
async def upload(
    prontuario_id: str,
    arquivo: UploadFile = File(...),
    descricao: Optional[str] = Form(None, max_length=200),
    tipo: Optional[str] = Form(None),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    if tipo is not None and tipo not in TIPOS_VALIDOS:
        raise HTTPException(422, f"tipo deve ser um de {sorted(TIPOS_VALIDOS)}")

    # leitura early-abort: se passar do max, rejeitar
    raw = await arquivo.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Arquivo maior que {MAX_UPLOAD_BYTES // (1024*1024)}MB")

    # carrega prontuário travado
    p = _carregar_prontuario_lock(db, prontuario_id, clinica.id)
    fotos = list(p.fotos or [])
    if len(fotos) >= MAX_FOTOS_POR_PRONTUARIO:
        raise HTTPException(
            422,
            f"Limite de {MAX_FOTOS_POR_PRONTUARIO} fotos por prontuário atingido",
        )

    try:
        webp_bytes, meta = processar_upload(raw)
    except FotoError as e:
        raise HTTPException(422, str(e))

    fs_salvar(clinica.id, prontuario_id, webp_bytes, meta)
    entry = {
        "key": meta.key,
        "sha256": meta.sha256,
        "mime": meta.mime,
        "tamanho_bytes": meta.tamanho_bytes,
        "descricao": (descricao or "").strip() or None,
        "tipo": tipo,
        "criado_em": datetime.utcnow().isoformat(),
    }
    fotos.append(entry)
    p.fotos = fotos
    flag_modified(p, "fotos")
    audit.log(
        db, **ctx, acao=AcaoAudit.CREATE, recurso="foto_prontuario",
        recurso_id=meta.key,
        detalhes={
            "prontuario_id": prontuario_id,
            "tamanho_bytes": meta.tamanho_bytes,
            "tipo": tipo,
        },
    )
    db.commit()
    return UploadResposta(foto=FotoOut(**entry), total_fotos=len(fotos))


@router.get("/{key}")
def baixar(
    prontuario_id: str,
    key: str,
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    # confirma ownership ANTES de tocar FS
    p = (
        db.query(Prontuario)
        .filter(Prontuario.id == prontuario_id, Prontuario.clinica_id == clinica.id)
        .first()
    )
    if not p:
        raise HTTPException(404, "Prontuário não encontrado")
    entry = next((f for f in (p.fotos or []) if f.get("key") == key), None)
    if not entry:
        raise HTTPException(404, "Foto não encontrada")

    try:
        bytes_ = fs_ler(clinica.id, prontuario_id, key)
    except FotoError as e:
        raise HTTPException(404, str(e))

    audit.log(
        db, **ctx, acao=AcaoAudit.READ, recurso="foto_prontuario",
        recurso_id=key,
        detalhes={"prontuario_id": prontuario_id, "bytes": len(bytes_)},
    )
    db.commit()
    return Response(
        content=bytes_,
        media_type=entry.get("mime", "image/webp"),
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self' data:",
            "Vary": "Authorization",
            "Content-Disposition": f'inline; filename="{key}"',
        },
    )


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
def remover(
    prontuario_id: str,
    key: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _carregar_prontuario_lock(db, prontuario_id, clinica.id)
    fotos = list(p.fotos or [])
    idx = next((i for i, f in enumerate(fotos) if f.get("key") == key), -1)
    if idx < 0:
        raise HTTPException(404, "Foto não encontrada")
    removida = fotos.pop(idx)
    p.fotos = fotos
    flag_modified(p, "fotos")
    fs_deletar(clinica.id, prontuario_id, key)
    audit.log(
        db, **ctx, acao=AcaoAudit.DELETE, recurso="foto_prontuario",
        recurso_id=key,
        detalhes={
            "prontuario_id": prontuario_id,
            "tamanho_bytes": removida.get("tamanho_bytes"),
        },
    )
    db.commit()
