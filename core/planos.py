"""Definição de planos e limites por tier.

Sprint 1 — adiciona enforcement de limites:
- Cada plano define quantos profissionais/pacientes/etc são permitidos
- Dependency `requer_limite(recurso)` checa antes de criar
- HTTP 402 Payment Required quando ultrapassa (UX renderiza modal de upgrade)

Cache: contagem in-memory com TTL 60s (evita COUNT(*) em hot path).
Pra escalar pra multi-instance, trocar pra Redis.
"""
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import clinica_atual
from database import get_db_dependency
from models import Clinica, Paciente, Plano, Profissional


# ============================================================================
# Configuração dos planos
# ============================================================================

@dataclass
class LimitesPlano:
    max_profissionais: Optional[int]      # None = ilimitado
    max_pacientes_ativos: Optional[int]   # None = ilimitado
    max_unidades: Optional[int]            # None = ilimitado
    features: set[str]
    preco_mensal: int                      # em centavos (R$ 197 = 19700)
    nome_amigavel: str
    descricao: str


# Features que cada plano libera (gate por feature)
FEATURE_PRONTUARIO = "prontuario"
FEATURE_FINANCEIRO = "financeiro"
FEATURE_PACOTES = "pacotes"
FEATURE_RELATORIOS = "relatorios"
FEATURE_MULTI_UNIDADE = "multi_unidade"
FEATURE_API = "api"
FEATURE_WHITELABEL = "whitelabel"


LIMITES: dict[str, LimitesPlano] = {
    Plano.ESSENCIAL: LimitesPlano(
        max_profissionais=2,
        max_pacientes_ativos=100,
        max_unidades=1,
        features={"agenda", "whatsapp_ia", "reagendamento"},
        preco_mensal=9700,
        nome_amigavel="Essencial",
        descricao="Pra começar: agenda + WhatsApp IA + reagendamento autônomo",
    ),
    Plano.PRO: LimitesPlano(
        max_profissionais=5,
        max_pacientes_ativos=None,
        max_unidades=1,
        features={
            "agenda", "whatsapp_ia", "reagendamento",
            FEATURE_PRONTUARIO, FEATURE_FINANCEIRO, FEATURE_PACOTES, FEATURE_RELATORIOS,
        },
        preco_mensal=19700,
        nome_amigavel="Pro",
        descricao="Mais usado: tudo do Essencial + prontuário + financeiro + pacotes",
    ),
    Plano.ENTERPRISE: LimitesPlano(
        max_profissionais=None,
        max_pacientes_ativos=None,
        max_unidades=None,
        features={
            "agenda", "whatsapp_ia", "reagendamento",
            FEATURE_PRONTUARIO, FEATURE_FINANCEIRO, FEATURE_PACOTES, FEATURE_RELATORIOS,
            FEATURE_MULTI_UNIDADE, FEATURE_API, FEATURE_WHITELABEL,
        },
        preco_mensal=49700,
        nome_amigavel="Enterprise",
        descricao="Pra redes: tudo + multi-unidade + API + white-label",
    ),
    Plano.TRIAL: LimitesPlano(  # trial = acesso Pro por N dias
        max_profissionais=5,
        max_pacientes_ativos=None,
        max_unidades=1,
        features={
            "agenda", "whatsapp_ia", "reagendamento",
            FEATURE_PRONTUARIO, FEATURE_FINANCEIRO, FEATURE_PACOTES, FEATURE_RELATORIOS,
        },
        preco_mensal=0,
        nome_amigavel="Trial",
        descricao="Período de avaliação — acesso completo",
    ),
}


def limites_de(plano: str) -> LimitesPlano:
    return LIMITES.get(plano) or LIMITES[Plano.ESSENCIAL]


# ============================================================================
# Cache in-memory de contagem (chave: f"{clinica_id}:{recurso}", TTL 60s)
# ============================================================================

_cache: dict[str, tuple[int, float]] = {}
_TTL_SEGUNDOS = 60


def _contar_recurso(db: Session, clinica_id: str, recurso: str) -> int:
    """Conta uso atual de um recurso na clínica, com cache."""
    chave = f"{clinica_id}:{recurso}"
    agora = time.time()
    cached = _cache.get(chave)
    if cached and (agora - cached[1]) < _TTL_SEGUNDOS:
        return cached[0]

    if recurso == "profissionais":
        total = db.query(func.count(Profissional.id)).filter(
            Profissional.clinica_id == clinica_id,
            Profissional.ativo == True,
        ).scalar() or 0
    elif recurso == "pacientes_ativos":
        total = db.query(func.count(Paciente.id)).filter(
            Paciente.clinica_id == clinica_id,
            Paciente.deletado_em.is_(None),
        ).scalar() or 0
    else:
        total = 0

    _cache[chave] = (total, agora)
    return total


