"""/health: 200 com banco respondendo, 503 (mesmo corpo) quando o ping falha."""
from sqlalchemy.exc import OperationalError


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "Recepia API", "db": "ok"}


def test_health_retorna_503_quando_banco_cai(client):
    from database import get_db_dependency
    from main import app

    class _DbQuebrado:
        def execute(self, *_a, **_kw):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def _db_quebrado():
        yield _DbQuebrado()

    app.dependency_overrides[get_db_dependency] = _db_quebrado
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "service": "Recepia API", "db": "error"}
