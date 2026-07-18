"""Testes de hash de senha e JWT (core/security.py)."""
from datetime import datetime, timedelta


from core.security import (
    criar_token,
    decodificar_token,
    hash_senha,
    verificar_senha,
)


# ===========================================================================
# Hash de senha (bcrypt)
# ===========================================================================

class TestHashSenha:
    def test_hash_diferente_da_senha(self):
        senha = "minha-senha-123"
        h = hash_senha(senha)
        assert h != senha
        assert len(h) > 20  # bcrypt produz strings longas

    def test_hash_nao_deterministico(self):
        """Bcrypt usa salt aleatório, então mesma senha gera hashes diferentes."""
        h1 = hash_senha("senha")
        h2 = hash_senha("senha")
        assert h1 != h2

    def test_verificar_senha_correta(self):
        senha = "PaSsw0rd!@#"
        h = hash_senha(senha)
        assert verificar_senha(senha, h) is True

    def test_verificar_senha_errada(self):
        h = hash_senha("certa")
        assert verificar_senha("errada", h) is False

    def test_verificar_senha_case_sensitive(self):
        h = hash_senha("Senha123")
        assert verificar_senha("senha123", h) is False
        assert verificar_senha("Senha123", h) is True


# ===========================================================================
# JWT
# ===========================================================================

class TestJWT:
    def test_criar_token_retorna_string_jwt(self):
        token = criar_token("user-id-123", "clinica-id-abc", "admin")
        assert isinstance(token, str)
        assert token.count(".") == 2  # header.payload.signature

    def test_decodificar_token_valido(self):
        token = criar_token("user-1", "clinica-1", "admin")
        payload = decodificar_token(token)
        assert payload is not None
        assert payload["sub"] == "user-1"
        assert payload["clinica_id"] == "clinica-1"
        assert payload["role"] == "admin"
        assert "exp" in payload
        assert "iat" in payload

    def test_decodificar_token_invalido_retorna_none(self):
        assert decodificar_token("isso.nao.eh.um.jwt") is None
        assert decodificar_token("") is None
        assert decodificar_token("garbage") is None

    def test_decodificar_token_assinatura_invalida(self):
        token = criar_token("u", "c", "admin")
        # corrompe a assinatura
        partes = token.split(".")
        partes[2] = "AAAAAAAAAAAA"
        token_corrompido = ".".join(partes)
        assert decodificar_token(token_corrompido) is None

    def test_token_diferente_pra_usuarios_diferentes(self):
        t1 = criar_token("u1", "c1", "admin")
        t2 = criar_token("u2", "c1", "admin")
        assert t1 != t2

    def test_token_expirado_retorna_none(self, monkeypatch):
        """Cria token com expiração negativa e verifica que decodifica retorna None."""
        # força exp no passado
        from jose import jwt
        from config import settings
        payload = {
            "sub": "u1",
            "clinica_id": "c1",
            "role": "admin",
            "exp": datetime.utcnow() - timedelta(minutes=1),
            "iat": datetime.utcnow() - timedelta(minutes=10),
        }
        expired = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        assert decodificar_token(expired) is None
