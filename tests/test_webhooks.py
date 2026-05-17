"""Testes do webhook /api/webhook/evolution — roteamento por instance_name."""
from datetime import datetime, timedelta

import pytest

from models import Agendamento, Paciente, Status


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
    def test_sem_instance_ignora(self, client):
        resp = client.post("/api/webhook/evolution", json={"event": "messages.upsert"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert resp.json()["reason"] == "no_instance"

    def test_instance_inexistente_ignora(self, client):
        payload = _payload_msg("instancia-fantasma", "5511900000000@s.whatsapp.net", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert resp.json()["reason"] == "clinic_not_found"

    def test_from_me_ignora(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "5511999990000@s.whatsapp.net", "msg", from_me=True)
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.json()["status"] == "ignored"
        assert resp.json()["reason"] == "from_me"

    def test_mensagem_de_grupo_ignora(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "12345-67890@g.us", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.json()["status"] == "ignored"
        assert resp.json()["reason"] == "group_message"

    def test_mensagem_sem_texto_ignora(self, client, clinica_fake):
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
        assert resp.json()["status"] == "ignored"
        assert resp.json()["reason"] == "no_text"


class TestWebhookConexao:
    def test_connection_update_open_marca_conectado(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = {
            "event": "connection.update",
            "instance": instance,
            "data": {"state": "open"},
        }
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json()["conectado"] is True

        db_session.expire_all()
        from models import Clinica
        c = db_session.query(Clinica).filter(Clinica.id == clinica_fake["clinica"].id).first()
        assert c.evolution_conectado is True

    def test_connection_update_close_marca_desconectado(self, client, db_session, clinica_fake):
        clinica_fake["clinica"].evolution_conectado = True
        db_session.commit()
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = {
            "event": "CONNECTION_UPDATE",
            "instance": instance,
            "data": {"state": "close"},
        }
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.json()["conectado"] is False


class TestWebhookProcessamentoResposta:
    def test_resposta_sim_confirma_agendamento(self, client, db_session, clinica_fake):
        # Cria paciente + agendamento pendente
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

        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, f"{telefone}@s.whatsapp.net", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "processed"
        assert body["resultado"]["novo_status"] == Status.CONFIRMADO

    def test_resposta_de_telefone_sem_agendamento_ignora(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "5511900000000@s.whatsapp.net", "sim")
        resp = client.post("/api/webhook/evolution", json=payload)
        assert resp.status_code == 200
        assert resp.json()["resultado"]["status"] == "ignored"
        assert resp.json()["resultado"]["reason"] == "no_pending_appointment"

    def test_evento_desconhecido_ignora(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        resp = client.post(
            "/api/webhook/evolution",
            json={"event": "evento.xyz", "instance": instance},
        )
        assert resp.json()["status"] == "ignored"


class TestWebhookStatus:
    def test_status_ok(self, client):
        resp = client.get("/api/webhook/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
