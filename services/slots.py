"""G1: cálculo de slots livres pra sugerir em reagendamento.

Considera:
- HorarioFuncionamento da clínica (por dia da semana, com slot_min)
- Agendamentos existentes (PENDENTE/CONFIRMADO contam como ocupados)
- Slot mínimo de antecedência (não sugere "daqui 30min")
"""
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from core.timezones import TZ_BR, agora_br, agora_utc
from models import Agendamento, BloqueioAgenda, Clinica, HorarioFuncionamento, Status


DIA_SEMANA_BR = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
DIA_SEMANA_LONGO = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

# Antecedência mínima pra um slot ser "sugerível" — paciente precisa de tempo
ANTECEDENCIA_MINIMA_HORAS = 4


def sugerir_slots(
    db: Session,
    clinica: Clinica,
    n_slots: int = 3,
    dias_a_frente: int = 7,
    excluir_agendamento_id: Optional[str] = None,
) -> list[dict]:
    """Retorna até n_slots livres nos próximos dias_a_frente dias.

    Cada slot:
    {
        "numero": 1,
        "data_hora_utc": datetime,           # pra armazenar no banco
        "data_hora_br": datetime,            # pra display
        "label": "amanhã (qui) 14:00",       # texto amigável
    }
    """
    # Carrega horários ativos uma única vez
    horarios = (
        db.query(HorarioFuncionamento)
        .filter(
            HorarioFuncionamento.clinica_id == clinica.id,
            HorarioFuncionamento.ativo == True,
        )
        .all()
    )
    horarios_by_dia = {h.dia_semana: h for h in horarios}

    if not horarios_by_dia:
        return []

    agora = agora_br()
    minimo_disponivel = agora + timedelta(hours=ANTECEDENCIA_MINIMA_HORAS)

    # Pré-busca agendamentos do range pra evitar N queries no loop.
    # Ocupação é por INTERVALO (data_hora + duracao), não por igualdade exata —
    # um agendamento fora da grade (14:30) ou mais longo que o slot também
    # bloqueia os slots que ele atravessa. O range começa 1 dia antes pra
    # capturar agendamentos longos que começaram antes da janela.
    inicio_range_utc = agora_utc()
    fim_range_utc = inicio_range_utc + timedelta(days=dias_a_frente + 1)
    q = db.query(Agendamento.data_hora, Agendamento.duracao_minutos).filter(
        Agendamento.clinica_id == clinica.id,
        Agendamento.status.in_([Status.PENDENTE, Status.CONFIRMADO]),
        Agendamento.data_hora >= inicio_range_utc - timedelta(days=1),
        Agendamento.data_hora <= fim_range_utc,
    )
    if excluir_agendamento_id:
        q = q.filter(Agendamento.id != excluir_agendamento_id)
    ocupados_utc = [
        (dh, dh + timedelta(minutes=dur or 30)) for dh, dur in q.all()
    ]

    # Sprint 9: períodos bloqueados (almoço, férias, feriado). Conservador —
    # qualquer bloqueio que cruze a janela invalida o slot, mesmo se for de
    # um único profissional (clínicas pequenas: melhor não oferecer do que furar).
    bloqueios = db.query(BloqueioAgenda.inicio, BloqueioAgenda.fim).filter(
        BloqueioAgenda.clinica_id == clinica.id,
        BloqueioAgenda.fim > inicio_range_utc,
        BloqueioAgenda.inicio < fim_range_utc,
    ).all()

    slots: list[dict] = []
    hoje_br = agora.date()

    for d in range(0, dias_a_frente + 1):
        data_br = hoje_br + timedelta(days=d)
        dia_semana = data_br.weekday()
        h = horarios_by_dia.get(dia_semana)
        if not h:
            continue

        inicio_h, inicio_m = _parse_hhmm(h.hora_inicio)
        fim_h, fim_m = _parse_hhmm(h.hora_fim)
        inicio_dia = datetime.combine(data_br, time(inicio_h, inicio_m), tzinfo=TZ_BR)
        fim_dia = datetime.combine(data_br, time(fim_h, fim_m), tzinfo=TZ_BR)

        atual = inicio_dia
        delta = timedelta(minutes=h.intervalo_slot_min)

        while atual + delta <= fim_dia:
            slot_utc_naive = atual.astimezone(timezone.utc).replace(tzinfo=None)
            slot_fim_utc = slot_utc_naive + delta

            # Filtra: antecedência mínima
            if atual < minimo_disponivel:
                atual += delta
                continue

            # Filtra: já ocupado (sobreposição de intervalos, não igualdade)
            if any(o_ini < slot_fim_utc and slot_utc_naive < o_fim
                   for o_ini, o_fim in ocupados_utc):
                atual += delta
                continue

            # Filtra: cruza um bloqueio de agenda (qualquer sobreposição)
            if any(b_ini < slot_fim_utc and slot_utc_naive < b_fim
                   for b_ini, b_fim in bloqueios):
                atual += delta
                continue

            slots.append({
                "numero": len(slots) + 1,
                "data_hora_utc": slot_utc_naive,
                "data_hora_br": atual,
                "label": _label_amigavel(atual, agora),
            })

            if len(slots) >= n_slots:
                return slots

            atual += delta

    return slots


def formatar_opcoes_pra_mensagem(slots: list[dict]) -> str:
    """Monta lista numerada pra colar em template de mensagem."""
    if not slots:
        return ""
    return "\n".join(f"{s['numero']}. {s['label']}" for s in slots)


def extrair_numero_resposta(mensagem: str, max_num: int) -> Optional[int]:
    """Tenta extrair número da resposta da paciente (ex: '2', 'opção 2', 'a 2', '2 por favor')."""
    import re
    if not mensagem:
        return None
    # Acha primeiro número entre 1 e max_num na mensagem
    for match in re.finditer(r"\b(\d+)\b", mensagem):
        n = int(match.group(1))
        if 1 <= n <= max_num:
            return n
    return None


# ============================================================================
# Internals
# ============================================================================

def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _label_amigavel(dt_br: datetime, agora_br_dt: datetime) -> str:
    """'hoje 15:30' | 'amanhã (qua) 14:00' | 'qui 23/05 10:00'"""
    delta_dias = (dt_br.date() - agora_br_dt.date()).days
    hora_str = dt_br.strftime("%H:%M")

    if delta_dias == 0:
        return f"hoje {hora_str}"
    if delta_dias == 1:
        return f"amanhã ({DIA_SEMANA_BR[dt_br.weekday()]}) {hora_str}"
    if 2 <= delta_dias <= 6:
        return f"{DIA_SEMANA_LONGO[dt_br.weekday()]} {dt_br.strftime('%d/%m')} {hora_str}"
    return f"{dt_br.strftime('%d/%m')} {hora_str}"
