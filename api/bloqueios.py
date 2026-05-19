"""CRUD de bloqueios de agenda (Sprint 9)."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from database import get_db_dependency
from models import AcaoAudit, BloqueioAgenda, Clinica, Profissional
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core import audit
from core.timezones import to_utc_naive

router = APIRouter(prefix="/api/bloqueios", tags=["bloqueios"])


class BloqueioIn(BaseModel):
    inicio: datetime
    fim: datetime
    motivo: str = "Bloqueio"
    profissional_id: str | None = None

    @model_validator(mode="after")
    def fim_apos_inicio(self):
        if self.fim <= self.inicio:
            raise ValueError("fim deve ser depois do inicio")
        return self


class BloqueioOut(BaseModel):
    id: str
    inicio: datetime
    fim: datetime
    motivo: str
    profissional_id: str | None
    criado_em: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[BloqueioOut])
def listar(
    data_inicio: str | None = None,
    data_fim: str | None = None,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(BloqueioAgenda).filter(BloqueioAgenda.clinica_id == clinica.id)
    if data_inicio:
        try:
            q = q.filter(BloqueioAgenda.fim >= datetime.fromisoformat(data_inicio))
        except ValueError:
            raise HTTPException(400, "data_inicio inválida (use YYYY-MM-DD)")
    if data_fim:
        try:
            fim_dt = datetime.fromisoformat(data_fim)
            q = q.filter(BloqueioAgenda.inicio <= fim_dt.replace(hour=23, minute=59, second=59))
        except ValueError:
            raise HTTPException(400, "data_fim inválida (use YYYY-MM-DD)")
    return q.order_by(BloqueioAgenda.inicio).all()


@router.post("", response_model=BloqueioOut, status_code=status.HTTP_201_CREATED)
def criar(
    payload: BloqueioIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    if payload.profissional_id:
        prof = db.query(Profissional).filter(
            Profissional.id == payload.profissional_id,
            Profissional.clinica_id == clinica.id,
        ).first()
        if not prof:
            raise HTTPException(404, "Profissional não encontrado")
    b = BloqueioAgenda(
        clinica_id=clinica.id,
        profissional_id=payload.profissional_id,
        inicio=to_utc_naive(payload.inicio),
        fim=to_utc_naive(payload.fim),
        motivo=payload.motivo.strip() or "Bloqueio",
    )
    db.add(b)
    audit.log(db, **ctx, acao=AcaoAudit.CREATE, recurso="bloqueio_agenda", recurso_id=b.id,
              detalhes={"inicio": payload.inicio.isoformat(), "fim": payload.fim.isoformat(), "motivo": b.motivo})
    db.commit()
    db.refresh(b)
    return b


@router.delete("/{bloqueio_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover(
    bloqueio_id: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    b = db.query(BloqueioAgenda).filter(
        BloqueioAgenda.id == bloqueio_id,
        BloqueioAgenda.clinica_id == clinica.id,
    ).first()
    if not b:
        raise HTTPException(404, "Bloqueio não encontrado")
    audit.log(db, **ctx, acao=AcaoAudit.DELETE, recurso="bloqueio_agenda", recurso_id=b.id,
              detalhes={"motivo": b.motivo})
    db.delete(b)
    db.commit()
