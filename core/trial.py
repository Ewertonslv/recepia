"""Helpers de trial. Trial = 7 dias a partir do dia BR de cadastro.

IMPORTANTE — semântica de `trial_expira_em`:
    `trial_expira_em` é o ÚLTIMO DIA em que o cliente AINDA pode usar o sistema
    (inclusivo). Cadastro dia D → `trial_expira_em = D + (TRIAL_DIAS-1)`. Assim
    o cliente usa exatamente TRIAL_DIAS dias (D, D+1, ..., D+TRIAL_DIAS-1).
    `trial_expirado` bloqueia QUANDO hoje > trial_expira_em (i.e., `<` é correto).

    Não troque `<` por `<=` em `trial_expirado` SEM ajustar `data_expira_trial`
    pra `+TRIAL_DIAS` — caso contrário o trial fica off-by-one (8 dias ao invés de 7).
"""
from datetime import date, timedelta
from core.timezones import agora_utc, from_utc_to_br
from models import Clinica, Plano

TRIAL_DIAS = 7


def hoje_br() -> date:
    return from_utc_to_br(agora_utc()).date()


def data_expira_trial() -> date:
    """Último dia INCLUSIVO de uso do trial. Cadastro hoje → trial vale 7 dias (hoje + 6)."""
    return hoje_br() + timedelta(days=TRIAL_DIAS - 1)


def esta_em_trial(c: Clinica) -> bool:
    return c.plano == Plano.TRIAL and c.trial_expira_em is not None


def trial_expirado(c: Clinica) -> bool:
    if not esta_em_trial(c):
        return False
    # `<` é o operador correto: bloqueia só A PARTIR do dia seguinte ao expira_em.
    # Ver docstring do módulo pra explicação completa do off-by-one.
    return c.trial_expira_em < hoje_br()


def dias_restantes(c: Clinica) -> int:
    """Conta hoje como dia 1. Cadastrou hoje → "7 dias restantes". Último dia → "1 dia restante"."""
    if not esta_em_trial(c):
        return 0
    diff = (c.trial_expira_em - hoje_br()).days + 1
    return max(0, diff)
