"""Testes do webhook /api/webhook/evolution — assinatura HMAC + roteamento.

Contrato de segurança (F2/B8/F14):
- F2: o body é autenticado por HMAC-SHA256 (`EVOLUTION_WEBHOOK_SECRET`). Sem
  assinatura válida → 401, ANTES de qualquer processamento.
- B8: para payload válido responde sempre `{"status": "ok"}` (não vaza se a
  clínica existe, nem a intenção/agendamento).
- F14: payload que não bate no schema → 400.
O valor dos testes de efeito está nos EFEITOS COLATERAIS (interação criada ou
não, conexão atualizada, agendamento confirmado).
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta

from config import settings
from models import Agendamento, Clinica, Interacao, Paciente, Status


def _assinar(body_bytes: bytes) -> str:
    return hmac.new(
        settings.EVOLUTION_WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256
    ).hexdigest()


def _post(client, payload, *, assinar=True, assinatura=None):
    """POST no webhook com corpo pré-serializado + assinatura HMAC do MESMO corpo.

    Serializamos aqui (content=) pra que os bytes assinados sejam exatamente os
    que o servidor lê em `await request.body()`.
    """
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if assinatura is not None:
        headers["X-Webhook-Signature"] = assinatura
    elif assinar:
        headers["X-Webhook-Signature"] = _assinar(body)
    return client.post("/api/webhook/evolution", content=body, headers=headers)


def _payload_msg(instance, from_jid, texto, from_me=False):
    return {
        "event": "messages.upsert",
        "instance": instance,
        "data": {
            "key": {"fromMe": from_me, "remoteJid": from_jid},
            "message": {"conversation": texto},
        },
    }


class TestWebhookAssinatura:
    """F2: HMAC é a primeira barreira — vem antes do schema e do roteamento."""

    def test_sem_assinatura_rejeita_401(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        resp = _post(client, _payload_msg(instance, "5511999990000@s.whatsapp.net", "sim"),
                     assinar=False)
        assert resp.status_code == 401

    def test_assinatura_invalida_rejeita_401(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        resp = _post(client, _payload_msg(instance, "5511999990000@s.whatsapp.net", "sim"),
                     assinatura="sha256=deadbeef")
        assert resp.status_code == 401

    def test_assinatura_de_outro_corpo_rejeita_401(self, client, clinica_fake):
        # Assinatura válida PARA OUTRO body não pode passar (replay/adulteração).
        instance = clinica_fake["clinica"].evolution_instance_name
        outra = _assinar(json.dumps({"event": "x", "instance": instance}).encode())
        resp = _post(client, _payload_msg(instance, "5511999990000@s.whatsapp.net", "sim"),
                     assinatura=outra)
        assert resp.status_code == 401

    def test_assinatura_valida_processa(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        resp = _post(client, _payload_msg(instance, "5511900000000@s.whatsapp.net", "oi"))
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_prefixo_sha256_aceito(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        body = json.dumps(_payload_msg(instance, "5511900000000@s.whatsapp.net", "oi")).encode()
        resp = _post(client, json.loads(body), assinatura="sha256=" + _assinar(body))
        assert resp.status_code == 200


class TestWebhookRouting:
    def test_sem_instance_rejeita(self, client):
        # Schema (F14) exige `instance` → payload (assinado) sem ele é rejeitado com 400.
        resp = _post(client, {"event": "messages.upsert"})
        assert resp.status_code == 400

    def test_instance_inexistente_responde_ok_sem_vazar(self, client):
        # B8: não vaza se a clínica existe — sempre 200 {"status": "ok"}.
        payload = _payload_msg("instancia-fantasma", "5511900000000@s.whatsapp.net", "sim")
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_from_me_nao_processa(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "5511999990000@s.whatsapp.net", "msg", from_me=True)
        resp = _post(client, payload)
        assert resp.json() == {"status": "ok"}
        assert db_session.query(Interacao).count() == 0  # mensagem própria não vira interação

    def test_mensagem_de_grupo_nao_processa(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = _payload_msg(instance, "12345-67890@g.us", "sim")
        resp = _post(client, payload)
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
        resp = _post(client, payload)
        assert resp.json() == {"status": "ok"}
        assert db_session.query(Interacao).count() == 0  # sem texto → nada a processar


class TestWebhookConexao:
    def test_connection_update_open_marca_conectado(self, client, db_session, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        payload = {"event": "connection.update", "instance": instance, "data": {"state": "open"}}
        resp = _post(client, payload)
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
        resp = _post(client, payload)
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
        resp = _post(client, payload)
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
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_evento_desconhecido_responde_ok(self, client, clinica_fake):
        instance = clinica_fake["clinica"].evolution_instance_name
        resp = _post(client, {"event": "evento.xyz", "instance": instance})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestWebhookStatus:
    def test_status_ok(self, client):
        resp = client.get("/api/webhook/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
