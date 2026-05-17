"""CRUD de Planos de Tratamento — multi-tenant, scoped por clinica_id.

Sprint 3 — Plano de tratamento:
- 1 paciente pode ter N planos (canal, ortodontia, clareamento, etc).
- Cada plano tem N sessões previstas (sessoes_previstas: int).
- Sessões realizadas = COUNT(Prontuario.plano_tratamento_id == plano.id).
- Status: ativo | concluido | cancelado.
- Sem campo financeiro (fora de escopo).

Hardenings LGPD:
- Feature gate FEATURE_PRONTUARIO em TODAS as rotas (plano clínico é dado de saúde).
- Cross-tenant: helper valida paciente/profissional pertencem à clínica (404, não 403).
- Listagem exige paciente_id (reduz superfície + cardinalidade de audit).
- Audit em todas as mutações (CREATE/UPDATE/concluir/cancelar).
- DELETE não existe — use POST /cancelar com motivo obrigatório.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from core import audit
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core.planos import FEATURE_PRONTUARIO, requer_feature
from database import get_db_dependency
from models import (
    AcaoAudit, Clinica, Paciente, PlanoTratamento, Profissional, Prontuario,
)

router = APIRouter(
    prefix="/api/planos-tratamento",
    tags=["planos-tratamento"],
    dependencies=[Depends(requer_feature(FEATURE_PRONTUARIO))],
)


STATUS_VALIDOS = {"ativo", "concluido", "cancelado"}
MAX_NOME = 120
MAX_DESC = 5_000
MAX_SESSOES = 200  # cap defensivo (200 sessões já é absurdo)


# ============================================================================
# Schemas
# ============================================================================

class PlanoIn(BaseModel):
    paciente_id: str
    nome: str = Field(..., min_length=1, max_length=MAX_NOME)
    descricao: Optional[str] = Field(None, max_length=MAX_DESC)
    sessoes_previstas: int = Field(1, ge=1, le=MAX_SESSOES)
    profissional_id: Optional[str] = None

    @field_validator("nome")
    @classmethod
    def _strip_nome(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nome obrigatório")
        return v


class PlanoUpdate(BaseModel):
    """Update permite mudar nome/descricao/sessoes/profissional/status. Paciente é imutável."""
    nome: Optional[str] = Field(None, min_length=1, max_length=MAX_NOME)
    descricao: Optional[str] = Field(None, max_length=MAX_DESC)
    sessoes_previstas: Optional[int] = Field(None, ge=1, le=MAX_SESSOES)
    profissional_id: Optional[str] = None
    status: Optional[str] = None

    @field_validator("nome")
    @classmethod
    def _strip_nome(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("nome não pode ser vazio")
        return v

    @field_validator("status")
    @classmethod
    def _valida_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in STATUS_VALIDOS:
            raise ValueError(f"status inválido (use: {sorted(STATUS_VALIDOS)})")
        return v


class CancelarPayload(BaseModel):
    motivo: str = Field(..., min_length=3, max_length=500)


class PlanoOut(BaseModel):
    id: str
    clinica_id: str
    paciente_id: str
    nome: str
    descricao: Optional[str]
    sessoes_previstas: int
    sessoes_realizadas: int  # derivado: count(prontuarios)
    status: str
    profissional_id: Optional[str]
    criado_em: datetime
    atualizado_em: datetime
    concluido_em: Optional[datetime]


class ProntuarioVinculadoOut(BaseModel):
    id: str
    criado_em: datetime
    profissional_id: Optional[str]
    proxima_acao: Optional[str]


class PlanoDetalheOut(PlanoOut):
    prontuarios: list[ProntuarioVinculadoOut]


# ============================================================================
# Helpers
# ============================================================================

def _validar_paciente(db: Session, clinica_id: str, paciente_id: str) -> None:
    pac = db.query(Paciente.id).filter(
        Paciente.id == paciente_id,
        Paciente.clinica_id == clinica_id,
        Paciente.deletado_em.is_(None),
    ).first()
    if not pac:
        raise HTTPException(404, "Paciente não encontrado")


def _validar_profissional(db: Session, clinica_id: str, profissional_id: Optional[str]) -> None:
    if not profissional_id:
        return
    ok = db.query(Profissional.id).filter(
        Profissional.id == profissional_id,
        Profissional.clinica_id == clinica_id,
    ).first()
    if not ok:
        raise HTTPException(404, "Profissional não encontrado")


def _buscar_ou_404(db: Session, plano_id: str, clinica_id: str) -> PlanoTratamento:
    p = (
        db.query(PlanoTratamento)
        .filter(PlanoTratamento.id == plano_id, PlanoTratamento.clinica_id == clinica_id)
        .first()
    )
    if not p:
        raise HTTPException(404, "Plano de tratamento não encontrado")
    return p


def _contar_sessoes(db: Session, plano_id: str) -> int:
    return db.query(func.count(Prontuario.id)).filter(
        Prontuario.plano_tratamento_id == plano_id,
    ).scalar() or 0


def _serializar(p: PlanoTratamento, sessoes_realizadas: int) -> dict:
    return {
        "id": p.id,
        "clinica_id": p.clinica_id,
        "paciente_id": p.paciente_id,
        "nome": p.nome,
        "descricao": p.descricao,
        "sessoes_previstas": p.sessoes_previstas,
        "sessoes_realizadas": sessoes_realizadas,
        "status": p.status,
        "profissional_id": p.profissional_id,
        "criado_em": p.criado_em,
        "atualizado_em": p.atualizado_em,
        "concluido_em": p.concluido_em,
    }


# ============================================================================
# Endpoints
# ============================================================================

@router.post("", response_model=PlanoOut, status_code=status.HTTP_201_CREATED)
def criar(
    payload: PlanoIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    _validar_paciente(db, clinica.id, payload.paciente_id)
    _validar_profissional(db, clinica.id, payload.profissional_id)

    p = PlanoTratamento(
        clinica_id=clinica.id,
        paciente_id=payload.paciente_id,
        nome=payload.nome,
        descricao=payload.descricao,
        sessoes_previstas=payload.sessoes_previstas,
        profissional_id=payload.profissional_id,
        status="ativo",
    )
    db.add(p)
    db.flush()
    audit.log(
        db, **ctx, acao=AcaoAudit.CREATE, recurso="plano_tratamento",
        recurso_id=p.id,
        detalhes={
            "paciente_id": payload.paciente_id,
            "nome": p.nome,
            "sessoes_previstas": p.sessoes_previstas,
        },
    )
    db.commit()
    db.refresh(p)
    return _serializar(p, 0)


@router.get("", response_model=list[PlanoOut])
def listar(
    paciente_id: str = Query(..., description="Obrigatório — listagem só por paciente"),
    status_filtro: Optional[str] = Query(None, alias="status"),
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    _validar_paciente(db, clinica.id, paciente_id)

    q = db.query(PlanoTratamento).filter(
        PlanoTratamento.clinica_id == clinica.id,
        PlanoTratamento.paciente_id == paciente_id,
    )
    if status_filtro:
        if status_filtro not in STATUS_VALIDOS:
            raise HTTPException(400, f"status inválido (use: {sorted(STATUS_VALIDOS)})")
        q = q.filter(PlanoTratamento.status == status_filtro)

    planos = q.order_by(PlanoTratamento.criado_em.desc()).all()
    if not planos:
        return []

    ids = [p.id for p in planos]
    # 1 query agregada pra contar sessões de TODOS os planos do paciente
    contagem = dict(
        db.query(Prontuario.plano_tratamento_id, func.count(Prontuario.id))
        .filter(Prontuario.plano_tratamento_id.in_(ids))
        .group_by(Prontuario.plano_tratamento_id)
        .all()
    )
    return [_serializar(p, contagem.get(p.id, 0)) for p in planos]


@router.get("/{plano_id}", response_model=PlanoDetalheOut)
def obter(
    plano_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    p = _buscar_ou_404(db, plano_id, clinica.id)
    prontuarios = (
        db.query(Prontuario)
        .filter(
            Prontuario.clinica_id == clinica.id,
            Prontuario.plano_tratamento_id == p.id,
        )
        .order_by(Prontuario.criado_em.desc())
        .all()
    )
    base = _serializar(p, len(prontuarios))
    base["prontuarios"] = [
        {
            "id": pr.id,
            "criado_em": pr.criado_em,
            "profissional_id": pr.profissional_id,
            "proxima_acao": pr.proxima_acao,
        }
        for pr in prontuarios
    ]
    return base


@router.put("/{plano_id}", response_model=PlanoOut)
def atualizar(
    plano_id: str,
    payload: PlanoUpdate,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _buscar_ou_404(db, plano_id, clinica.id)
    mudancas = payload.model_dump(exclude_unset=True)

    if "profissional_id" in mudancas:
        _validar_profissional(db, clinica.id, mudancas["profissional_id"])

    # Se mudou status pra concluido manualmente via PUT, seta concluido_em.
    if mudancas.get("status") == "concluido" and p.status != "concluido":
        p.concluido_em = datetime.utcnow()
    elif mudancas.get("status") == "ativo" and p.status != "ativo":
        # Reabriu? limpa concluido_em pra refletir realidade.
        p.concluido_em = None

    for k, v in mudancas.items():
        setattr(p, k, v)

    audit.log(
        db, **ctx, acao=AcaoAudit.UPDATE, recurso="plano_tratamento",
        recurso_id=p.id, detalhes={"campos": list(mudancas.keys())},
    )
    db.commit()
    db.refresh(p)
    return _serializar(p, _contar_sessoes(db, p.id))


@router.post("/{plano_id}/concluir", response_model=PlanoOut)
def concluir(
    plano_id: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _buscar_ou_404(db, plano_id, clinica.id)
    if p.status == "concluido":
        return _serializar(p, _contar_sessoes(db, p.id))
    if p.status == "cancelado":
        raise HTTPException(400, "Plano cancelado não pode ser concluído")

    p.status = "concluido"
    p.concluido_em = datetime.utcnow()
    audit.log(
        db, **ctx, acao=AcaoAudit.UPDATE, recurso="plano_tratamento",
        recurso_id=p.id, detalhes={"transicao": "concluir"},
    )
    db.commit()
    db.refresh(p)
    return _serializar(p, _contar_sessoes(db, p.id))


@router.post("/{plano_id}/cancelar", response_model=PlanoOut)
def cancelar(
    plano_id: str,
    payload: CancelarPayload,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    p = _buscar_ou_404(db, plano_id, clinica.id)
    if p.status == "cancelado":
        return _serializar(p, _contar_sessoes(db, p.id))

    snapshot_status = p.status
    p.status = "cancelado"
    audit.log(
        db, **ctx, acao=AcaoAudit.DELETE, recurso="plano_tratamento",
        recurso_id=p.id,
        detalhes={
            "transicao": "cancelar",
            "status_anterior": snapshot_status,
            "motivo": payload.motivo,
        },
    )
    db.commit()
    db.refresh(p)
    return _serializar(p, _contar_sessoes(db, p.id))
