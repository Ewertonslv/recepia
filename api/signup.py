"""Self-signup público — cria clínica + admin + trial 7 dias.

Sem auth. Rate limited (3/hour/IP). LGPD: registra aceite_termos timestamp.
"""
from datetime import datetime, date
from core.timezones import agora_utc
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from core import audit
from core.especialidades import listar_slugs
from core.limiter import limiter
from core.phones import TelefoneInvalido, normalizar as normalizar_telefone
from core.security import criar_token, hash_senha
from core.trial import data_expira_trial, dias_restantes
from database import get_db_dependency
from models import AcaoAudit, Clinica, Plano, Usuario
from seeds import aplicar_configuracoes_default, aplicar_horarios_default

router = APIRouter(prefix="/api", tags=["signup"])


class SignupIn(BaseModel):
    nome_clinica: str = Field(..., min_length=2, max_length=120)
    especialidade: str
    nome_responsavel: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    telefone: str = Field(..., min_length=10)
    senha: str = Field(..., min_length=8, max_length=128)
    aceito_termos: bool

    @field_validator("especialidade")
    @classmethod
    def _esp(cls, v):
        if v not in listar_slugs():
            raise ValueError(f"Especialidade inválida. Use uma de: {listar_slugs()}")
        return v

    @field_validator("aceito_termos")
    @classmethod
    def _termos(cls, v):
        if not v:
            raise ValueError("Você precisa aceitar os termos pra continuar")
        return v


class SignupOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    clinica_id: str
    clinica_nome: str
    trial_expira_em: date
    dias_restantes: int
    role: str = "admin"
    usuario_nome: str


@router.post("/signup", response_model=SignupOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/hour")
def signup_publico(request: Request, payload: SignupIn, db: Session = Depends(get_db_dependency)):
    # normaliza telefone
    try:
        telefone_norm = normalizar_telefone(payload.telefone)
    except TelefoneInvalido as e:
        raise HTTPException(400, f"Telefone inválido: {e}")

    # email único entre signups públicos (anti-farm trial)
    email_lower = payload.email.lower().strip()
    duplicado = (
        db.query(Usuario)
        .join(Clinica, Usuario.clinica_id == Clinica.id)
        .filter(
            Usuario.email == email_lower,
            Usuario.role == "admin",
            Clinica.origem_cadastro == "signup_publico",
        )
        .first()
    )
    # Sprint 6: mensagem GENÉRICA — não distingue email vs telefone (evita enumeração)
    _DUP_MSG = "Esses dados já têm uma conta. Entre em vez de criar nova."
    if duplicado:
        raise HTTPException(status.HTTP_409_CONFLICT, _DUP_MSG)

    # telefone único entre signups públicos (2ª barreira)
    tel_dup = (
        db.query(Usuario)
        .join(Clinica, Usuario.clinica_id == Clinica.id)
        .filter(
            Usuario.telefone == telefone_norm,
            Usuario.role == "admin",
            Clinica.origem_cadastro == "signup_publico",
        )
        .first()
    )
    if tel_dup:
        raise HTTPException(status.HTTP_409_CONFLICT, _DUP_MSG)

    # cria clínica em trial 7d
    clinica = Clinica(
        nome=payload.nome_clinica,
        plano=Plano.TRIAL,
        especialidade=payload.especialidade,
        trial_expira_em=data_expira_trial(),
        origem_cadastro="signup_publico",
        ativo=True,
    )
    db.add(clinica)
    db.flush()
    db.refresh(clinica)
    clinica.evolution_instance_name = f"clinica-{clinica.id[:8]}"

    usuario = Usuario(
        clinica_id=clinica.id,
        email=email_lower,
        nome=payload.nome_responsavel,
        senha_hash=hash_senha(payload.senha),
        telefone=telefone_norm,
        aceitou_termos_em=agora_utc(),
        role="admin",
    )
    db.add(usuario)
    db.flush()

    aplicar_configuracoes_default(db, clinica.id)
    aplicar_horarios_default(db, clinica.id)

    ip = request.client.host if request.client else None
    ua = (request.headers.get("user-agent") or "")[:200] or None
    audit.log(
        db, usuario_id=usuario.id, clinica_id=clinica.id, ip=ip, user_agent=ua,
        acao=AcaoAudit.SETUP, recurso="clinica", recurso_id=clinica.id,
        detalhes={
            "nome": clinica.nome, "especialidade": clinica.especialidade,
            "origem": "signup_publico", "trial_expira_em": clinica.trial_expira_em.isoformat(),
        },
    )
    audit.log(
        db, usuario_id=usuario.id, clinica_id=clinica.id, ip=ip, user_agent=ua,
        acao=AcaoAudit.LOGIN, recurso="usuario", recurso_id=usuario.id, detalhes={"via": "signup"},
    )
    db.commit()

    token = criar_token(usuario.id, clinica.id, usuario.role)
    return SignupOut(
        access_token=token,
        clinica_id=clinica.id,
        clinica_nome=clinica.nome,
        trial_expira_em=clinica.trial_expira_em,
        dias_restantes=dias_restantes(clinica),
        usuario_nome=usuario.nome or "",
    )