def invalidar_cache_contagem(clinica_id: str, recurso: str) -> None:
    """Chamar quando criar/deletar um recurso pra forçar recálculo."""
    _cache.pop(f"{clinica_id}:{recurso}", None)


# ============================================================================
# Dependencies pra usar em endpoints
# ============================================================================

class LimiteExcedido(HTTPException):
    """HTTP 402 com payload estruturado pro UX renderizar modal de upgrade."""
    def __init__(self, recurso: str, limite: int, plano_atual: str, sugerido: str):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "erro": "limite_excedido",
                "recurso": recurso,
                "limite": limite,
                "plano_atual": plano_atual,
                "upgrade_para": sugerido,
                "mensagem": f"Seu plano {limites_de(plano_atual).nome_amigavel} permite até "
                            f"{limite} {recurso}. Faça upgrade pra continuar crescendo 💛",
            },
        )


class FeatureBloqueada(HTTPException):
    def __init__(self, feature: str, plano_atual: str, sugerido: str):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "erro": "feature_bloqueada",
                "feature": feature,
                "plano_atual": plano_atual,
                "upgrade_para": sugerido,
                "mensagem": f"A funcionalidade '{feature}' faz parte do plano "
                            f"{limites_de(sugerido).nome_amigavel}. Bora fazer upgrade?",
            },
        )


def requer_limite(recurso: str):
    """Dependency factory: bloqueia criação se limite atingido.

    Uso:
        @router.post("", dependencies=[Depends(requer_limite("profissionais"))])
    """
    def _checker(
        clinica: Clinica = Depends(clinica_atual),
        db: Session = Depends(get_db_dependency),
    ) -> None:
        plano = clinica.plano
        limites = limites_de(plano)
        max_attr = f"max_{recurso}" if recurso != "pacientes_ativos" else "max_pacientes_ativos"
        limite = getattr(limites, max_attr, None)
        if limite is None:
            return  # ilimitado

        usado = _contar_recurso(db, clinica.id, recurso)
        if usado >= limite:
            sugerido = _proximo_plano(plano)
            raise LimiteExcedido(recurso, limite, plano, sugerido)

    return _checker


def requer_feature(feature: str):
    """Dependency factory: bloqueia se feature não está no plano."""
    def _checker(clinica: Clinica = Depends(clinica_atual)) -> None:
        limites = limites_de(clinica.plano)
        if feature not in limites.features:
            sugerido = _menor_plano_com_feature(feature)
            raise FeatureBloqueada(feature, clinica.plano, sugerido)
    return _checker


def _proximo_plano(plano: str) -> str:
    sequencia = [Plano.ESSENCIAL, Plano.PRO, Plano.ENTERPRISE]
    try:
        i = sequencia.index(plano)
        return sequencia[min(i + 1, len(sequencia) - 1)]
    except ValueError:
        return Plano.PRO


def _menor_plano_com_feature(feature: str) -> str:
    for nivel in [Plano.ESSENCIAL, Plano.PRO, Plano.ENTERPRISE]:
        if feature in limites_de(nivel).features:
            return nivel
    return Plano.ENTERPRISE


# ============================================================================
# Public: snapshot de uso (pro endpoint /api/plano/me/uso)
# ============================================================================

def snapshot_uso(db: Session, clinica: Clinica) -> dict:
    limites = limites_de(clinica.plano)
    return {
        "plano": clinica.plano,
        "plano_nome": limites.nome_amigavel,
        "preco_mensal": limites.preco_mensal,
        "uso": {
            "profissionais": {
                "atual": _contar_recurso(db, clinica.id, "profissionais"),
                "limite": limites.max_profissionais,
            },
            "pacientes_ativos": {
                "atual": _contar_recurso(db, clinica.id, "pacientes_ativos"),
                "limite": limites.max_pacientes_ativos,
            },
        },
        "features": sorted(limites.features),
        "upgrade_disponivel": _proximo_plano(clinica.plano) != clinica.plano,
    }
