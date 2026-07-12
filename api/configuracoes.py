"""G2: CRUD de templates de mensagem (Configuracao).

Cada clínica edita suas próprias mensagens via dashboard.
Audit logado em cada UPDATE.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core import audit
from database import get_db_dependency
from models import AcaoAudit, Clinica, Configuracao

router = APIRouter(prefix="/api/configuracoes", tags=["configuracoes"])


class ConfiguracaoOut(BaseModel):
    chave: str
    valor: str

    class Config:
        from_attributes = True


class ConfiguracaoIn(BaseModel):
    valor: str = Field(..., min_length=1, max_length=2000)


@router.get("", response_model=list[ConfiguracaoOut])
def listar(
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    return (
        db.query(Configuracao)
        .filter(Configuracao.clinica_id == clinica.id)
        .order_by(Configuracao.chave.asc())
        .all()
    )


@router.get("/{chave}", response_model=ConfiguracaoOut)
def obter(
    chave: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    config = (
        db.query(Configuracao)
        .filter(Configuracao.clinica_id == clinica.id, Configuracao.chave == chave)
        .first()
    )
    if not config:
        raise HTTPException(404, "Configuração não encontrada")
    return config


@router.put("/{chave}", response_model=ConfiguracaoOut)
def atualizar(
    chave: str,
    payload: ConfiguracaoIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Atualiza ou cria. Útil pra clínica customizar tom das mensagens."""
    config = (
        db.query(Configuracao)
        .filter(Configuracao.clinica_id == clinica.id, Configuracao.chave == chave)
        .first()
    )
    valor_antigo = config.valor if config else None
    if config:
        config.valor = payload.valor
    else:
        config = Configuracao(clinica_id=clinica.id, chave=chave, valor=payload.valor)
        db.add(config)

    audit.log(
        db, **ctx,
        acao=AcaoAudit.UPDATE, recurso="configuracao", recurso_id=chave,
        detalhes={"valor_antigo": valor_antigo, "valor_novo": payload.valor},
    )
    db.commit()
    db.refresh(config)
    return config
