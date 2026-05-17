"""Conversão de timezone (B1/B11).

Política: TUDO no banco fica em UTC naive.
- Entrada (API): se naive, assume BR; converte pra UTC antes de salvar.
- Saída (templates, relatórios): converte UTC -> BR pra display.
- Scheduler: usa datetime.utcnow() consistente com o que está no banco.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ_BR = ZoneInfo("America/Sao_Paulo")


def to_utc_naive(dt: datetime) -> datetime:
    """Datetime de entrada -> naive UTC pra salvar no banco.

    Se vier sem tzinfo (naive), assume horário BR.
    Se vier com tzinfo, converte pra UTC e remove o tzinfo (compat com modelos atuais).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_BR)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def from_utc_to_br(dt: datetime) -> datetime:
    """Datetime naive UTC do banco -> tz-aware BR pra display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_BR)


def agora_utc() -> datetime:
    """Now em UTC naive (mesmo formato salvo no banco)."""
    return datetime.utcnow()


def agora_br() -> datetime:
    """Now em BR tz-aware (pra display)."""
    return datetime.now(TZ_BR)
