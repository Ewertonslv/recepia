"""Testes da rota /auth/login + validação de JWT pelas dependências."""


class TestLogin:
    def test_login_sucesso(self, client, clinica_fake):
        resp = client.post(
            "/auth/login",
            json={"email": "admin@clinicaa.com", "senha": "senha12345"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["clinica_id"] == clinica_fake["clinica"].id
        assert body["clinica_nome"] == "Clinica Teste A"

    def test_login_senha_errada(self, client, clinica_fake):
        resp = client.post(
            "/auth/login",
            json={"email": "admin@clinicaa.com", "senha": "errada-totalmente"},
        )
        assert resp.status_code == 401
        assert "incorretos" in resp.json()["detail"].lower()

    def test_login_email_inexistente(self, client, clinica_fake):
        resp = client.post(
            "/auth/login",
            json={"email": "naoexiste@x.com", "senha": "qualquer"},
        )
        assert resp.status_code == 401

    def test_login_usuario_inativo(self, client, db_session, clinica_fake):
        clinica_fake["usuario"].ativo = False
        db_session.commit()
        resp = client.post(
            "/auth/login",
            json={"email": "admin@clinicaa.com", "senha": "senha12345"},
        )
        assert resp.status_code == 401

    def test_login_email_invalido_formato(self, client):
        resp = client.post(
            "/auth/login",
            json={"email": "nao-eh-email", "senha": "qualquer"},
        )
        assert resp.status_code == 422  # validation error do pydantic


class TestJWTProtection:
    def test_endpoint_sem_token_rejeita(self, client, clinica_fake):
        resp = client.get("/api/pacientes")
        assert resp.status_code == 422 or resp.status_code == 401

    def test_endpoint_com_token_invalido_rejeita(self, client):
        resp = client.get(
            "/api/pacientes",
            headers={"Authorization": "Bearer token-falso-completamente"},
        )
        assert resp.status_code == 401

    def test_endpoint_sem_bearer_prefix_rejeita(self, client, token_clinica_a):
        resp = client.get(
            "/api/pacientes",
            headers={"Authorization": token_clinica_a},  # sem "Bearer "
        )
        assert resp.status_code == 401

    def test_endpoint_com_token_valido_aceita(self, client, auth_headers_a):
        resp = client.get("/api/pacientes", headers=auth_headers_a)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_token_usuario_de_clinica_inativa_rejeita(self, client, db_session, clinica_fake, auth_headers_a):
        clinica_fake["clinica"].ativo = False
        db_session.commit()
        resp = client.get("/api/pacientes", headers=auth_headers_a)
        assert resp.status_code == 403
