"""Recepia API — entrypoint FastAPI.

Multi-tenant. Cada request precisa identificar a clínica via:
- JWT do usuário logado (dashboard) → Authorization: Bearer ...
- API key da clínica (integrações automáticas) → X-Api-Key: ...
- Admin master (você) → X-Admin-Key: ...
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import origens_cors
from core.deps import clinica_atual
from core.limiter import limiter
from core.timezones import agora_utc, from_utc_to_br
from database import get_db_dependency, init_db
from models import Agendamento, Clinica, Status
from api import (
    agendamentos, anamnese, auth, bloqueios, clinicas, configuracoes, documentos, fotos, horarios,
    odontograma, paciente_foto, pacientes, planos, planos_tratamento,
    procedimentos, profissionais, prontuarios, signup, webhooks, whatsapp,
)


BASE_DIR = Path(__file__).parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
LANDING_DIR = BASE_DIR / "landing"

app = FastAPI(
    title="Recepia API",
    description="Recepcionista IA pelo WhatsApp pra clínicas de estética.",
    version="0.2.0",
)


# Security headers — aplicados ANTES dos routers (Sprint 6)
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(self)"
        # HSTS só em produção HTTPS (toggle por env pra não quebrar dev http)
        if os.getenv("HTTPS_ENABLED", "false").lower() == "true":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS — origens específicas (F6)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origens_cors(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key", "X-Api-Key"],
)

# Rate limiting (F7) — slowapi
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

init_db()

# Routers
app.include_router(signup.router)          # /api/signup (Sprint 4 — self-serve, sem auth)
app.include_router(auth.router)            # /auth/login
app.include_router(clinicas.router_login)  # /admin/login (admin master)
app.include_router(clinicas.router)        # /admin/clinicas (admin only)
app.include_router(clinicas.router_me)     # /api/clinicas/me (clínica logada — Sprint 2)
app.include_router(pacientes.router)       # /api/pacientes (tenant scoped)
app.include_router(agendamentos.router)    # /api/agendamentos (tenant scoped)
app.include_router(configuracoes.router)   # /api/configuracoes (templates de msg)
app.include_router(horarios.router)        # /api/horarios (funcionamento por dia)
app.include_router(profissionais.router)   # /api/profissionais (Sprint 1)
app.include_router(planos.router)          # /api/plano (Sprint 1)
app.include_router(prontuarios.router)     # /api/prontuarios (Sprint 1 D3 — LGPD Art. 11)
app.include_router(planos_tratamento.router)  # /api/planos-tratamento (Sprint 3 — plano clínico)
app.include_router(fotos.router)           # /api/prontuarios/{id}/fotos (Sprint 1 D4 — biométrico)
app.include_router(paciente_foto.router)   # /api/pacientes/{id}/foto (Sprint 2 — avatar)
app.include_router(documentos.router)      # /api/pacientes/{id}/documentos/* (Sprint 2 — PDFs)
app.include_router(odontograma.router)     # /api/pacientes/{id}/odontograma (Sprint 2 — odonto only)
app.include_router(anamnese.router)        # /api/pacientes/{id}/anamnese (Sprint 8 — questionário clínico)
app.include_router(procedimentos.router)   # /api/procedimentos (Sprint 9)
app.include_router(bloqueios.router)       # /api/bloqueios (Sprint 9)
app.include_router(whatsapp.router)        # /api/whatsapp (clínica conecta seu WhatsApp)
app.include_router(webhooks.router)        # /api/webhook (Evolution callback)


@app.get("/health")
def health(db: Session = Depends(get_db_dependency)):
    """G7: health real — ping DB. Usado por k8s/uptime."""
    from sqlalchemy import text
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    return {
        "status": "ok" if db_ok else "degraded",
        "service": "Recepia API",
        "db": "ok" if db_ok else "error",
    }


# Dashboard estático — F1/B7 fix: StaticFiles tem proteção embutida contra traversal
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")

# Landing pública — servida na raiz quando o diretório existe
if LANDING_DIR.exists():
    @app.get("/", include_in_schema=False)
    def landing_root():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "index.html")

    @app.get("/cadastro", include_in_schema=False)
    def landing_cadastro():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "cadastro.html")

    @app.get("/entrar", include_in_schema=False)
    def landing_entrar():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "entrar.html")

    @app.get("/termos", include_in_schema=False)
    def landing_termos():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "termos.html")

    @app.get("/privacidade", include_in_schema=False)
    def landing_privacidade():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "privacidade.html")

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap():
        from fastapi.responses import FileResponse
        return FileResponse(LANDING_DIR / "sitemap.xml", media_type="application/xml")


@app.get("/api/relatorios/dashboard")
def relatorios_dashboard(
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    """Métricas do dia da clínica logada. Janela = dia BR atual, convertido pra UTC."""
    agora_br_now = from_utc_to_br(agora_utc())
    inicio_dia_br = agora_br_now.replace(hour=0, minute=0, second=0, microsecond=0)
    fim_dia_br = inicio_dia_br + timedelta(days=1)

    # Converte de volta pra UTC naive (formato armazenado no banco)
    from datetime import timezone as _tz
    inicio_utc = inicio_dia_br.astimezone(_tz.utc).replace(tzinfo=None)
    fim_utc = fim_dia_br.astimezone(_tz.utc).replace(tzinfo=None)

    base = db.query(Agendamento).filter(
        Agendamento.clinica_id == clinica.id,
        Agendamento.data_hora >= inicio_utc,
        Agendamento.data_hora < fim_utc,
    )
    total = base.count()
    confirmados = base.filter(Agendamento.status == Status.CONFIRMADO).count()
    pendentes = base.filter(Agendamento.status == Status.PENDENTE).count()
    taxa = int((confirmados / total) * 100) if total else 0

    return {
        "total_dia": total,
        "confirmados": confirmados,
        "pendentes": pendentes,
        "taxa_confirmacao": taxa,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
