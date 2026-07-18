"""FastAPI dependencies — autenticação e injeção de tenant.

Mudanças vs versão anterior:
- audit_context: extrai IP/UA/usuario pra logs LGPD (F9).
- usuario_atual: valida claim clinica_id no JWT contra valor atual do banco (F10/B5).
"""
import hmac

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db_dependency
from models import Clinica, Usuario
from core.security import decodificar_token, decodificar_token_admin, verificar_jti_admin


# ============================================================================
# Admin (você) — usa ADMIN_API_KEY ou JWT admin (sessão da UI admin)
# ============================================================================

def verificar_admin(
    x_admin_key: str | None = Header(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db_dependency),
) -> None:
    """Aceita X-Admin-Key (scripts/curl) OU Authorization: Bearer <jwt> com role=superadmin (UI admin).

    Sprint 6: Bearer JWT também é checado contra blacklist (admin_tokens_revogados).
    Tokens legados sem `jti` passam (compat).
    """
    # compare_digest: comparação em tempo constante (evita timing side-channel),
    # consistente com os paths de JWT.
    if x_admin_key and hmac.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        return
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = decodificar_token_admin(token)
        if payload and payload.get("role") == "superadmin":
            if not verificar_jti_admin(payload.get("jti"), db):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revogado")
            return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin auth requerida (X-Admin-Key ou JWT superadmin)")


# ============================================================================
# Tenant via JWT (usuário logado no dashboard)
# ============================================================================

def usuario_atual(
    authorization: str = Header(...),
    db: Session = Depends(get_db_dependency),
) -> Usuario:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decodificar_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado")

    usuario = db.query(Usuario).filter(Usuario.id == payload["sub"], Usuario.ativo == True).first()
    if not usuario:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não encontrado")

    # F10/B5: token diz clinica X, banco diz clinica Y → não confiar, exigir relogin
    if payload.get("clinica_id") != usuario.clinica_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token desatualizado — faça login novamente")
    return usuario


def clinica_atual(usuario: Usuario = Depends(usuario_atual)) -> Clinica:
    """Retorna a clínica do usuário logado. Use em TODA rota que mexe em dado de cliente."""
    if not usuario.clinica or not usuario.clinica.ativo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Clínica inativa")
    return usuario.clinica


# ============================================================================
# Audit context (F9) — coleta IP/UA/usuario pra log de LGPD
# ============================================================================

def audit_context(
    request: Request,
    usuario: Usuario = Depends(usuario_atual),
) -> dict:
    """Retorna kwargs prontos pra `audit.log(db, **ctx, acao=..., recurso=...)`."""
    return {
        "usuario_id": usuario.id,
        "clinica_id": usuario.clinica_id,
        "ip": request.client.host if request.client else None,
        "user_agent": (request.headers.get("user-agent") or "")[:200] or None,
    }


def audit_context_admin(request: Request) -> dict:
    """Versão pra endpoints admin. NÃO inclui clinica_id (caller passa explicitamente)."""
    return {
        "usuario_id": None,
        "ip": request.client.host if request.client else None,
        "user_agent": (request.headers.get("user-agent") or "")[:200] or None,
    }


# ============================================================================
# Tenant via API key (integrações automáticas — webhook, cron, etc)
# ============================================================================

def clinica_por_api_key(
    x_api_key: str = Header(...),
    db: Session = Depends(get_db_dependency),
) -> Clinica:
    clinica = db.query(Clinica).filter(Clinica.api_key == x_api_key, Clinica.ativo == True).first()
    if not clinica:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida")
    # Sprint 4: bypass via X-Api-Key bloqueado pós-trial (mesma regra do dashboard)
    from core.trial import trial_expirado
    if trial_expirado(clinica):
        raise HTTPException(
            status_code=402,
            detail={
                "erro": "trial_expirado",
                "expirou_em": clinica.trial_expira_em.isoformat() if clinica.trial_expira_em else None,
                "mensagem": "Seu trial de 7 dias acabou. Escolha um plano pra continuar 💛",
            },
        )
    return clinica


def requer_clinica_ativa(clinica: Clinica = Depends(clinica_atual)) -> Clinica:
    """Bloqueia mutations pós-trial. GETs continuam livres com clinica_atual."""
    from core.trial import trial_expirado
    if trial_expirado(clinica):
        raise HTTPException(
            status_code=402,
            detail={
                "erro": "trial_expirado",
                "expirou_em": clinica.trial_expira_em.isoformat() if clinica.trial_expira_em else None,
                "mensagem": "Seu trial de 7 dias acabou. Escolha um plano pra continuar 💛",
            },
        )
    return clinica
