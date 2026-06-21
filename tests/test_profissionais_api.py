"""Testes de profissionais — CRUD, RBAC de comissão e limite por plano."""
import pytest

from models import Plano, Usuario
from core.security import criar_token, hash_senha


def _criar(client, headers, nome="Dra. Camila", **over):
    body = {"nome": nome}
    body.update(over)
    return client.post("/api/profissionais", headers=headers, json=body)


def _operador_headers(db_session, clinica, email="operador@clinicaa.com"):
    op = Usuario(
        clinica_id=clinica.id, email=email,
        senha_hash=hash_senha("senha12345"), nome="Operador", role="operador",
    )
    db_session.add(op)
    db_session.commit()
    db_session.refresh(op)
    return {"Authorization": f"Bearer {criar_token(op.id, op.clinica_id, op.role)}"}


class TestProfissionalCRUD:
    def test_criar(self, client, auth_headers_a):
        r = _criar(client, auth_headers_a)
        assert r.status_code == 201
        assert r.json()["nome"] == "Dra. Camila"

    def test_listar(self, client, auth_headers_a):
        _criar(client, auth_headers_a, "Aaa")
        _criar(client, auth_headers_a, "Bbb")
        r = client.get("/api/profissionais", headers=auth_headers_a)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_atualizar(self, client, auth_headers_a):
        p = _criar(client, auth_headers_a).json()
        r = client.put(f"/api/profissionais/{p['id']}", headers=auth_headers_a, json={"nome": "Editado"})
        assert r.status_code == 200
        assert r.json()["nome"] == "Editado"

    def test_desativar_some_da_listagem(self, client, auth_headers_a):
        p = _criar(client, auth_headers_a).json()
        assert client.delete(f"/api/profissionais/{p['id']}", headers=auth_headers_a).status_code == 204
        ativos = client.get("/api/profissionais", headers=auth_headers_a).json()
        assert p["id"] not in [x["id"] for x in ativos]
        todos = client.get("/api/profissionais?incluir_inativos=true", headers=auth_headers_a).json()
        assert p["id"] in [x["id"] for x in todos]


class TestProfissionalComissaoRBAC:
    def test_admin_ve_comissao(self, client, auth_headers_a):
        p = _criar(client, auth_headers_a, comissao_percentual=30).json()
        assert p["comissao_percentual"] == 30

    def test_operador_nao_ve_comissao(self, client, db_session, clinica_fake, auth_headers_a):
        _criar(client, auth_headers_a, comissao_percentual=40)
        op_headers = _operador_headers(db_session, clinica_fake["clinica"])
        profs = client.get("/api/profissionais", headers=op_headers).json()
        assert profs and "comissao_percentual" not in profs[0]

    def test_operador_nao_pode_definir_comissao(self, client, db_session, clinica_fake):
        op_headers = _operador_headers(db_session, clinica_fake["clinica"], email="op2@clinicaa.com")
        r = client.post("/api/profissionais", headers=op_headers, json={"nome": "Profissional Teste", "comissao_percentual": 10})
        assert r.status_code == 403


class TestProfissionalLimitePlano:
    def test_essencial_bloqueia_terceiro(self, client, db_session, clinica_fake, auth_headers_a):
        from core.planos import invalidar_cache_contagem
        clinica_fake["clinica"].plano = Plano.ESSENCIAL
        db_session.commit()
        invalidar_cache_contagem(clinica_fake["clinica"].id, "profissionais")
        assert _criar(client, auth_headers_a, "P1").status_code == 201
        assert _criar(client, auth_headers_a, "P2").status_code == 201
        r = _criar(client, auth_headers_a, "P3")  # essencial = máx 2
        assert r.status_code == 402


class TestProfissionalIsolamento:
    def test_atualizar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        p = _criar(client, auth_headers_a).json()
        r = client.put(f"/api/profissionais/{p['id']}", headers=auth_headers_b, json={"nome": "HACK"})
        assert r.status_code == 404

    def test_desativar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        p = _criar(client, auth_headers_a).json()
        r = client.delete(f"/api/profissionais/{p['id']}", headers=auth_headers_b)
        assert r.status_code == 404
