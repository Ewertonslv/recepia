"""Fixtures globais — banco SQLite in-memory + TestClient FastAPI.

Isolamento total entre testes: cada teste recebe um engine novo
e um TestClient com override do `get_db_dependency`.
"""
import os
import sys
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Garante que importamos a partir da raiz `codigo/`
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Define settings dummy ANTES de importar qualquer módulo do app
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-32-bytes-long-enough!!"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["GROQ_API_KEY"] = ""  # força fallback regex no processor
os.environ["EVOLUTION_API_URL"] = "http://fake-evolution"
os.environ["EVOLUTION_API_KEY"] = "fake-key"
# Secret do webhook setado → suíte roda no modo "produção-like" (DEBUG=false),
# então o webhook EXIGE assinatura HMAC válida (ver tests/test_webhooks.py).
os.environ["EVOLUTION_WEBHOOK_SECRET"] = "test-webhook-secret-0123456789abcdef"


# ===========================================================================
# DB Fixtures
# ===========================================================================

@pytest.fixture(scope="function")
def engine():
    """Engine SQLite in-memory novo por teste — isolamento total."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Importa models APÓS configurar env
    from database import Base
    import models  # noqa: F401 — registra classes
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


@pytest.fixture(scope="function")
def db_session(engine) -> Generator[Session, None, None]:
    """Sessão SQLAlchemy isolada por teste."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ===========================================================================
# FastAPI TestClient com override de DB
# ===========================================================================

@pytest.fixture(scope="function")
def client(engine, monkeypatch) -> Generator[TestClient, None, None]:
    """TestClient com get_db sobrescrito pra usar SQLite in-memory.

    Também mocka WhatsAppService pra não chamar Evolution de verdade.
    """
    # Mock do WhatsApp ANTES do app importar os routers
    from services import whatsapp as ws_module

    class FakeWhatsAppService:
        def __init__(self):
            self.sent = []

        def criar_instancia(self, instance_name):
            return {"success": True, "data": {"instance_name": instance_name}}

        def obter_qrcode(self, instance_name):
            return {"success": True, "base64": "fake-qr-base64", "pairing_code": "ABCD-EFGH"}

        def status_instancia(self, instance_name):
            return {"success": True, "conectado": True, "estado": "open"}

        def desconectar(self, instance_name):
            return {"success": True, "status": 200}

        def enviar_mensagem(self, instance_name, telefone, mensagem):
            self.sent.append({"instance": instance_name, "to": telefone, "msg": mensagem})
            return {"success": True, "data": {"messageId": "fake-msg-id"}}

        def configurar_webhook(self, instance_name, url_webhook):
            return {"success": True}

    monkeypatch.setattr(ws_module, "WhatsAppService", FakeWhatsAppService)

    # Override do get_db
    from database import get_db_dependency
    from main import app

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_dependency] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ===========================================================================
# Fixtures de domínio
# ===========================================================================

@pytest.fixture
def clinica_fake(db_session):
    """Cria uma clínica + usuário admin direto no DB. Retorna dict com refs."""
    from models import Clinica, Usuario
    from core.security import hash_senha
    from seeds import aplicar_configuracoes_default

    clinica = Clinica(
        nome="Clinica Teste A", cnpj="11111111000111",
        # Override: testes de CRUD/isolamento criam paciente só com nome/telefone.
        # Desliga a obrigatoriedade de data_nascimento/cpf da especialidade (odonto).
        config_paciente={"campos": {"data_nascimento": {"obrigatorio": False},
                                    "cpf": {"obrigatorio": False}}},
    )
    db_session.add(clinica)
    db_session.flush()
    clinica.evolution_instance_name = f"clinica-{clinica.id[:8]}"

    usuario = Usuario(
        clinica_id=clinica.id,
        email="admin@clinicaa.com",
        senha_hash=hash_senha("senha12345"),
        nome="Admin A",
        role="admin",
    )
    db_session.add(usuario)
    aplicar_configuracoes_default(db_session, clinica.id)
    db_session.commit()
    db_session.refresh(clinica)
    db_session.refresh(usuario)
    return {"clinica": clinica, "usuario": usuario, "senha": "senha12345"}


@pytest.fixture
def clinica_fake_b(db_session):
    """Segunda clínica pra testar isolamento multi-tenant."""
    from models import Clinica, Usuario
    from core.security import hash_senha
    from seeds import aplicar_configuracoes_default

    clinica = Clinica(
        nome="Clinica Teste B", cnpj="22222222000122",
        config_paciente={"campos": {"data_nascimento": {"obrigatorio": False},
                                    "cpf": {"obrigatorio": False}}},
    )
    db_session.add(clinica)
    db_session.flush()
    clinica.evolution_instance_name = f"clinica-{clinica.id[:8]}"

    usuario = Usuario(
        clinica_id=clinica.id,
        email="admin@clinicab.com",
        senha_hash=hash_senha("senha12345"),
        nome="Admin B",
        role="admin",
    )
    db_session.add(usuario)
    aplicar_configuracoes_default(db_session, clinica.id)
    db_session.commit()
    db_session.refresh(clinica)
    db_session.refresh(usuario)
    return {"clinica": clinica, "usuario": usuario, "senha": "senha12345"}


@pytest.fixture
def token_clinica_a(clinica_fake):
    """JWT pronto pra usar como Bearer da Clinica A."""
    from core.security import criar_token
    u = clinica_fake["usuario"]
    return criar_token(u.id, u.clinica_id, u.role)


@pytest.fixture
def token_clinica_b(clinica_fake_b):
    """JWT da Clinica B."""
    from core.security import criar_token
    u = clinica_fake_b["usuario"]
    return criar_token(u.id, u.clinica_id, u.role)


@pytest.fixture
def auth_headers_a(token_clinica_a):
    return {"Authorization": f"Bearer {token_clinica_a}"}


@pytest.fixture
def auth_headers_b(token_clinica_b):
    return {"Authorization": f"Bearer {token_clinica_b}"}


@pytest.fixture
def admin_headers():
    return {"X-Admin-Key": "test-admin-key"}
