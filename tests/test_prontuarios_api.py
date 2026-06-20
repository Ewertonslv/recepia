"""Testes do CRUD de prontuários — LGPD Art. 11/37 (dado sensível de saúde).

Cobre: CRUD, validação de FK, isolamento multi-tenant (404, nunca 403/200),
audit obrigatório, DELETE só admin + motivo, e o feature-gate por plano.
"""
import pytest

from models import AcaoAudit, AuditLog, Plano, Usuario
from core.security import criar_token, hash_senha


def _criar_paciente(client, headers, nome="Paciente Teste", telefone="5511999990000"):
    return client.post(
        "/api/pacientes", headers=headers,
        json={"nome": nome, "telefone": telefone},
    ).json()


def _criar_prontuario(client, headers, paciente_id, **extra):
    payload = {"paciente_id": paciente_id, "anotacoes": "Consulta de rotina"}
    payload.update(extra)
    return client.post("/api/prontuarios", headers=headers, json=payload)


# ===========================================================================
# CRUD básico
# ===========================================================================

class TestProntuarioCRUD:
    def test_criar_prontuario(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        resp = _criar_prontuario(
            client, auth_headers_a, pac["id"],
            procedimentos_realizados="Profilaxia", alergias=["Dipirona"],
            proxima_acao="Retorno em 6 meses",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["paciente_id"] == pac["id"]
        assert body["alergias"] == ["Dipirona"]
        assert body["proxima_acao"] == "Retorno em 6 meses"
        assert body["fotos"] == []

    def test_criar_com_paciente_inexistente_404(self, client, auth_headers_a):
        resp = _criar_prontuario(client, auth_headers_a, "paciente-fake")
        assert resp.status_code == 404

    def test_listar_exige_paciente_id(self, client, auth_headers_a):
        resp = client.get("/api/prontuarios", headers=auth_headers_a)
        assert resp.status_code == 422  # paciente_id é Query obrigatório

    def test_listar_por_paciente(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        _criar_prontuario(client, auth_headers_a, pac["id"])
        _criar_prontuario(client, auth_headers_a, pac["id"])
        resp = client.get(f"/api/prontuarios?paciente_id={pac['id']}", headers=auth_headers_a)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_obter_prontuario(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        resp = client.get(f"/api/prontuarios/{pr['id']}", headers=auth_headers_a)
        assert resp.status_code == 200
        assert resp.json()["id"] == pr["id"]

    def test_obter_inexistente_404(self, client, auth_headers_a):
        resp = client.get("/api/prontuarios/id-fake", headers=auth_headers_a)
        assert resp.status_code == 404

    def test_atualizar_prontuario(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        resp = client.put(
            f"/api/prontuarios/{pr['id']}", headers=auth_headers_a,
            json={"anotacoes": "Editado", "proxima_acao": "Alta"},
        )
        assert resp.status_code == 200
        assert resp.json()["anotacoes"] == "Editado"
        assert resp.json()["proxima_acao"] == "Alta"

    def test_alergia_acima_do_limite_rejeita(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        resp = _criar_prontuario(
            client, auth_headers_a, pac["id"], alergias=["x" * 81],
        )
        assert resp.status_code == 422


# ===========================================================================
# DELETE — só admin + motivo (LGPD Art. 16)
# ===========================================================================

class TestProntuarioDelete:
    def test_deletar_como_admin_com_motivo(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        resp = client.request(
            "DELETE",
            f"/api/prontuarios/{pr['id']}", headers=auth_headers_a,
            json={"motivo": "Solicitação do paciente (LGPD)"},
        )
        assert resp.status_code == 204
        check = client.get(f"/api/prontuarios/{pr['id']}", headers=auth_headers_a)
        assert check.status_code == 404

    def test_deletar_sem_motivo_rejeita(self, client, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        resp = client.request(
            "DELETE",f"/api/prontuarios/{pr['id']}", headers=auth_headers_a, json={})
        assert resp.status_code == 422

    def test_operador_nao_pode_deletar(self, client, db_session, clinica_fake, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        operador = Usuario(
            clinica_id=clinica_fake["clinica"].id, email="operador@clinicaa.com",
            senha_hash=hash_senha("senha12345"), nome="Operador", role="operador",
        )
        db_session.add(operador)
        db_session.commit()
        db_session.refresh(operador)
        op_headers = {"Authorization": f"Bearer {criar_token(operador.id, operador.clinica_id, operador.role)}"}
        resp = client.request(
            "DELETE",
            f"/api/prontuarios/{pr['id']}", headers=op_headers,
            json={"motivo": "tentativa do operador"},
        )
        assert resp.status_code == 403


# ===========================================================================
# Feature-gate por plano (prontuário só Pro/Enterprise/Trial)
# ===========================================================================

class TestProntuarioFeatureGate:
    def test_plano_essencial_bloqueia_prontuario(self, client, db_session, clinica_fake, auth_headers_a):
        clinica_fake["clinica"].plano = Plano.ESSENCIAL
        db_session.commit()
        resp = client.post(
            "/api/prontuarios", headers=auth_headers_a,
            json={"paciente_id": "qualquer", "anotacoes": "x"},
        )
        assert resp.status_code == 402  # FeatureBloqueada


# ===========================================================================
# Audit obrigatório (LGPD Art. 37)
# ===========================================================================

class TestProntuarioAudit:
    def test_create_e_read_geram_audit(self, client, db_session, auth_headers_a):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        client.get(f"/api/prontuarios/{pr['id']}", headers=auth_headers_a)

        logs = db_session.query(AuditLog).filter(AuditLog.recurso == "prontuario").all()
        acoes = {l.acao for l in logs}
        assert AcaoAudit.CREATE in acoes
        assert AcaoAudit.READ in acoes


# ===========================================================================
# ISOLAMENTO MULTI-TENANT (crítico) — sempre 404, nunca 403/200
# ===========================================================================

class TestProntuarioIsolamento:
    def test_obter_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        resp = client.get(f"/api/prontuarios/{pr['id']}", headers=auth_headers_b)
        assert resp.status_code == 404

    def test_atualizar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        resp = client.put(
            f"/api/prontuarios/{pr['id']}", headers=auth_headers_b,
            json={"anotacoes": "HACK"},
        )
        assert resp.status_code == 404

    def test_deletar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        pac = _criar_paciente(client, auth_headers_a)
        pr = _criar_prontuario(client, auth_headers_a, pac["id"]).json()
        # Token B é admin, então passa o gate de role e cai no 404 de tenant.
        resp = client.request(
            "DELETE",
            f"/api/prontuarios/{pr['id']}", headers=auth_headers_b,
            json={"motivo": "tentativa cross-tenant"},
        )
        assert resp.status_code == 404
        check = client.get(f"/api/prontuarios/{pr['id']}", headers=auth_headers_a)
        assert check.status_code == 200  # não foi apagado

    def test_profissional_de_outra_clinica_404(self, client, db_session, clinica_fake_b, auth_headers_a):
        from models import Profissional
        pac = _criar_paciente(client, auth_headers_a)
        prof_b = Profissional(clinica_id=clinica_fake_b["clinica"].id, nome="Dr. B")
        db_session.add(prof_b)
        db_session.commit()
        db_session.refresh(prof_b)
        resp = _criar_prontuario(client, auth_headers_a, pac["id"], profissional_id=prof_b.id)
        assert resp.status_code == 404
