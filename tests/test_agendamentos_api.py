"""Testes CRUD de agendamentos + ISOLAMENTO MULTI-TENANT."""
from datetime import datetime, timedelta

import pytest


def _criar_paciente(client, headers, nome="Paciente X", telefone="5511977777777"):
    r = client.post(
        "/api/pacientes",
        headers=headers,
        json={"nome": nome, "telefone": telefone},
    )
    return r.json()


def _criar_agendamento(client, headers, paciente_id, data_hora=None, servico="Limpeza de pele"):
    if data_hora is None:
        data_hora = (datetime.utcnow() + timedelta(days=1)).isoformat()
    return client.post(
        "/api/agendamentos",
        headers=headers,
        json={
            "paciente_id": paciente_id,
            "data_hora": data_hora,
            "duracao_minutos": 60,
            "servico": servico,
        },
    )


# ===========================================================================
# CRUD
# ===========================================================================

class TestAgendamentoCRUD:
    def test_criar_agendamento(self, client, auth_headers_a):
        p = _criar_paciente(client, auth_headers_a)
        resp = _criar_agendamento(client, auth_headers_a, p["id"])
        assert resp.status_code == 201
        body = resp.json()
        assert body["paciente_id"] == p["id"]
        assert body["status"] == "pendente"
        assert body["confirmacao_enviada"] is False
        assert body["servico"] == "Limpeza de pele"

    def test_criar_agendamento_paciente_inexistente_400(self, client, auth_headers_a):
        resp = _criar_agendamento(client, auth_headers_a, "id-fake")
        assert resp.status_code == 400

    def test_listar_agendamentos(self, client, auth_headers_a):
        p = _criar_paciente(client, auth_headers_a)
        _criar_agendamento(client, auth_headers_a, p["id"])
        _criar_agendamento(client, auth_headers_a, p["id"],
                           data_hora=(datetime.utcnow() + timedelta(days=2)).isoformat())
        resp = client.get("/api/agendamentos", headers=auth_headers_a)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_listar_com_filtro_status(self, client, auth_headers_a, db_session):
        p = _criar_paciente(client, auth_headers_a)
        ag = _criar_agendamento(client, auth_headers_a, p["id"]).json()

        # Atualiza um pra confirmado
        client.put(
            f"/api/agendamentos/{ag['id']}",
            headers=auth_headers_a,
            json={"status": "confirmado"},
        )
        resp = client.get("/api/agendamentos?status_filtro=confirmado", headers=auth_headers_a)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["status"] == "confirmado"

    def test_atualizar_agendamento(self, client, auth_headers_a):
        p = _criar_paciente(client, auth_headers_a)
        ag = _criar_agendamento(client, auth_headers_a, p["id"]).json()
        resp = client.put(
            f"/api/agendamentos/{ag['id']}",
            headers=auth_headers_a,
            json={"status": "confirmado", "servico": "Botox"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmado"
        assert resp.json()["servico"] == "Botox"

    def test_cancelar_agendamento(self, client, auth_headers_a):
        p = _criar_paciente(client, auth_headers_a)
        ag = _criar_agendamento(client, auth_headers_a, p["id"]).json()
        resp = client.delete(f"/api/agendamentos/{ag['id']}", headers=auth_headers_a)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelado"

    def test_obter_inexistente_404(self, client, auth_headers_a):
        resp = client.get("/api/agendamentos/id-fake", headers=auth_headers_a)
        assert resp.status_code == 404


# ===========================================================================
# ISOLAMENTO MULTI-TENANT (CRITICO)
# ===========================================================================

class TestIsolamentoMultiTenant:
    def test_listar_so_retorna_da_propria_clinica(self, client, auth_headers_a, auth_headers_b):
        pa = _criar_paciente(client, auth_headers_a, "PA", "5511911111111")
        pb = _criar_paciente(client, auth_headers_b, "PB", "5511922222222")
        _criar_agendamento(client, auth_headers_a, pa["id"], servico="A-only")
        _criar_agendamento(client, auth_headers_b, pb["id"], servico="B-only")

        resp_a = client.get("/api/agendamentos", headers=auth_headers_a)
        resp_b = client.get("/api/agendamentos", headers=auth_headers_b)

        servicos_a = {a["servico"] for a in resp_a.json()}
        servicos_b = {a["servico"] for a in resp_b.json()}
        assert "A-only" in servicos_a
        assert "A-only" not in servicos_b
        assert "B-only" in servicos_b
        assert "B-only" not in servicos_a

    def test_obter_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        pa = _criar_paciente(client, auth_headers_a)
        ag_a = _criar_agendamento(client, auth_headers_a, pa["id"]).json()
        resp = client.get(f"/api/agendamentos/{ag_a['id']}", headers=auth_headers_b)
        assert resp.status_code == 404

    def test_atualizar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        pa = _criar_paciente(client, auth_headers_a)
        ag_a = _criar_agendamento(client, auth_headers_a, pa["id"]).json()
        resp = client.put(
            f"/api/agendamentos/{ag_a['id']}",
            headers=auth_headers_b,
            json={"status": "cancelado"},
        )
        assert resp.status_code == 404

    def test_criar_agendamento_pra_paciente_de_outra_clinica_rejeita(
        self, client, auth_headers_a, auth_headers_b
    ):
        """Clínica B não pode criar agendamento usando paciente_id da A."""
        pa = _criar_paciente(client, auth_headers_a, "Da A", "5511910000000")
        resp = _criar_agendamento(client, auth_headers_b, pa["id"])
        assert resp.status_code == 400  # paciente não encontrado nesta clínica

    def test_cancelar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        pa = _criar_paciente(client, auth_headers_a)
        ag_a = _criar_agendamento(client, auth_headers_a, pa["id"]).json()
        resp = client.delete(f"/api/agendamentos/{ag_a['id']}", headers=auth_headers_b)
        assert resp.status_code == 404
