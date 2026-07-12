"""Testes de /admin/clinicas — criar/listar/desativar (requer ADMIN_API_KEY)."""


class TestCriarClinica:
    def test_criar_clinica_sucesso(self, client, admin_headers):
        resp = client.post(
            "/admin/clinicas",
            headers=admin_headers,
            json={
                "nome": "Clinica Nova",
                "cnpj": "33333333000133",
                "admin_email": "novo@clinica.com",
                "admin_senha": "senha-forte-123",
                "admin_nome": "Admin Novo",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["nome"] == "Clinica Nova"
        assert body["ativo"] is True
        assert body["plano"] == "trial"
        assert body["api_key"]  # gerado automaticamente
        assert body["evolution_instance_name"].startswith("clinica-")
        assert body["admin_login_email"] == "novo@clinica.com"

    def test_criar_clinica_sem_admin_key_rejeita(self, client):
        resp = client.post(
            "/admin/clinicas",
            json={
                "nome": "X",
                "admin_email": "x@x.com",
                "admin_senha": "senha-1234",
            },
        )
        # falta header → 422 (Header required) ou 401
        assert resp.status_code in (401, 422)

    def test_criar_clinica_admin_key_errada(self, client):
        resp = client.post(
            "/admin/clinicas",
            headers={"X-Admin-Key": "errada"},
            json={
                "nome": "X",
                "admin_email": "x@x.com",
                "admin_senha": "senha-1234",
            },
        )
        assert resp.status_code == 401

    def test_mesmo_email_admin_pode_repetir_entre_clinicas(self, client, admin_headers, clinica_fake):
        # Multi-tenant: a unicidade do email do admin é POR CLÍNICA, não global
        # (api/clinicas.criar_clinica checa Usuario.clinica_id == nova_clinica.id).
        # Reusar o email de outra clínica ao criar uma nova é permitido.
        resp = client.post(
            "/admin/clinicas",
            headers=admin_headers,
            json={
                "nome": "Outra",
                "admin_email": "admin@clinicaa.com",  # mesmo email da clinica_fake
                "admin_senha": "senha-1234",
            },
        )
        assert resp.status_code == 201

    def test_criar_clinica_senha_curta_rejeita(self, client, admin_headers):
        resp = client.post(
            "/admin/clinicas",
            headers=admin_headers,
            json={
                "nome": "X",
                "admin_email": "x@y.com",
                "admin_senha": "curto",  # < 8 chars
            },
        )
        assert resp.status_code == 422

    def test_criar_clinica_aplica_templates_default(self, client, admin_headers, db_session):
        resp = client.post(
            "/admin/clinicas",
            headers=admin_headers,
            json={
                "nome": "Com templates",
                "admin_email": "ct@x.com",
                "admin_senha": "senha-1234",
            },
        )
        assert resp.status_code == 201
        clinica_id = resp.json()["id"]
        # Verifica que templates default foram criados
        from models import Configuracao
        configs = db_session.query(Configuracao).filter(
            Configuracao.clinica_id == clinica_id
        ).all()
        assert len(configs) >= 5
        chaves = {c.chave for c in configs}
        assert "msg_confirmacao_24h" in chaves
        assert "msg_cancelado" in chaves


class TestListarClinicas:
    def test_listar_vazio(self, client, admin_headers):
        resp = client.get("/admin/clinicas", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_listar_com_clinicas(self, client, admin_headers, clinica_fake, clinica_fake_b):
        resp = client.get("/admin/clinicas", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        nomes = {c["nome"] for c in body}
        assert "Clinica Teste A" in nomes
        assert "Clinica Teste B" in nomes

    def test_listar_sem_admin_key_rejeita(self, client):
        resp = client.get("/admin/clinicas")
        assert resp.status_code in (401, 422)


class TestDesativarClinica:
    def test_desativar_sucesso(self, client, admin_headers, clinica_fake, db_session):
        cid = clinica_fake["clinica"].id
        resp = client.post(f"/admin/clinicas/{cid}/desativar", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "desativada"

        db_session.expire_all()
        from models import Clinica
        c = db_session.query(Clinica).filter(Clinica.id == cid).first()
        assert c.ativo is False

    def test_desativar_inexistente_404(self, client, admin_headers):
        resp = client.post("/admin/clinicas/id-fake/desativar", headers=admin_headers)
        assert resp.status_code == 404
