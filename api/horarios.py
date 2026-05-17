"""G6: CRUD de HorarioFuncionamento (base pra G1 — reagendamento real).

7 entradas possíveis por clínica (uma por dia da semana).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core import audit
from database import get_db_dependency
from models import AcaoAudit, Clinica, HorarioFuncionamento


router = APIRouter(prefix="/api/horarios", tags=["horarios"])


class HorarioIn(BaseModel):
    hora_inicio: str = Field(..., pattern=r"^\d{2}:\d{2}$")  # "09:00"
    hora_fim: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    intervalo_slot_min: int = Field(60, ge=15, le=240)
    ativo: bool = True

    @field_validator("hora_inicio", "hora_fim")
    @classmethod
    def valida_hora(cls, v: str) -> str:
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("hora inválida")
        return v


class HorarioOut(BaseModel):
    dia_semana: int  # 0=segunda, 6=domingo
    hora_inicio: str
    hora_fim: str
    intervalo_slot_min: int
    ativo: bool

    class Config:
        from_attributes = True


@router.get("", response_model=list[HorarioOut])
def listar(
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    return (
        db.query(HorarioFuncionamento)
        .filter(HorarioFuncionamento.clinica_id == clinica.id)
        .order_by(HorarioFuncionamento.dia_semana.asc())
        .all()
    )


@router.put("/{dia_semana}", response_model=HorarioOut)
def atualizar(
    dia_semana: int,
    payload: HorarioIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    if not (0 <= dia_semana <= 6):
        raise HTTPException(400, "dia_semana deve estar entre 0 (seg) e 6 (dom)")

    horario = (
        db.query(HorarioFuncionamento)
        .filter(
            HorarioFuncionamento.clinica_id == clinica.id,
            HorarioFuncionamento.dia_semana == dia_semana,
        )
        .first()
    )
    if horario:
        horario.hora_inicio = payload.hora_inicio
        horario.hora_fim = payload.hora_fim
        horario.intervalo_slot_min = payload.intervalo_slot_min
        horario.ativo = payload.ativo
    else:
        horario = HorarioFuncionamento(
            clinica_id=clinica.id,
            dia_semana=dia_semana,
            hora_inicio=payload.hora_inicio,
            hora_fim=payload.hora_fim,
            intervalo_slot_min=payload.intervalo_slot_min,
            ativo=payload.ativo,
        )
        db.add(horario)

    audit.log(
        db, **ctx,
        acao=AcaoAudit.UPDATE, recurso="horario_funcionamento", recurso_id=str(dia_semana),
        detalhes=payload.model_dump(),
    )
    db.commit()
    db.refresh(horario)
    return horario
