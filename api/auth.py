"""Login + criação de token JWT pra usuário da clínica.

Hardenings:
- F13: timing attack mitigado com DUMMY_HASH (sempre roda bcrypt).
- F7: rate limit 5 tentativas/min/IP via slowapi.
- Sprint 9: Google OAuth (ativado via GOOGLE_CLIENT_ID env var).
"""
import os
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from config import settings
from database import get_db_dependency
from models import AcaoAudit, Clinica, Usuario
from core.limiter import limiter
from core.security import DUMMY_HASH, criar_token, verificar_senha
from core import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Google OAuth config (opcional — desabilitado se vars não definidas) ────────
_G_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
_G_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
_G_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", "")
_GOOGLE_ENABLED  = bool(_G_CLIENT_ID and _G_CLIENT_SECRET and _G_REDIRECT_URI)

_G_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_G_TOKEN_URL = "https://oauth2.googleapis.com/token"
_G_INFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"


class LoginIn(BaseModel):
    email: EmailStr
    senha: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    clinica_id: str
    clinica_nome: str
    role: str
    usuario_nome: str


@router.post("/login", response_model=TokenOut)
@limiter.limit("5/minute")
def login(request: Request, payload: LoginIn, db: Session = Depends(get_db_dependency)):
    usuario = db.query(Usuario).filter(Usuario.email == payload.email, Usuario.ativo == True).first()

    # F13: sempre roda bcrypt (mesmo se user não existe) pra eliminar timing diff
    senha_hash_para_checar = usuario.senha_hash if usuario else DUMMY_HASH
    senha_ok = verificar_senha(payload.senha, senha_hash_para_checar)

    if not usuario or not senha_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email ou senha incorretos")

    token = criar_token(usuario.id, usuario.clinica_id, usuario.role)

    audit.log(
        db,
        clinica_id=usuario.clinica_id,
        usuario_id=usuario.id,
        acao=AcaoAudit.LOGIN,
        recurso="usuario",
        recurso_id=usuario.id,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
    )
    db.commit()

    return TokenOut(
        access_token=token,
        clinica_id=usuario.clinica_id,
        clinica_nome=usuario.clinica.nome,
        role=usuario.role,
        usuario_nome=usuario.nome,
    )


# ── Google OAuth ───────────────────────────────────────────────────────────────

@router.get("/google")
def google_login():
    """Redireciona para o consent screen do Google.
    Requer GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI no .env.
    """
    if not _GOOGLE_ENABLED:
        raise HTTPException(501, "Google OAuth não configurado. Defina GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI no .env.")
    import urllib.parse
    # CSRF: state aleatório guardado em cookie httponly e conferido no callback.
    state = secrets.token_urlsafe(24)
    params = urllib.parse.urlencode({
        "client_id": _G_CLIENT_ID,
        "redirect_uri": _G_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    })
    resp = RedirectResponse(f"{_G_AUTH_URL}?{params}")
    resp.set_cookie(
        "g_oauth_state", state, max_age=600, httponly=True,
        samesite="lax", secure=not settings.DEBUG, path="/auth/google",
    )
    return resp


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db_dependency),
):
    """Callback do Google OAuth. Troca code por token, autentica ou cria usuário."""
    if not _GOOGLE_ENABLED:
        raise HTTPException(501, "Google OAuth não configurado.")
    if error:
        return RedirectResponse(f"/dashboard/?google_error={error}")
    # CSRF: o state devolvido pelo Google tem que bater com o cookie do /google.
    cookie_state = request.cookies.get("g_oauth_state")
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        return RedirectResponse("/dashboard/?google_error=invalid_state")
    if not code:
        raise HTTPException(400, "Código de autorização ausente.")

    import httpx

    # 1. Trocar code por access_token + id_token
    try:
        resp = httpx.post(_G_TOKEN_URL, data={
            "code": code,
            "client_id": _G_CLIENT_ID,
            "client_secret": _G_CLIENT_SECRET,
            "redirect_uri": _G_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=10)
        resp.raise_for_status()
        tokens = resp.json()
    except Exception:
        return RedirectResponse("/dashboard/?google_error=token_exchange_failed")

    access_token_google = tokens.get("access_token")
    if not access_token_google:
        return RedirectResponse("/dashboard/?google_error=no_access_token")

    # 2. Buscar info do usuário
    try:
        info_resp = httpx.get(_G_INFO_URL, headers={"Authorization": f"Bearer {access_token_google}"}, timeout=10)
        info_resp.raise_for_status()
        info = info_resp.json()
    except Exception:
        return RedirectResponse("/dashboard/?google_error=userinfo_failed")

    google_id = info.get("sub")
    email = info.get("email")
    nome = info.get("name") or info.get("given_name") or ""

    if not google_id or not email:
        return RedirectResponse("/dashboard/?google_error=missing_user_info")

    # 3. Buscar usuário por google_id ou email
    usuario = db.query(Usuario).filter(
        Usuario.google_id == google_id,
        Usuario.ativo == True,
    ).first()

    if not usuario:
        # Tenta por email (vincula google_id a conta existente)
        usuario = db.query(Usuario).filter(
            Usuario.email == email,
            Usuario.ativo == True,
        ).first()
        if usuario:
            usuario.google_id = google_id
            if not usuario.nome and nome:
                usuario.nome = nome
            db.commit()

    if not usuario:
        return RedirectResponse("/dashboard/?google_error=usuario_nao_encontrado&email=" + email)

    jwt_token = criar_token(usuario.id, usuario.clinica_id, usuario.role)

    audit.log(
        db,
        clinica_id=usuario.clinica_id,
        usuario_id=usuario.id,
        acao=AcaoAudit.LOGIN,
        recurso="usuario",
        recurso_id=usuario.id,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        detalhes={"metodo": "google_oauth"},
    )
    db.commit()

    # 4. Redireciona pro dashboard com token no fragment (#) — não vai pro servidor
    clinica = db.query(Clinica).filter(Clinica.id == usuario.clinica_id).first()
    clinica_nome = clinica.nome if clinica else ""
    import urllib.parse
    params = urllib.parse.urlencode({
        "token": jwt_token,
        "clinica_nome": clinica_nome,
        "role": usuario.role,
        "usuario_nome": usuario.nome or "",
        "clinica_id": usuario.clinica_id,
    })
    return RedirectResponse(f"/dashboard/?google_login=1#{params}")
