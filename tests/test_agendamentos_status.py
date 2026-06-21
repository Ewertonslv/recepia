"""Testes das transições de status de agendamento (_TRANSICOES_PERMITIDAS).

Regras: PENDENTE pode ir pra qualquer estado; estados finais (REALIZADO/
NO_SHOW/CANCELADO) só podem reabrir pra PENDENTE.
"""
from datetime import datetime, timedelta


def _setup_agendamento(client, headers):
    pac = client.post(
        "/api/pacientes", headers=headers,
        json={"nome": "Paciente Status", "telefone": "5511966660000"},
    ).json()
    dh = (datetime.utcnow() + timedelta(days=1)).isoformat()
    return client.post(
        "/api/agendamentos", headers=headers,
        json={"paciente_id": pac["id"], "data_hora": dh, "duracao_minutos": 30, "servico": "X"},
    ).json()


def _set_status(client, headers, ag_id, status):
    return client.put(f"/api/agendamentos/{ag_id}", headers=headers, json={"status": status})


class TestAgendamentoStatusTransicoes:
    def test_pendente_para_confirmado(self, client, auth_headers_a):
        ag = _setup_agendamento(client, auth_headers_a)
        r = _set_status(client, auth_headers_a, ag["id"], "confirmado")
        assert r.status_code == 200
        assert r.json()["status"] == "confirmado"

    def test_confirmado_para_realizado(self, client, auth_headers_a):
        ag = _setup_agendamento(client, auth_headers_a)
        _set_status(client, auth_headers_a, ag["id"], "confirmado")
        r = _set_status(client, auth_headers_a, ag["id"], "realizado")
        assert r.status_code == 200
        assert r.json()["status"] == "realizado"

    def test_transicao_invalida_bloqueada(self, client, auth_headers_a):
        ag = _setup_agendamento(client, auth_headers_a)
        _set_status(client, auth_headers_a, ag["id"], "realizado")  # pendente -> realizado: ok
        r = _set_status(client, auth_headers_a, ag["id"], "confirmado")  # realizado -> confirmado: bloqueado
        assert r.status_code == 400

    def test_reabrir_realizado_para_pendente(self, client, auth_headers_a):
        ag = _setup_agendamento(client, auth_headers_a)
        _set_status(client, auth_headers_a, ag["id"], "realizado")
        r = _set_status(client, auth_headers_a, ag["id"], "pendente")  # reabrir é permitido
        assert r.status_code == 200

    def test_status_desconhecido_rejeitado(self, client, auth_headers_a):
        ag = _setup_agendamento(client, auth_headers_a)
        r = _set_status(client, auth_headers_a, ag["id"], "banana")
        assert r.status_code == 400
