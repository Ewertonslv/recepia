"""Testes do webhook /api/webhook/evolution — roteamento por instance_name.

Contrato de segurança (B8/F14): o webhook responde sempre `{"status": "ok"}` para
qualquer payload válido (não vaza se a clínica existe, nem a intenção/agendamento),
e responde 400 para payload inválido (schema). O valor do teste está nos EFEITOS
COLATERAIS (interação criada ou não, conexão atualizada, agendamento confirmado).
"""
from datetime import datetime, timedelta

import pytest

from models import Agendamento, Clinica, Interacao, Paciente, Status


def _payload_msg(instance, from_jid, texto, from_me=False):
    return {
        "event": "messages.upsert",
        "instance": instance,
        "data": {
            "key": {"fromMe": from_me, "remoteJid": from_jid},
            "message": {"conversation": texto},
        },
    }


class TestWebhookRouting:
    def test_sem_instance_rejeita(self, client):
        # Schema (F14) exige `instance` → payload sem ele é rejeitado com 400.
        resp = client.post("/api/webhook/evolution", json={"event": "messages.upsert"})
        assert resp.status_code == 400

    def test_instance_inexistente_responde_ok_sem_vazar(self, client):
        # B8: não vaza se a clínica existe — sempre 200 {"status": "ok"}.
        payload = _payload_msg("instancia-fantasma", "5511900000000@s.whatsapp.net", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_from_me_nao_processa(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "5511999990000@s.whatsapp.net", "msg", from_me=True)
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.json() == {"status": "ok"}
        assert db_session.query(Interacao).count() == 0  # mensagem própria não vira interação

    def test_mensagem_de_grupo_nao_processa(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "12345-67890@g.us", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.json() == {"status": "ok"}
        assert db_session.query(Interacao).count() == 0  # grupo ignorado

    def test_mensagem_sem_texto_nao_processa(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = {
            "event": "messages.upsert",
            "instance": instance,
            "data": {
                "key": {"fromMe": False, "remoteJid": "5511999990000@s.whatsapp.net"},
                "message": {},
            },
        }
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.json() == {"status": "ok"}
        assert db_session.query(Interacao).count() == 0  # sem texto → nada a processar


class TestWebhookConexao:
    def test_connection_update_open_marca_conectado(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = {"event": "connection.update", "instance": instance, "data": {"state": "open"}}
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        db_session.expire_all()
        c = db_session.query(Clinica).filter(Clinica.id == clinica_fake["clinica"].id).first()
        assert c.evolution_conectado is True

    def test_connection_update_close_marca_desconectado(self, client, db_session, clinica_fake):
        clinica_fake["clinica"].evolution_conectado = True
        db_session.commit()
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = {"event": "CONNECTION_UPDATE", "instance": instance, "data": {"state": "close"}}
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200

        db_session.expire_all()
        c = db_session.query(Clinica).filter(Clinica.id == clinica_fake["clinica"].id).first()
        assert c.evolution_conectado is False


class TestWebhookProcessamentoResposta:
    def test_resposta_sim_confirma_agendamento(self, client, db_session, clinica_fake):
        telefone = "5511999990000"
        p = Paciente(clinica_id=clinica_fake["clinica"].id, nome="Maria", telefone=telefone)
        db_session.add(p)
        db_session.flush()
        ag = Agendamento(
            clinica_id=clinica_fake["clinica"].id,
            paciente_id=p.id,
            data_hora=datetime.utcnow() + timedelta(hours=24),
            status=Status.PENDENTE,
            confirmacao_enviada=True,
        )
        db_session.add(ag)
        db_session.commit()
        ag_id = ag.id

        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, f"{telefone}@s.whatsapp.net", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}  # B8: não vaza a intenção/novo status

        # Efeito colateral real: o agendamento foi CONFIRMADO.
        db_session.expire_all()
        ag_db = db_session.query(Agendamento).filter(Agendamento.id == ag_id).first()
        assert ag_db.status == Status.CONFIRMADO

    def test_resposta_de_telefone_sem_agendamento_nao_quebra(self, client, db_session, clinica_fake):
        # Telefone sem paciente/agendamento → tratado como contato novo (boas-vindas),
        # sem confirmar nada. Webhook responde 200 silencioso.
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "5511900000000@s.whatsapp.net", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_evento_desconhecido_responde_ok(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        resp = client.post(
            "/api/webhook/evolution",
            json={"event": "evento.xyz", "instance": instance},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestWebhookStatus:
    def test_status_ok(self, client):
        resp = client.get("/api/webhook/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
