"""Testes do self-signup público (cria clínica + admin + trial 7 dias)."""
import pytest


def _payload(**over):
    base = {
        "nome_clinica": "Clínica Nova",
        "especialidade": "odonto",
        "nome_responsavel": "Dra. Ana",
        "email": "ana@novaclinica.com",
        "telefone": "11999990000",
        "senha": "senhaforte123",
        "aceito_termos": True,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _sem_rate_limit():
    """Signup é 3/hora/IP — desliga o limiter pra os testes não estourarem 429."""
    from core.limiter import limiter
    anterior = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = anterior


class TestSignup:
    def test_signup_cria_clinica_trial(self, client):
        r = client.post("/api/signup", json=_payload())
        assert r.status_code == 201
        b = r.json()
        assert b["access_token"]
        assert b["role"] == "admin"
        assert b["dias_restantes"] == 7
        assert b["clinica_nome"] == "Clínica Nova"

    def test_login_funciona_apos_signup(self, client):
        client.post("/api/signup", json=_payload(email="login@x.com", telefone="11988880000"))
        r = client.post("/auth/login", json={"email": "login@x.com", "senha": "senhaforte123"})
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_email_duplicado_409(self, client):
        client.post("/api/signup", json=_payload(email="dup@x.com", telefone="11900000001"))
        r = client.post("/api/signup", json=_payload(email="dup@x.com", telefone="11900000002"))
        assert r.status_code == 409

    def test_telefone_duplicado_409(self, client):
        client.post("/api/signup", json=_payload(email="a@x.com", telefone="11955550000"))
        r = client.post("/api/signup", json=_payload(email="b@x.com", telefone="11955550000"))
        assert r.status_code == 409

    def test_especialidade_invalida_422(self, client):
        r = client.post("/api/signup", json=_payload(especialidade="veterinaria"))
        assert r.status_code == 422

    def test_sem_aceitar_termos_422(self, client):
        r = client.post("/api/signup", json=_payload(aceito_termos=False))
        assert r.status_code == 422

    def test_senha_curta_422(self, client):
        r = client.post("/api/signup", json=_payload(senha="123"))
        assert r.status_code == 422
