"""Testes do SchedulerService — busca de agendamentos pra confirmar/lembrar.

Foca em: scoping por clinica_id, filtros corretos (status/janela de tempo),
e processar_resposta_paciente com mock de WhatsApp.
"""
from datetime import datetime, timedelta

import pytest

from models import Agendamento, Interacao, Paciente, Status
from services.scheduler import SchedulerService


# ===========================================================================
# Helpers
# ===========================================================================

def _make_scheduler_with_fake_ws(db_session, monkeypatch):
    """Constrói scheduler com WhatsAppService mockado pra não chamar Evolution."""
    sent = []

    class FakeWS:
        def enviar_mensagem(self, instance_name, telefone, mensagem):
            sent.append({"to": telefone, "msg": mensagem, "inst": instance_name})
            return {"success": True}

    sched = SchedulerService(db_session)
    sched.whatsapp = FakeWS()
    return sched, sent


def _criar_paciente(db, clinica_id, nome="P", telefone="5511999990000"):
    p = Paciente(clinica_id=clinica_id, nome=nome, telefone=telefone)
    db.add(p)
    db.flush()
    return p


def _criar_agendamento(db, clinica_id, paciente_id, **kwargs):
    defaults = {
        "data_hora": datetime.utcnow() + timedelta(hours=12),
        "status": Status.PENDENTE,
        "confirmacao_enviada": False,
        "segunda_confirmacao": False,
    }
    defaults.update(kwargs)
    ag = Agendamento(clinica_id=clinica_id, paciente_id=paciente_id, **defaults)
    db.add(ag)
    db.flush()
    return ag


# ===========================================================================
# Busca pra confirmar
# ===========================================================================

class TestBuscarConfirmar:
    def test_so_clinicas_ativas_e_conectadas(self, db_session, clinica_fake):
        c = clinica_fake["clinica"]
        c.evolution_conectado = True
        db_session.commit()
        p = _criar_paciente(db_session, c.id)
        _criar_agendamento(db_session, c.id, p.id)
        db_session.commit()

        sched = SchedulerService(db_session)
        ags = sched.buscar_agendamentos_pra_confirmar()
        assert len(ags) == 1

    def test_nao_busca_se_clinica_desconectada(self, db_session, clinica_fake):
        c = clinica_fake["clinica"]
        c.evolution_conectado = False  # desconectada
        db_session.commit()
        p = _criar_paciente(db_session, c.id)
        _criar_agendamento(db_session, c.id, p.id)
        db_session.commit()

        sched = SchedulerService(db_session)
        ags = sched.buscar_agendamentos_pra_confirmar()
        assert ags == []

    def test_nao_busca_ja_confirmado_enviado(self, db_session, clinica_fake):
        c = clinica_fake["clinica"]
        c.evolution_conectado = True
        db_session.commit()
        p = _criar_paciente(db_session, c.id)
        _criar_agendamento(db_session, c.id, p.id, confirmacao_enviada=True)
        db_session.commit()

        sched = SchedulerService(db_session)
        assert sched.buscar_agendamentos_pra_confirmar() == []

    def test_nao_busca_fora_da_janela(self, db_session, clinica_fake):
        c = clinica_fake["clinica"]
        c.evolution_conectado = True
        db_session.commit()
        p = _criar_paciente(db_session, c.id)
        # 10 dias no futuro → fora da janela de 24h
        _criar_agendamento(db_session, c.id, p.id,
                          data_hora=datetime.utcnow() + timedelta(days=10))
        db_session.commit()
        sched = SchedulerService(db_session)
        assert sched.buscar_agendamentos_pra_confirmar() == []

    def test_isolamento_por_clinica_id(self, db_session, clinica_fake, clinica_fake_b):
        ca = clinica_fake["clinica"]
        cb = clinica_fake_b["clinica"]
        ca.evolution_conectado = True
        cb.evolution_conectado = True
        db_session.commit()

        pa = _criar_paciente(db_session, ca.id, telefone="5511910000001")
        pb = _criar_paciente(db_session, cb.id, telefone="5511920000002")
        _criar_agendamento(db_session, ca.id, pa.id)
        _criar_agendamento(db_session, cb.id, pb.id)
        db_session.commit()

        sched = SchedulerService(db_session)
        ags_a = sched.buscar_agendamentos_pra_confirmar(clinica_id=ca.id)
        ags_b = sched.buscar_agendamentos_pra_confirmar(clinica_id=cb.id)

        assert len(ags_a) == 1
        assert len(ags_b) == 1
        assert ags_a[0].clinica_id == ca.id
        assert ags_b[0].clinica_id == cb.id
        # CRITICAL: nenhum cross-tenant
        assert ags_a[0].id != ags_b[0].id


