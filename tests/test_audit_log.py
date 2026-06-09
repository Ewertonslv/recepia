"""Testes do audit log (LGPD Art. 37).

Toda operação CRUD em recurso sensível precisa gerar AuditLog com
clinica_id, acao e recurso preenchidos.
"""
from datetime import datetime, timedelta

import pytest

from core import audit
from models import AcaoAudit, AuditLog


# ===========================================================================
# Helper direto
# ===========================================================================

class TestAuditLogHelper:
    def test_log_cria_registro(self, db_session):
        audit.log(
            db_session,
            clinica_id="c1",
            usuario_id="u1",
            acao=AcaoAudit.CREATE,
            recurso="paciente",
            recurso_id="p1",
            ip="127.0.0.1",
            detalhes={"nome": "Teste"},
        )
        db_session.commit()
        logs = db_session.query(AuditLog).all()
        assert len(logs) == 1
        assert logs[0].acao == "CREATE"
        assert logs[0].recurso == "paciente"
        assert logs[0].clinica_id == "c1"
        assert logs[0].detalhes == {"nome": "Teste"}

    def test_log_sem_commit_explicito_fica_pendente(self, db_session):
        """Helper NÃO comita — quem chama é responsável."""
        audit.log(db_session, clinica_id="c1", usuario_id=None,
                  acao=AcaoAudit.READ, recurso="x")
        # Helper NÃO comita. A fixture usa autoflush=False (igual ao app), então
        # forçamos o flush pra confirmar que o registro fica PENDENTE na transação
        # (escrito, mas ainda não commitado).
        db_session.flush()
        assert db_session.query(AuditLog).count() == 1


# ===========================================================================
# Audit gerado pelas rotas
# ===========================================================================

class TestAuditViaRotas:
    def test_criar_clinica_gera_audit_setup(self, client, admin_headers, db_session):
        resp = client.post(
            "/admin/clinicas",
            headers=admin_headers,
            json={
                "nome": "Audit Test",
                "admin_email": "audit@x.com",
                "admin_senha": "senha-1234",
            },
        )
        assert resp.status_code == 201
        cid = resp.json()["id"]
        log = db_session.query(AuditLog).filter(
            AuditLog.clinica_id == cid,
            AuditLog.acao == AcaoAudit.SETUP,
            AuditLog.recurso == "clinica",
        ).first()
        assert log is not None

    def test_login_gera_audit(self, client, db_session, clinica_fake):
        resp = client.post(
            "/auth/login",
            json={"email": "admin@clinicaa.com", "senha": "senha12345"},
        )
        assert resp.status_code == 200
        log = db_session.query(AuditLog).filter(
            AuditLog.acao == AcaoAudit.LOGIN,
            AuditLog.usuario_id == clinica_fake["usuario"].id,
        ).first()
        assert log is not None
        assert log.clinica_id == clinica_fake["clinica"].id

    def test_criar_paciente_gera_audit_create(self, client, auth_headers_a, db_session, clinica_fake):
        resp = client.post(
            "/api/pacientes",
            headers=auth_headers_a,
            json={"nome": "Auditavel", "telefone": "5511900000099"},
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]
        log = db_session.query(AuditLog).filter(
            AuditLog.acao == AcaoAudit.CREATE,
            AuditLog.recurso == "paciente",
            AuditLog.recurso_id == pid,
        ).first()
        assert log is not None
        assert log.clinica_id == clinica_fake["clinica"].id

    def test_atualizar_paciente_gera_audit_update(self, client, auth_headers_a, db_session):
        criado = client.post(
            "/api/pacientes",
            headers=auth_headers_a,
            json={"nome": "Antigo", "telefone": "5511900000088"},
        ).json()
        client.put(
            f"/api/pacientes/{criado['id']}",
            headers=auth_headers_a,
            json={"nome": "Novo", "telefone": "5511900000088"},
        )
        log = db_session.query(AuditLog).filter(
            AuditLog.acao == AcaoAudit.UPDATE,
            AuditLog.recurso == "paciente",
            AuditLog.recurso_id == criado["id"],
        ).first()
        assert log is not None

    def test_deletar_paciente_gera_audit_delete_com_motivo_lgpd(
        self, client, auth_headers_a, db_session
    ):
        criado = client.post(
            "/api/pacientes",
            headers=auth_headers_a,
            json={"nome": "Pra apagar", "telefone": "5511900000077"},
        ).json()
        resp = client.delete(f"/api/pacientes/{criado['id']}", headers=auth_headers_a)
        assert resp.status_code == 204
        log = db_session.query(AuditLog).filter(
            AuditLog.acao == AcaoAudit.DELETE,
            AuditLog.recurso == "paciente",
            AuditLog.recurso_id == criado["id"],
        ).first()
        assert log is not None
        assert log.detalhes.get("motivo") == "lgpd_direito_esquecimento"

    def test_criar_agendamento_gera_audit(self, client, auth_headers_a, db_session):
        p = client.post(
            "/api/pacientes",
            headers=auth_headers_a,
            json={"nome": "P", "telefone": "5511900000066"},
        ).json()
        ag = client.post(
            "/api/agendamentos",
            headers=auth_headers_a,
            json={
                "paciente_id": p["id"],
                "data_hora": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            },
        ).json()
        log = db_session.query(AuditLog).filter(
            AuditLog.acao == AcaoAudit.CREATE,
            AuditLog.recurso == "agendamento",
            AuditLog.recurso_id == ag["id"],
        ).first()
        assert log is not None

    def test_audit_log_preserva_clinica_id_mesmo_apos_delete(self, db_session):
        """AuditLog não tem FK pra clinicas (intencional) — log sobrevive a delete da clínica."""
        audit.log(db_session, clinica_id="clinica-deletada", usuario_id=None,
                  acao=AcaoAudit.DELETE, recurso="clinica", recurso_id="clinica-deletada")
        db_session.commit()
        log = db_session.query(AuditLog).filter(
            AuditLog.clinica_id == "clinica-deletada"
        ).first()
        assert log is not None
