"""Testes de services/slots.sugerir_slots — a lógica central de horários livres.

O 'agora' é fixado (monkeypatch de agora_br/agora_utc) pra tornar determinístico:
segunda-feira 2026-07-06 08:00 BR == 11:00 UTC.
"""
from datetime import datetime, timedelta

import pytest

from core.timezones import TZ_BR
from models import Agendamento, BloqueioAgenda, HorarioFuncionamento, Status
from services import slots as slots_mod

# Segunda-feira. weekday() == 0.
_NOW_BR = datetime(2026, 7, 6, 8, 0, tzinfo=TZ_BR)
_NOW_UTC_NAIVE = datetime(2026, 7, 6, 11, 0)  # 08:00 BR = 11:00 UTC


@pytest.fixture(autouse=True)
def _fixar_agora(monkeypatch):
    monkeypatch.setattr(slots_mod, "agora_br", lambda: _NOW_BR)
    monkeypatch.setattr(slots_mod, "agora_utc", lambda: _NOW_UTC_NAIVE)


def _horario(db, clinica, dia_semana, ini="09:00", fim="18:00", slot=60, ativo=True):
    h = HorarioFuncionamento(
        clinica_id=clinica.id, dia_semana=dia_semana,
        hora_inicio=ini, hora_fim=fim, intervalo_slot_min=slot, ativo=ativo,
    )
    db.add(h)
    db.flush()
    return h


class TestSugerirSlots:
    def test_sem_horarios_retorna_vazio(self, db_session, clinica_fake):
        assert slots_mod.sugerir_slots(db_session, clinica_fake["clinica"]) == []

    def test_respeita_antecedencia_minima(self, db_session, clinica_fake):
        # Seg 09-18, agora 08:00 → mínimo = 12:00 (4h). 09/10/11 são cedo demais.
        _horario(db_session, clinica_fake["clinica"], 0)
        s = slots_mod.sugerir_slots(db_session, clinica_fake["clinica"], n_slots=1)
        assert len(s) == 1
        assert s[0]["data_hora_br"].hour == 12
        # 12:00 BR == 15:00 UTC naive.
        assert s[0]["data_hora_utc"] == datetime(2026, 7, 6, 15, 0)

    def test_pula_slot_ocupado(self, db_session, clinica_fake):
        _horario(db_session, clinica_fake["clinica"], 0)
        # Ocupa 12:00 BR (== 15:00 UTC) → primeiro livre vira 13:00.
        db_session.add(Agendamento(
            clinica_id=clinica_fake["clinica"].id,
            paciente_id=_pac(db_session, clinica_fake["clinica"]),
            data_hora=datetime(2026, 7, 6, 15, 0), status=Status.PENDENTE,
        ))
        db_session.flush()
        s = slots_mod.sugerir_slots(db_session, clinica_fake["clinica"], n_slots=1)
        assert s[0]["data_hora_br"].hour == 13

    def test_pula_slot_bloqueado(self, db_session, clinica_fake):
        _horario(db_session, clinica_fake["clinica"], 0)
        # Bloqueia 12:00-12:30 BR (== 15:00-15:30 UTC) → primeiro livre vira 13:00.
        db_session.add(BloqueioAgenda(
            clinica_id=clinica_fake["clinica"].id,
            inicio=datetime(2026, 7, 6, 15, 0), fim=datetime(2026, 7, 6, 15, 30),
        ))
        db_session.flush()
        s = slots_mod.sugerir_slots(db_session, clinica_fake["clinica"], n_slots=1)
        assert s[0]["data_hora_br"].hour == 13

    def test_horario_inativo_ignorado(self, db_session, clinica_fake):
        _horario(db_session, clinica_fake["clinica"], 0, ativo=False)
        assert slots_mod.sugerir_slots(db_session, clinica_fake["clinica"]) == []

    def test_numeracao_sequencial(self, db_session, clinica_fake):
        _horario(db_session, clinica_fake["clinica"], 0)
        s = slots_mod.sugerir_slots(db_session, clinica_fake["clinica"], n_slots=3)
        assert [x["numero"] for x in s] == [1, 2, 3]

    def test_excluir_agendamento_libera_slot(self, db_session, clinica_fake):
        _horario(db_session, clinica_fake["clinica"], 0)
        ag = Agendamento(
            clinica_id=clinica_fake["clinica"].id,
            paciente_id=_pac(db_session, clinica_fake["clinica"]),
            data_hora=datetime(2026, 7, 6, 15, 0), status=Status.CONFIRMADO,
        )
        db_session.add(ag)
        db_session.flush()
        # Sem excluir: 12:00 ocupado. Excluindo o próprio agendamento: 12:00 volta.
        s = slots_mod.sugerir_slots(db_session, clinica_fake["clinica"], n_slots=1,
                                    excluir_agendamento_id=ag.id)
        assert s[0]["data_hora_br"].hour == 12


class TestExtrairNumero:
    @pytest.mark.parametrize("msg,esperado", [
        ("2", 2), ("opção 2", 2), ("a 2 por favor", 2), ("quero o 3", 3),
        ("nenhum", None), ("", None), ("99", None),
    ])
    def test_extrai(self, msg, esperado):
        assert slots_mod.extrair_numero_resposta(msg, max_num=3) == esperado


def _pac(db, clinica):
    from models import Paciente
    p = Paciente(clinica_id=clinica.id, nome="Slot", telefone="5511900001111")
    db.add(p)
    db.flush()
    return p.id
