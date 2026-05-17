"""Login + criação de token JWT pra usuário da clínica.

Hardenings:
- F13: timing attack mitigado com DUMMY_HASH (sempre roda bcrypt).
- F7: rate limit 5 tentativas/min/IP via slowapi.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db_dependency
from models import AcaoAudit, Usuario
from core.limiter import limiter
from core.security import DUMMY_HASH, criar_token, verificar_senha
from core import audit

router = APIRouter(prefix="/auth", tags=["auth"])


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
