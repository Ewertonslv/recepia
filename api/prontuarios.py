"""CRUD de Prontuários — multi-tenant, scoped por clinica_id.

Fotos de prontuário são gerenciadas em `api/fotos.py` (upload/download/delete);
aqui o campo `fotos` expõe apenas a metadata já persistida.

Hardenings LGPD Art. 11/37/46 (dado sensível de saúde):
- Feature gate FEATURE_PRONTUARIO em TODAS as rotas (router-level).
- Audit READ obrigatório em GET /{id} (1 entry/registro) + GET list (1 entry agregado).
- Cross-tenant: helper único valida FKs pertencem à mesma clinica_id (404, não 403).
- DELETE só admin + motivo (registrado em detalhes do audit pra rastreabilidade).
- Listagem exige paciente_id (reduz superfície de vazamento + cardinalidade de audit).
- Free-text com cap (anotações 10k, proxima_acao 200, alergias 50 itens x 80 chars).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from core.deps import audit_context, clinica_atual, requer_clinica_ativa, usuario_atual
from core.planos import FEATURE_PRONTUARIO, requer_feature
from core import audit
from database import get_db_dependency
from models import (
    AcaoAudit, Agendamento, Clinica, Paciente, PlanoTratamento,
    Profissional, Prontuario, Usuario,
)

router = APIRouter(
    prefix="/api/prontuarios",
    tags=["prontuarios"],
    dependencies=[Depends(requer_feature(FEATURE_PRONTUARIO))],
)

MAX_TEXT = 10_000
MAX_ALERGIAS = 50
MAX_ALERGIA_ITEM = 80


# ============================================================================
# Schemas
# ============================================================================

class ProntuarioIn(BaseModel):
    paciente_id: str
    profissional_id: Optional[str] = None
    agendamento_id: Optional[str] = None
    plano_tratamento_id: Optional[str] = None
    anotacoes: Optional[str] = Field(None, max_length=MAX_TEXT)
    procedimentos_realizados: Optional[str] = Field(None, max_length=MAX_TEXT)
    alergias: list[str] = Field(default_factory=list, max_length=MAX_ALERGIAS)
    proxima_acao: Optional[str] = Field(None, max_length=200)

    @field_validator("alergias")
    @classmethod
    def _valida_alergias(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            if not isinstance(s, str):
                raise ValueError("alergia deve ser string")
            s = s.strip()
            if len(s) > MAX_ALERGIA_ITEM:
                raise ValueError(f"alergia deve ter até {MAX_ALERGIA_ITEM} chars")
            if s:
                out.append(s)
        return out


class ProntuarioUpdate(BaseModel):
    """Update não permite mudar paciente_id (cria novo registro pra isso)."""
    profissional_id: Optional[str] = None
    agendamento_id: Optional[str] = None
    plano_tratamento_id: Optional[str] = None
    anotacoes: Optional[str] = Field(None, max_length=MAX_TEXT)
    procedimentos_realizados: Optional[str] = Field(None, max_length=MAX_TEXT)
    alergias: Optional[list[str]] = Field(None, max_length=MAX_ALERGIAS)
    proxima_acao: Optional[str] = Field(None, max_length=200)


class ProntuarioOut(BaseModel):
    id: str
    clinica_id: str
    paciente_id: str
    profissional_id: Optional[str]
    agendamento_id: Optional[str]
    plano_tratamento_id: Optional[str]
    anotacoes: Optional[str]
    procedimentos_realizados: Optional[str]
    alergias: list[str]
    proxima_acao: Optional[str]
    fotos: list[dict]  # metadata das fotos (key/sha256/mime/tipo/criado_em); arquivos servidos por api/fotos.py
    criado_em: datetime
    atualizado_em: datetime

    class Config:
        from_attributes = True


class DeletePayload(BaseModel):
    motivo: str = Field(..., min_length=5, max_length=500)


# ============================================================================
# Helpers
# ============================================================================

def _validar_fks(
    db: Session,
    clinica_id: str,
    paciente_id: str,
    profissional_id: Optional[str],
    agendamento_id: Optional[str],
    plano_tratamento_id: Optional[str] = None,
) -> None:
    """Garante que TODA FK pertence à mesma clínica. 404 (não 403) pra não vazar existência."""
    pac = db.query(Paciente.id).filter(
        Paciente.id == paciente_id,
        Paciente.clinica_id == clinica_id,
        Paciente.deletado_em.is_(None),
    ).first()
    if not pac:
        raise HTTPException(404, "Paciente não encontrado")

    if profissional_id:
        ok = db.query(Profissional.id).filter(
            Profissional.id == profissional_id,
            Profissional.clinica_id == clinica_id,
        ).first()
        if not ok:
            raise HTTPException(404, "Profissional não encontrado")

    if agendamento_id:
        ok = db.query(Agendamento.id).filter(
            Agendamento.id == agendamento_id,
            Agendamento.clinica_id == clinica_id,
        ).first()
        if not ok:
            raise HTTPException(404, "Agendamento não encontrado")

    if plano_tratamento_id:
        # Plano precisa ser da MESMA clínica E do MESMO paciente do prontuário.
        ok = db.query(PlanoTratamento.id).filter(
            PlanoTratamento.id == plano_tratamento_id,
            PlanoTratamento.clinica_id == clinica_id,
            PlanoTratamento.paciente_id == paciente_id,
        ).first()
        if not ok:
            raise HTTPException(404, "Plano de tratamento não encontrado")


def _buscar_ou_404(db: Session, prontuario_id: str, clinica_id: str) -> Prontuario:
    p = (
        db.query(Prontuario)
        .filter(Prontuario.id == prontuario_id, Prontuario.clinica_id == clinica_id)
        .first()
    )
    if not p:
        raise HTTPException(404, "Prontuário não encontrado")
    return p


# ============================================================================
# Endpoints
# ============================================================================

@router.post("", response_model=ProntuarioOut, status_code=status.HTTP_201_CREATED)
def criar(
    payload: ProntuarioIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    _validar_fks(
        db, clinica.id, payload.paciente_id,
        payload.profissional_id, payload.agendamento_id,
        payload.plano_tratamento_id,
    )
    p = Prontuario(
        clinica_id=clinica.id,
        paciente_id=payload.paciente_id,
        profissional_id=payload.profissional_id,
        agendamento_id=payload.agendamento_id,
        plano_tratamento_id=payload.plano_tratamento_id,
        anotacoes=payload.anotacoes,
        procedimentos_realizados=payload.procedimentos_realizados,
        alergias=payload.alergias,
        proxima_acao=payload.proxima_acao,
        fotos=[],
    )
    db.add(p)
    db.flush()
    audit.log(
        db, **ctx, acao=AcaoAudit.CREATE, recurso="prontuario",
        recurso_id=p.id, detalhes={"paciente_id": payload.paciente_id},
    )
    db.commit()
    db.refresh(p)
    return p


@router.get("", response_model=list[ProntuarioOut])
def listar(
    paciente_id: str = Query(..., description="Obrigatório — listagem só por paciente"),
    profissional_id: Optional[str] = None,
    desde: Optional[datetime] = None,
    ate: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    # Sprint 6: listagem só com paciente NÃO soft-deletado (consistência com criar/atualizar)
    pac = db.query(Paciente.id).filter(
        Paciente.id == paciente_id,
        Paciente.clinica_id == clinica.id,
        Paciente.deletado_em.is_(None),
    ).first()
    if not pac:
        raise HTTPException(404, "Paciente não encontrado")

    q = db.query(Prontuario).filter(
        Prontuario.clinica_id == clinica.id,
        Prontuario.paciente_id == paciente_id,
    )
    if profissional_id:
        q = q.filter(Prontuario.profissional_id == profissional_id)
    if desde:
        q = q.filter(Prontuario.criado_em >= desde)
    if ate:
        q = q.filter(Prontuario.criado_em <= ate)
    rows = q.order_by(Prontuario.criado_em.desc()).offset(offset).limit(limit).all()

    audit.log(
        db, **ctx, acao=AcaoAudit.READ, recurso="prontuario",
        recurso_id=None,
        detalhes={
            "paciente_id": paciente_id,
            "count": len(rows),
            "offset": offset,
            "limit": limit,
        },
    )
    db.commit()
    return rows


@router.get("/{prontuario_id}", response_model=ProntuarioOut)
def obter(
    prontuario_id: str,
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _buscar_ou_404(db, prontuario_id, clinica.id)
    audit.log(
        db, **ctx, acao=AcaoAudit.READ, recurso="prontuario",
        recurso_id=p.id, detalhes={"paciente_id": p.paciente_id},
    )
    db.commit()
    return p


@router.put("/{prontuario_id}", response_model=ProntuarioOut)
def atualizar(
    prontuario_id: str,
    payload: ProntuarioUpdate,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _buscar_ou_404(db, prontuario_id, clinica.id)
    mudancas = payload.model_dump(exclude_unset=True)
    _validar_fks(
        db, clinica.id, p.paciente_id,  # paciente fixo
        mudancas["profissional_id"] if "profissional_id" in mudancas else p.profissional_id,
        mudancas["agendamento_id"] if "agendamento_id" in mudancas else p.agendamento_id,
        mudancas["plano_tratamento_id"] if "plano_tratamento_id" in mudancas else p.plano_tratamento_id,
    )
    for k, v in mudancas.items():
        setattr(p, k, v)
    audit.log(
        db, **ctx, acao=AcaoAudit.UPDATE, recurso="prontuario",
        recurso_id=p.id, detalhes={"campos": list(mudancas.keys())},
    )
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{prontuario_id}", status_code=status.HTTP_204_NO_CONTENT)
def deletar(
    prontuario_id: str,
    payload: DeletePayload,
    clinica: Clinica = Depends(requer_clinica_ativa),
    usuario: Usuario = Depends(usuario_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Hard delete restrito a admin + motivo obrigatório (LGPD Art. 16 II / 18)."""
    if usuario.role != "admin":
        raise HTTPException(403, "Só admin pode deletar prontuário")
    p = _buscar_ou_404(db, prontuario_id, clinica.id)
    snapshot = {
        "paciente_id": p.paciente_id,
        "criado_em": p.criado_em.isoformat(),
        "motivo": payload.motivo,
        "tinha_fotos": bool(p.fotos),
    }
    audit.log(
        db, **ctx, acao=AcaoAudit.DELETE, recurso="prontuario",
        recurso_id=p.id, detalhes=snapshot,
    )
    db.delete(p)
    db.commit()
