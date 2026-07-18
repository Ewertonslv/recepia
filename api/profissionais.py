"""CRUD de Profissionais — multi-tenant, scoped por clinica_id.

Sprint 1.

Hardenings:
- F9: audit_context com IP/UA/usuario_id.
- Tier limit: HTTP 402 ao criar acima do limite do plano.
- RBAC: comissão só aparece pra role=admin (recepcionista não vê).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from core.deps import audit_context, clinica_atual, requer_clinica_ativa, usuario_atual
from core import audit
from core.planos import invalidar_cache_contagem, requer_limite, verificar_limite
from database import get_db_dependency
from models import AcaoAudit, Clinica, Profissional, Usuario

router = APIRouter(prefix="/api/profissionais", tags=["profissionais"])


# ============================================================================
# Schemas
# ============================================================================

class ProfissionalIn(BaseModel):
    nome: str = Field(..., min_length=2)
    email: Optional[EmailStr] = None
    especialidade: Optional[str] = Field(None, max_length=80)
    comissao_percentual: int = Field(0, ge=0, le=100)
    cor: str = Field("#E8B4B8", pattern=r"^#[0-9A-Fa-f]{6}$")
    ativo: bool = True


class ProfissionalPublicOut(BaseModel):
    """Visível pra qualquer usuário da clínica (sem comissão)."""
    id: str
    nome: str
    email: Optional[str]
    especialidade: Optional[str]
    cor: str
    ativo: bool
    criado_em: datetime

    class Config:
        from_attributes = True


class ProfissionalAdminOut(ProfissionalPublicOut):
    """Visível só pra admin (inclui comissão)."""
    comissao_percentual: int


def _serializa(prof: Profissional, eh_admin: bool):
    if eh_admin:
        return ProfissionalAdminOut.model_validate(prof)
    return ProfissionalPublicOut.model_validate(prof)


# ============================================================================
# Endpoints
# ============================================================================

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requer_limite("profissionais"))],
)
def criar(
    payload: ProfissionalIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    usuario: Usuario = Depends(usuario_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Cria profissional. Comissão só pode ser definida por admin."""
    if payload.comissao_percentual != 0 and usuario.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Só admin define comissão")

    prof = Profissional(
        clinica_id=clinica.id,
        nome=payload.nome,
        email=payload.email,
        especialidade=payload.especialidade,
        comissao_percentual=payload.comissao_percentual,
        cor=payload.cor,
        ativo=payload.ativo,
    )
    db.add(prof)
    db.flush()
    audit.log(db, **ctx, acao=AcaoAudit.CREATE, recurso="profissional",
              recurso_id=prof.id, detalhes={"nome": payload.nome})
    db.commit()
    invalidar_cache_contagem(clinica.id, "profissionais")
    db.refresh(prof)
    return _serializa(prof, usuario.role == "admin")


@router.get("")
def listar(
    incluir_inativos: bool = False,
    clinica: Clinica = Depends(clinica_atual),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(Profissional).filter(Profissional.clinica_id == clinica.id)
    if not incluir_inativos:
        q = q.filter(Profissional.ativo == True)
    profs = q.order_by(Profissional.nome.asc()).all()
    eh_admin = usuario.role == "admin"
    return [_serializa(p, eh_admin) for p in profs]


@router.get("/{prof_id}")
def obter(
    prof_id: str,
    clinica: Clinica = Depends(clinica_atual),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db_dependency),
):
    prof = (
        db.query(Profissional)
        .filter(Profissional.id == prof_id, Profissional.clinica_id == clinica.id)
        .first()
    )
    if not prof:
        raise HTTPException(404, "Profissional não encontrado")
    return _serializa(prof, usuario.role == "admin")


@router.put("/{prof_id}")
def atualizar(
    prof_id: str,
    payload: ProfissionalIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    usuario: Usuario = Depends(usuario_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    prof = (
        db.query(Profissional)
        .filter(Profissional.id == prof_id, Profissional.clinica_id == clinica.id)
        .first()
    )
    if not prof:
        raise HTTPException(404, "Profissional não encontrado")

    comissao_mudou = payload.comissao_percentual != prof.comissao_percentual
    if comissao_mudou and usuario.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Só admin altera comissão")

    # Reativar conta contra o teto do plano igual a criar — sem isso o PUT
    # era um bypass do limite (desativa, cria outro, reativa).
    if payload.ativo and not prof.ativo:
        verificar_limite(db, clinica, "profissionais")

    prof.nome = payload.nome
    prof.email = payload.email
    prof.especialidade = payload.especialidade
    prof.comissao_percentual = payload.comissao_percentual
    prof.cor = payload.cor
    prof.ativo = payload.ativo

    audit.log(db, **ctx, acao=AcaoAudit.UPDATE, recurso="profissional",
              recurso_id=prof.id, detalhes={"comissao_mudou": comissao_mudou})
    db.commit()
    invalidar_cache_contagem(clinica.id, "profissionais")
    db.refresh(prof)
    return _serializa(prof, usuario.role == "admin")


@router.delete("/{prof_id}", status_code=status.HTTP_204_NO_CONTENT)
def desativar(
    prof_id: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Soft delete — desativa mas mantém histórico de agendamentos/comissões.

    Pra hard delete, fazer manualmente via SQL (raro — usar com cuidado).
    """
    prof = (
        db.query(Profissional)
        .filter(Profissional.id == prof_id, Profissional.clinica_id == clinica.id)
        .first()
    )
    if not prof:
        raise HTTPException(404, "Profissional não encontrado")
    prof.ativo = False
    audit.log(db, **ctx, acao=AcaoAudit.UPDATE, recurso="profissional",
              recurso_id=prof.id, detalhes={"acao": "desativar"})
    db.commit()
    invalidar_cache_contagem(clinica.id, "profissionais")
