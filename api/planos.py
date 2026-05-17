"""Endpoint pra clínica ver/gerenciar seu plano atual + uso vs limites.

Sprint 1 — só leitura. Upgrade real (cobrança via Asaas) vem em sprint futuro.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.deps import clinica_atual
from core.planos import LIMITES, snapshot_uso
from database import get_db_dependency
from models import Clinica

router = APIRouter(prefix="/api/plano", tags=["plano"])


@router.get("/me/uso")
def meu_uso(
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    """Retorna plano atual + uso vs limite (pra dashboard de plano + modais de upgrade)."""
    return snapshot_uso(db, clinica)


@router.get("/comparativo")
def comparativo_planos():
    """Lista todos os planos disponíveis pra tela de comparação/upgrade."""
    return [
        {
            "id": plano_id,
            "nome": l.nome_amigavel,
            "preco_mensal_centavos": l.preco_mensal,
            "preco_mensal_reais": l.preco_mensal / 100,
            "descricao": l.descricao,
            "limites": {
                "profissionais": l.max_profissionais,
                "pacientes_ativos": l.max_pacientes_ativos,
                "unidades": l.max_unidades,
            },
            "features": sorted(l.features),
        }
        for plano_id, l in LIMITES.items()
        if plano_id != "trial"  # trial não aparece pra usuário escolher
    ]
