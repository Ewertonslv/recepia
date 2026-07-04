"""Testes do boot guard de configuração (config.Settings).

F2: em produção (DEBUG=false) o webhook do Evolution não pode ficar sem HMAC —
o boot precisa falhar se EVOLUTION_WEBHOOK_SECRET não estiver setado.
"""
import pytest
from pydantic import ValidationError

from config import Settings

_BASE = dict(
    JWT_SECRET="x" * 40,
    ADMIN_API_KEY="admin-key-forte",
    EVOLUTION_API_KEY="",
)


def test_producao_sem_webhook_secret_falha_no_boot():
    with pytest.raises(ValidationError):
        Settings(**_BASE, DEBUG=False, EVOLUTION_WEBHOOK_SECRET="")


def test_producao_com_webhook_secret_ok():
    s = Settings(**_BASE, DEBUG=False, EVOLUTION_WEBHOOK_SECRET="segredo-hex-123")
    assert s.EVOLUTION_WEBHOOK_SECRET == "segredo-hex-123"


def test_dev_pode_rodar_sem_webhook_secret():
    s = Settings(**_BASE, DEBUG=True, EVOLUTION_WEBHOOK_SECRET="")
    assert s.DEBUG is True


def test_jwt_secret_curto_rejeitado():
    with pytest.raises(ValidationError):
        Settings(JWT_SECRET="curto", ADMIN_API_KEY="ok", DEBUG=True,
                 EVOLUTION_WEBHOOK_SECRET="")


def test_valores_inseguros_rejeitados():
    with pytest.raises(ValidationError):
        Settings(JWT_SECRET="change-me-please-change-me-please", ADMIN_API_KEY="changeme",
                 DEBUG=True, EVOLUTION_WEBHOOK_SECRET="")