class TestBuscarLembrete:
    def test_busca_so_pendente_com_confirmacao_enviada_sem_segunda(
        self, db_session, clinica_fake
    ):
        c = clinica_fake["clinica"]
        c.evolution_conectado = True
        db_session.commit()
        p = _criar_paciente(db_session, c.id)
        _criar_agendamento(
            db_session, c.id, p.id,
            data_hora=datetime.utcnow() + timedelta(hours=1),
            confirmacao_enviada=True,
            segunda_confirmacao=False,
        )
        db_session.commit()

        sched = SchedulerService(db_session)
        ags = sched.buscar_agendamentos_pra_lembrete()
        assert len(ags) == 1

    def test_nao_envia_lembrete_se_ja_segunda_confirmacao(self, db_session, clinica_fake):
        c = clinica_fake["clinica"]
        c.evolution_conectado = True
        db_session.commit()
        p = _criar_paciente(db_session, c.id)
        _criar_agendamento(
            db_session, c.id, p.id,
            data_hora=datetime.utcnow() + timedelta(hours=1),
            confirmacao_enviada=True,
            segunda_confirmacao=True,
        )
        db_session.commit()
        sched = SchedulerService(db_session)
        assert sched.buscar_agendamentos_pra_lembrete() == []


# ===========================================================================
# Envio
# ===========================================================================

class TestEnviarConfirmacao:
    def test_envia_e_marca_flag(self, db_session, clinica_fake, monkeypatch):
        c = clinica_fake["clinica"]
        c.evolution_conectado = True
        db_session.commit()
        p = _criar_paciente(db_session, c.id, telefone="5511900000001")
        ag = _criar_agendamento(db_session, c.id, p.id)
        db_session.commit()

        sched, sent = _make_scheduler_with_fake_ws(db_session, monkeypatch)
        ok = sched.enviar_confirmacao(ag)
        assert ok is True
        assert ag.confirmacao_enviada is True
        assert len(sent) == 1
        assert sent[0]["to"] == "5511900000001"

        # Verifica que foi registrada Interacao
        interacoes = db_session.query(Interacao).filter(Interacao.agendamento_id == ag.id).all()
        assert len(interacoes) == 1
        assert interacoes[0].tipo == "confirmacao"


# ===========================================================================
# Processar resposta — multi-tenant scoped
# ===========================================================================

class TestProcessarResposta:
    def test_resposta_sim_confirma(self, db_session, clinica_fake, monkeypatch):
        c = clinica_fake["clinica"]
        p = _criar_paciente(db_session, c.id, telefone="5511955555555")
        ag = _criar_agendamento(db_session, c.id, p.id, confirmacao_enviada=True)
        db_session.commit()

        sched, sent = _make_scheduler_with_fake_ws(db_session, monkeypatch)
        resultado = sched.processar_resposta_paciente(c, "5511955555555", "sim")

        assert resultado["status"] == "processed"
        assert resultado["novo_status"] == Status.CONFIRMADO
        db_session.refresh(ag)
        assert ag.status == Status.CONFIRMADO

    def test_resposta_nao_cancela(self, db_session, clinica_fake, monkeypatch):
        c = clinica_fake["clinica"]
        p = _criar_paciente(db_session, c.id, telefone="5511955555556")
        ag = _criar_agendamento(db_session, c.id, p.id, confirmacao_enviada=True)
        db_session.commit()

        sched, _ = _make_scheduler_with_fake_ws(db_session, monkeypatch)
        resultado = sched.processar_resposta_paciente(c, "5511955555556", "não posso")
        assert resultado["novo_status"] == Status.CANCELADO

    def test_resposta_reagendar(self, db_session, clinica_fake, monkeypatch):
        c = clinica_fake["clinica"]
        p = _criar_paciente(db_session, c.id, telefone="5511955555557")
        _criar_agendamento(db_session, c.id, p.id, confirmacao_enviada=True)
        db_session.commit()

        sched, _ = _make_scheduler_with_fake_ws(db_session, monkeypatch)
        resultado = sched.processar_resposta_paciente(c, "5511955555557", "outro horário")
        assert resultado["novo_status"] == Status.REAGENDADO

    def test_telefone_sem_agendamento_ignora(self, db_session, clinica_fake, monkeypatch):
        c = clinica_fake["clinica"]
        sched, _ = _make_scheduler_with_fake_ws(db_session, monkeypatch)
        resultado = sched.processar_resposta_paciente(c, "5511999999999", "sim")
        assert resultado["status"] == "ignored"
        assert resultado["reason"] == "no_pending_appointment"

    def test_nao_busca_agendamento_de_outra_clinica(
        self, db_session, clinica_fake, clinica_fake_b, monkeypatch
    ):
        """Crítico: paciente da B com mesmo telefone NÃO pode confirmar agendamento da A."""
        ca = clinica_fake["clinica"]
        cb = clinica_fake_b["clinica"]

        # Mesma pessoa cadastrada em A com agendamento pendente
        pa = _criar_paciente(db_session, ca.id, telefone="5511966666666")
        _criar_agendamento(db_session, ca.id, pa.id, confirmacao_enviada=True)
        db_session.commit()

        # Webhook chega na instância da CLINICA B com o mesmo telefone
        sched, _ = _make_scheduler_with_fake_ws(db_session, monkeypatch)
        resultado = sched.processar_resposta_paciente(cb, "5511966666666", "sim")

        assert resultado["status"] == "ignored"
        assert resultado["reason"] == "no_pending_appointment"
