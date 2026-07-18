"""Admin: criar e gerenciar clínicas (tenants).

Usa ADMIN_API_KEY (você) OU JWT admin (sessão da UI admin). Não exposto aos usuários finais.

Hardenings:
- F8: api_key NÃO é retornada em listagem. Só em CREATE e em rotate (uma vez).
- Sprint 1 D5: login admin retorna JWT 2h pra UI admin; senha de reset gerada server-side.
"""
import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from core.timezones import agora_utc
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import case
from sqlalchemy.orm import Session

from config import settings
from database import get_db_dependency
from models import AcaoAudit, AdminTokenRevogado, AuditLog, Clinica, Plano, Usuario
from core.deps import audit_context, audit_context_admin, clinica_atual, requer_clinica_ativa, usuario_atual, verificar_admin
from core.especialidades import config_efetiva, get_especialidade, listar_slugs, to_dict as especialidade_to_dict
from core.foto_storage import MAX_UPLOAD_BYTES, FotoError, deletar_logo, ler_logo, salvar_logo
from core.limiter import limiter
from core.planos import LIMITES, snapshot_uso
from core.security import criar_token_admin, gerar_senha_aleatoria, hash_senha
from core import audit
from seeds import aplicar_configuracoes_default, aplicar_horarios_default

# Router SEM dependency global — pro endpoint /admin/login (que valida a key, não exige token)
router_login = APIRouter(prefix="/admin", tags=["admin"])

router = APIRouter(prefix="/admin/clinicas", tags=["admin"], dependencies=[Depends(verificar_admin)])

# Router pra clínica logada (usa JWT do dashboard, não admin)
router_me = APIRouter(prefix="/api/clinicas/me", tags=["clinica-me"])


class CriarClinicaIn(BaseModel):
    nome: str = Field(..., min_length=2)
    cnpj: str | None = None
    plano: str = Plano.TRIAL
    especialidade: str = "odonto"   # define labels + campos + documentos disponíveis
    # Usuário admin inicial da clínica
    admin_email: EmailStr
    admin_senha: str = Field(..., min_length=8)
    admin_nome: str | None = None


class ClinicaOut(BaseModel):
    """F8: SEM api_key na listagem padrão."""
    id: str
    nome: str
    cnpj: str | None
    plano: str
    especialidade: str = "odonto"
    ativo: bool
    evolution_instance_name: str | None
    evolution_conectado: bool
    admin_login_email: str | None

    class Config:
        from_attributes = True


class ClinicaCriadaOut(ClinicaOut):
    """Resposta de CREATE — única ocasião em que api_key é mostrada."""
    api_key: str


class ApiKeyRotacionadaOut(BaseModel):
    api_key: str


@router.post("", response_model=ClinicaCriadaOut, status_code=status.HTTP_201_CREATED)
def criar_clinica(
    payload: CriarClinicaIn,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """Onboarding de nova clínica. Cria tenant + usuário admin + templates default."""
    if payload.especialidade not in listar_slugs():
        raise HTTPException(422, f"Especialidade inválida. Use uma de: {listar_slugs()}")
    clinica = Clinica(
        nome=payload.nome,
        cnpj=payload.cnpj,
        plano=payload.plano,
        especialidade=payload.especialidade,
    )
    db.add(clinica)
    db.flush()
    db.refresh(clinica)  # B6: garante id materializado
    clinica.evolution_instance_name = f"clinica-{clinica.id[:8]}"

    # Email único POR CLÍNICA (B4) — checa no contexto certo
    if (
        db.query(Usuario)
        .filter(Usuario.clinica_id == clinica.id, Usuario.email == payload.admin_email)
        .first()
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email já cadastrado nesta clínica")

    usuario_admin = Usuario(
        clinica_id=clinica.id,
        email=payload.admin_email,
        senha_hash=hash_senha(payload.admin_senha),
        nome=payload.admin_nome or payload.nome,
        role="admin",
    )
    db.add(usuario_admin)
    db.flush()

    aplicar_configuracoes_default(db, clinica.id)
    aplicar_horarios_default(db, clinica.id)

    audit.log(
        db, **ctx,
        clinica_id=clinica.id,
        acao=AcaoAudit.SETUP,
        recurso="clinica",
        recurso_id=clinica.id,
        detalhes={"nome": clinica.nome, "admin_email": payload.admin_email},
    )

    db.commit()
    db.refresh(clinica)

    return ClinicaCriadaOut(
        id=clinica.id,
        nome=clinica.nome,
        cnpj=clinica.cnpj,
        plano=clinica.plano,
        especialidade=clinica.especialidade,
        ativo=clinica.ativo,
        api_key=clinica.api_key,
        evolution_instance_name=clinica.evolution_instance_name,
        evolution_conectado=clinica.evolution_conectado,
        admin_login_email=usuario_admin.email,
    )


@router.get("", response_model=list[ClinicaOut])
def listar_clinicas(db: Session = Depends(get_db_dependency)):
    clinicas = db.query(Clinica).all()
    out = []
    for c in clinicas:
        admin = next((u for u in c.usuarios if u.role == "admin"), None)
        out.append(ClinicaOut(
            id=c.id, nome=c.nome, cnpj=c.cnpj, plano=c.plano,
            especialidade=getattr(c, "especialidade", "odonto"),
            ativo=c.ativo,
            evolution_instance_name=c.evolution_instance_name,
            evolution_conectado=c.evolution_conectado,
            admin_login_email=admin.email if admin else None,
        ))
    return out


@router.post("/{clinica_id}/rotate-api-key", response_model=ApiKeyRotacionadaOut)
def rotacionar_api_key(
    clinica_id: str,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """F8: API key nova só é retornada UMA vez aqui. Use com cuidado."""
    import uuid
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    nova = str(uuid.uuid4())
    clinica.api_key = nova
    audit.log(
        db, **ctx,
        clinica_id=clinica.id,
        acao=AcaoAudit.UPDATE,
        recurso="api_key",
        recurso_id=clinica.id,
        detalhes={"acao": "rotacionar"},
    )
    db.commit()
    return ApiKeyRotacionadaOut(api_key=nova)


@router.post("/{clinica_id}/desativar")
def desativar_clinica(
    clinica_id: str,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    clinica.ativo = False
    audit.log(
        db, **ctx,
        clinica_id=clinica.id,
        acao=AcaoAudit.UPDATE,
        recurso="clinica",
        recurso_id=clinica.id,
        detalhes={"acao": "desativar"},
    )
    db.commit()
    return {"status": "desativada"}


# ============================================================================
# Admin Master — login + endpoints de gestão (Sprint 1 D5)
# ============================================================================

class AdminLoginIn(BaseModel):
    admin_key: str


class AdminTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 7200  # 2h em segundos


@router_login.post("/login", response_model=AdminTokenOut)
@limiter.limit("5/minute")
def login_admin(request: Request, payload: AdminLoginIn,
                db: Session = Depends(get_db_dependency)):
    """Valida X-Admin-Key e retorna JWT (TTL 2h) pra UI admin usar via Bearer."""
    import hmac as _hmac
    # compare_digest: comparação com != vaza timing byte-a-byte da chave mestra.
    if not _hmac.compare_digest(
        (payload.admin_key or "").encode(), settings.ADMIN_API_KEY.encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin key inválida")
    # Login com a credencial de maior privilégio precisa de trilha de auditoria.
    audit.log(
        db, clinica_id=None, usuario_id=None,
        acao=AcaoAudit.LOGIN, recurso="admin_master", recurso_id=None,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        detalhes={"metodo": "admin_key"},
    )
    db.commit()
    return AdminTokenOut(access_token=criar_token_admin())


@router_login.post("/logout")
def logout_admin(
    authorization: str = Header(...),
    db: Session = Depends(get_db_dependency),
):
    """Sprint 6: invalida JWT admin atual. Coloca jti na blacklist.

    Idempotente — se token já expirado ou sem jti (legado), retorna sem erro.
    """
    from core.security import decodificar_token_admin
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer requerido")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decodificar_token_admin(token)
    if not payload:
        return {"status": "já expirado"}
    jti = payload.get("jti")
    exp = payload.get("exp", 0)
    if jti:
        # Idempotente: se já está revogado, ignora insert (unique constraint)
        ja = db.query(AdminTokenRevogado.id).filter(AdminTokenRevogado.jti == jti).first()
        if not ja:
            db.add(AdminTokenRevogado(
                jti=jti,
                expira_em=datetime.utcfromtimestamp(exp),
            ))
            db.commit()
    return {"status": "revogado"}


class AlterarPlanoIn(BaseModel):
    novo_plano: str
    motivo: str | None = Field(None, max_length=300)


class AlterarEspecialidadeIn(BaseModel):
    nova_especialidade: str
    motivo: str | None = Field(None, max_length=300)


@router.post("/{clinica_id}/especialidade", response_model=ClinicaOut)
def alterar_especialidade(
    clinica_id: str,
    payload: AlterarEspecialidadeIn,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    if payload.nova_especialidade not in listar_slugs():
        raise HTTPException(422, f"Especialidade inválida. Use uma de: {listar_slugs()}")
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    anterior = clinica.especialidade
    if anterior == payload.nova_especialidade:
        raise HTTPException(422, "Já está nessa especialidade")
    clinica.especialidade = payload.nova_especialidade
    audit.log(
        db, **ctx,
        clinica_id=clinica.id,
        acao=AcaoAudit.UPDATE,
        recurso="clinica",
        recurso_id=clinica.id,
        detalhes={
            "acao": "alterar_especialidade",
            "anterior": anterior,
            "nova": payload.nova_especialidade,
            "motivo": payload.motivo,
        },
    )
    db.commit()
    db.refresh(clinica)
    admin = next((u for u in clinica.usuarios if u.role == "admin"), None)
    return ClinicaOut(
        id=clinica.id, nome=clinica.nome, cnpj=clinica.cnpj, plano=clinica.plano,
        especialidade=clinica.especialidade,
        ativo=clinica.ativo,
        evolution_instance_name=clinica.evolution_instance_name,
        evolution_conectado=clinica.evolution_conectado,
        admin_login_email=admin.email if admin else None,
    )


@router.post("/{clinica_id}/plano", response_model=ClinicaOut)
def alterar_plano(
    clinica_id: str,
    payload: AlterarPlanoIn,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    if payload.novo_plano not in LIMITES:
        raise HTTPException(422, f"Plano inválido. Use um de: {sorted(LIMITES.keys())}")
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    anterior = clinica.plano
    clinica.plano = payload.novo_plano
    if payload.novo_plano != "trial":
        clinica.trial_expira_em = None
    audit.log(
        db, **ctx,
        clinica_id=clinica.id,
        acao=AcaoAudit.UPDATE,
        recurso="clinica",
        recurso_id=clinica.id,
        detalhes={
            "acao": "alterar_plano",
            "plano_anterior": anterior,
            "plano_novo": payload.novo_plano,
            "motivo": payload.motivo,
        },
    )
    db.commit()
    db.refresh(clinica)
    admin = next((u for u in clinica.usuarios if u.role == "admin"), None)
    return ClinicaOut(
        id=clinica.id, nome=clinica.nome, cnpj=clinica.cnpj, plano=clinica.plano,
        ativo=clinica.ativo,
        evolution_instance_name=clinica.evolution_instance_name,
        evolution_conectado=clinica.evolution_conectado,
        admin_login_email=admin.email if admin else None,
    )


@router.post("/{clinica_id}/ativar")
def ativar_clinica(
    clinica_id: str,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    clinica.ativo = True
    audit.log(
        db, **ctx,
        clinica_id=clinica.id,
        acao=AcaoAudit.UPDATE,
        recurso="clinica",
        recurso_id=clinica.id,
        detalhes={"acao": "ativar"},
    )
    db.commit()
    return {"status": "ativa"}


@router.get("/{clinica_id}/uso")
def uso_clinica(
    clinica_id: str,
    db: Session = Depends(get_db_dependency),
):
    """Snapshot de uso da clínica (reaproveita lógica do dashboard sem precisar de JWT do tenant)."""
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    return snapshot_uso(db, clinica)


class UsuarioOut(BaseModel):
    id: str
    email: str
    nome: str | None
    role: str
    ativo: bool
    criado_em: datetime

    class Config:
        from_attributes = True


@router.get("/{clinica_id}/usuarios", response_model=list[UsuarioOut])
def listar_usuarios(
    clinica_id: str,
    db: Session = Depends(get_db_dependency),
):
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    return (
        db.query(Usuario)
        .filter(Usuario.clinica_id == clinica_id)
        .order_by(Usuario.criado_em.asc())
        .all()
    )


class CriarUsuarioIn(BaseModel):
    email: EmailStr
    nome: str | None = None
    role: str = "operador"  # admin | operador


class CriarUsuarioOut(UsuarioOut):
    senha_inicial: str  # mostrada UMA vez — gerada server-side


@router.post("/{clinica_id}/usuarios", response_model=CriarUsuarioOut, status_code=status.HTTP_201_CREATED)
def criar_usuario(
    clinica_id: str,
    payload: CriarUsuarioIn,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    if payload.role not in {"admin", "operador"}:
        raise HTTPException(422, "role deve ser admin ou operador")
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    # email único POR clínica (B4)
    if db.query(Usuario).filter(Usuario.clinica_id == clinica_id, Usuario.email == payload.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email já cadastrado nesta clínica")
    senha = gerar_senha_aleatoria()
    u = Usuario(
        clinica_id=clinica_id,
        email=payload.email,
        nome=payload.nome or payload.email.split("@")[0],
        role=payload.role,
        senha_hash=hash_senha(senha),
    )
    db.add(u)
    db.flush()
    audit.log(
        db, **ctx,
        clinica_id=clinica_id,
        acao=AcaoAudit.CREATE,
        recurso="usuario",
        recurso_id=u.id,
        detalhes={"email": payload.email, "role": payload.role, "por_admin_master": True},
    )
    db.commit()
    db.refresh(u)
    return CriarUsuarioOut(
        id=u.id, email=u.email, nome=u.nome, role=u.role,
        ativo=u.ativo, criado_em=u.criado_em, senha_inicial=senha,
    )


class ResetSenhaOut(BaseModel):
    senha_nova: str  # mostrada UMA vez — admin precisa transmitir ao user fora da banda


@router.post("/{clinica_id}/usuarios/{uid}/reset-senha", response_model=ResetSenhaOut)
def reset_senha(
    clinica_id: str,
    uid: str,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """Gera senha aleatória server-side. Admin NÃO escolhe — evita senha fraca."""
    u = (
        db.query(Usuario)
        .filter(Usuario.id == uid, Usuario.clinica_id == clinica_id)
        .first()
    )
    if not u:
        raise HTTPException(404, "Usuário não encontrado")
    senha = gerar_senha_aleatoria()
    u.senha_hash = hash_senha(senha)
    audit.log(
        db, **ctx,
        clinica_id=clinica_id,
        acao=AcaoAudit.UPDATE,
        recurso="usuario",
        recurso_id=u.id,
        detalhes={"acao": "reset_senha", "por_admin_master": True},
    )
    db.commit()
    return ResetSenhaOut(senha_nova=senha)


@router.post("/{clinica_id}/usuarios/{uid}/desativar")
def desativar_usuario(
    clinica_id: str,
    uid: str,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    u = (
        db.query(Usuario)
        .filter(Usuario.id == uid, Usuario.clinica_id == clinica_id)
        .first()
    )
    if not u:
        raise HTTPException(404, "Usuário não encontrado")
    u.ativo = False
    audit.log(
        db, **ctx,
        clinica_id=clinica_id,
        acao=AcaoAudit.UPDATE,
        recurso="usuario",
        recurso_id=u.id,
        detalhes={"acao": "desativar", "por_admin_master": True},
    )
    db.commit()
    return {"status": "desativado"}


@router.get("/{clinica_id}/audit")
def listar_audit(
    clinica_id: str,
    limit: int = 100,
    db: Session = Depends(get_db_dependency),
):
    """Últimas N entradas do audit_log da clínica. Útil pra investigar incidentes."""
    if not db.query(Clinica.id).filter(Clinica.id == clinica_id).first():
        raise HTTPException(404, "Clínica não encontrada")
    limit = max(1, min(limit, 500))
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.clinica_id == clinica_id)
        .order_by(AuditLog.quando.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "acao": r.acao,
            "recurso": r.recurso,
            "recurso_id": r.recurso_id,
            "usuario_id": r.usuario_id,
            "ip": r.ip,
            "quando": r.quando.isoformat() + "Z",
            "detalhes": r.detalhes,
        }
        for r in rows
    ]


# ============================================================================
# Sprint 4 — Painel admin de signups públicos + funil + export CSV
# ============================================================================

def _status_clinica(c: Clinica, hoje: date) -> str:
    """Classifica clínica em 'em_trial' | 'expirado' | 'pago' | 'inativo'."""
    if not c.ativo:
        return "inativo"
    if c.plano != "trial":
        return "pago"
    if c.trial_expira_em and c.trial_expira_em >= hoje:
        return "em_trial"
    return "expirado"


def _status_clinica_sql(hoje: date):
    """Versão SQL de _status_clinica — usada pra filtrar/paginar no banco ao invés
    de carregar tudo em memória. MANTER alinhada com `_status_clinica()` acima.
    """
    return case(
        (Clinica.ativo.is_(False), "inativo"),
        (Clinica.plano != "trial", "pago"),
        (Clinica.trial_expira_em.is_(None), "expirado"),
        (Clinica.trial_expira_em >= hoje, "em_trial"),
        else_="expirado",
    )


@router.get("/signups")
def listar_signups(
    origem: str = "signup_publico",
    status_filtro: str | None = Query(None, alias="status"),
    desde: date | None = None,
    ate: date | None = None,
    incluir_inativas: bool = False,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db_dependency),
):
    """Lista detalhada de clínicas (default: signups públicos) com dados do admin + status calculado.

    Sprint 6: por default oculta clínicas inativas (consistência com soft delete).
    Passe `incluir_inativas=true` pra ver tudo. /funnel NÃO filtra (denominador real).
    """
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    hoje = date.today()

    # status_calculado vira coluna SQL — permite WHERE + paginação no banco.
    status_expr = _status_clinica_sql(hoje).label("status_calc")
    q = db.query(Clinica, status_expr).filter(Clinica.origem_cadastro == origem)
    if not incluir_inativas:
        q = q.filter(Clinica.ativo == True)  # noqa: E712 — SQLAlchemy bool comparison
    if desde:
        q = q.filter(Clinica.criado_em >= datetime.combine(desde, datetime.min.time()))
    if ate:
        q = q.filter(Clinica.criado_em < datetime.combine(ate + timedelta(days=1), datetime.min.time()))
    if status_filtro:
        # Filtro precisa ser a EXPRESSÃO (não o alias) pra funcionar em WHERE no PG.
        q = q.filter(_status_clinica_sql(hoje) == status_filtro)

    total = q.with_entities(Clinica.id).count()  # count separado pra UI saber tamanho real
    rows = q.order_by(Clinica.criado_em.desc()).offset(offset).limit(limit).all()

    out = []
    for c, st in rows:
        admin = next((u for u in c.usuarios if u.role == "admin"), None)
        out.append({
            "id": c.id,
            "nome": c.nome,
            "especialidade": getattr(c, "especialidade", "odonto"),
            "plano": c.plano,
            "ativo": c.ativo,
            "trial_expira_em": c.trial_expira_em.isoformat() if c.trial_expira_em else None,
            "evolution_conectado": c.evolution_conectado,
            "criado_em": c.criado_em.isoformat() + "Z",
            "admin_email": admin.email if admin else None,
            "admin_telefone": admin.telefone if admin else None,
            "admin_nome": admin.nome if admin else None,
            "status_calculado": st,
        })
    return {"total": total, "items": out}


@router.get("/funnel")
def funil_metricas(
    desde: date | None = None,
    ate: date | None = None,
    db: Session = Depends(get_db_dependency),
):
    """Métricas agregadas do funil de conversão (signup → conectou WhatsApp → pagou)."""
    hoje = date.today()
    if not ate:
        ate = hoje
    if not desde:
        desde = ate - timedelta(days=30)

    q = db.query(Clinica).filter(Clinica.origem_cadastro == "signup_publico")
    q = q.filter(Clinica.criado_em >= datetime.combine(desde, datetime.min.time()))
    q = q.filter(Clinica.criado_em < datetime.combine(ate + timedelta(days=1), datetime.min.time()))
    clinicas = q.all()

    total = len(clinicas)
    ativos_em_trial = 0
    trials_expirados = 0
    convertidos = 0
    whatsapp_conectado = 0
    por_esp_total: dict[str, int] = defaultdict(int)
    por_esp_conv: dict[str, int] = defaultdict(int)
    por_plano: dict[str, int] = defaultdict(int)

    planos_pagos = {"essencial", "pro", "enterprise"}
    for c in clinicas:
        esp = getattr(c, "especialidade", "odonto") or "odonto"
        por_esp_total[esp] += 1
        por_plano[c.plano] += 1
        if c.plano in planos_pagos:
            convertidos += 1
            por_esp_conv[esp] += 1
        elif c.plano == "trial":
            if c.trial_expira_em and c.trial_expira_em >= hoje:
                ativos_em_trial += 1
            else:
                trials_expirados += 1
        if c.evolution_conectado:
            whatsapp_conectado += 1

    pct = lambda n: round((n / total * 100), 2) if total else 0.0

    por_esp = [
        {
            "slug": slug,
            "total": tot,
            "convertidos": por_esp_conv.get(slug, 0),
            "taxa_percent": round((por_esp_conv.get(slug, 0) / tot * 100), 2) if tot else 0.0,
        }
        for slug, tot in sorted(por_esp_total.items(), key=lambda x: -x[1])
    ]
    por_plano_list = [
        {"plano": p, "count": c}
        for p, c in sorted(por_plano.items(), key=lambda x: -x[1])
    ]

    return {
        "periodo": {"desde": desde.isoformat(), "ate": ate.isoformat()},
        "total_signups": total,
        "ativos_em_trial": ativos_em_trial,
        "trials_expirados": trials_expirados,
        "convertidos": convertidos,
        "whatsapp_conectado": whatsapp_conectado,
        "taxa_conversao_percent": pct(convertidos),
        "taxa_whatsapp_percent": pct(whatsapp_conectado),
        "por_especialidade": por_esp,
        "por_plano": por_plano_list,
    }


@router.get("/export-leads.csv")
def exportar_leads_csv(
    request: Request,
    status_filtro: str = Query("nao_convertido", alias="status"),
    incluir_telefone: bool = True,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """Exporta CSV de leads (signup público). LGPD: auditado como EXPORT."""
    hoje = date.today()
    q = db.query(Clinica).filter(Clinica.origem_cadastro == "signup_publico")
    clinicas = q.order_by(Clinica.criado_em.desc()).all()

    def _aceita(c: Clinica) -> bool:
        st = _status_clinica(c, hoje)
        if status_filtro == "todos":
            return True
        if status_filtro == "nao_convertido":
            return c.plano == "trial"  # esteja expirado ou em trial — não pagou
        if status_filtro == "expirado":
            return st == "expirado"
        if status_filtro == "em_trial":
            return st == "em_trial"
        return False

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "email", "nome", "telefone", "clinica_nome", "especialidade",
        "plano", "trial_expira_em", "criado_em", "status",
    ])
    total = 0
    for c in clinicas:
        if not _aceita(c):
            continue
        admin = next((u for u in c.usuarios if u.role == "admin"), None)
        if not admin:
            continue
        st = _status_clinica(c, hoje)
        writer.writerow([
            admin.email,
            admin.nome or "",
            (admin.telefone or "") if incluir_telefone else "",
            c.nome,
            getattr(c, "especialidade", "odonto"),
            c.plano,
            c.trial_expira_em.isoformat() if c.trial_expira_em else "",
            c.criado_em.isoformat(),
            st,
        ])
        total += 1

    # Audit LGPD — export de dados pessoais
    audit.log(
        db, **ctx,
        clinica_id=None,
        acao=AcaoAudit.EXPORT,
        recurso="leads_csv",
        recurso_id=None,
        detalhes={"status": status_filtro, "total": total, "incluir_telefone": incluir_telefone},
    )
    db.commit()

    fname = f"leads_recepia_{hoje.isoformat()}.csv"
    # UTF-8 BOM pra Excel BR abrir com acentos corretos
    body_bytes = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    return Response(
        content=body_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{clinica_id}/extend-trial")
def estender_trial(
    clinica_id: str,
    dias: int = 7,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """Admin: estende o trial de uma clínica por N dias a partir de hoje (ou da expiração atual)."""
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    from datetime import date
    base = clinica.trial_expira_em if (clinica.trial_expira_em and clinica.trial_expira_em > date.today()) else date.today()
    clinica.trial_expira_em = base + timedelta(days=dias)
    if clinica.plano != Plano.TRIAL:
        clinica.plano = Plano.TRIAL
    audit.log(db, **ctx, clinica_id=clinica.id, acao=AcaoAudit.UPDATE, recurso="clinica",
              recurso_id=clinica.id, detalhes={"acao": "extend_trial", "dias": dias, "nova_expiracao": clinica.trial_expira_em.isoformat()})
    db.commit()
    return {"trial_expira_em": clinica.trial_expira_em.isoformat(), "dias_adicionados": dias}


@router.get("/{clinica_id}/notas")
def obter_notas(clinica_id: str, db: Session = Depends(get_db_dependency)):
    """Admin: lê notas internas sobre a clínica (visível só aqui, nunca pra clínica)."""
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    return {"notas_internas": getattr(clinica, "notas_internas", None) or ""}


@router.put("/{clinica_id}/notas")
def salvar_notas(
    clinica_id: str,
    body: dict,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """Admin: salva notas internas sobre a clínica."""
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    clinica.notas_internas = (body.get("notas_internas") or "").strip()[:2000]
    audit.log(db, **ctx, clinica_id=clinica.id, acao=AcaoAudit.UPDATE, recurso="clinica",
              recurso_id=clinica.id, detalhes={"acao": "notas_internas"})
    db.commit()
    return {"ok": True}


@router.post("/{clinica_id}/impersonar")
def impersonar_clinica(
    clinica_id: str,
    db: Session = Depends(get_db_dependency),
    ctx: dict = Depends(audit_context_admin),
):
    """Admin: gera JWT curto (15min) pra logar como admin da clínica. AUDIT obrigatório."""
    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    if not clinica:
        raise HTTPException(404, "Clínica não encontrada")
    admin_user = db.query(Usuario).filter(
        Usuario.clinica_id == clinica_id,
        Usuario.role == "admin",
        Usuario.ativo == True,
    ).first()
    if not admin_user:
        raise HTTPException(404, "Nenhum admin ativo nesta clínica")
    # Token de curta duração (15 min) para impersonação
    from jose import jwt as _jwt
    from config import settings
    from core.security import JWT_AUD, JWT_ISS
    payload = {
        "sub": admin_user.id,
        "clinica_id": clinica_id,
        "role": admin_user.role,
        "aud": JWT_AUD,
        "iss": JWT_ISS,
        "exp": agora_utc() + timedelta(minutes=15),
        "iat": agora_utc(),
        "impersonado_por": "admin_master",
    }
    token = _jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    audit.log(db, **ctx, clinica_id=clinica.id, acao=AcaoAudit.READ, recurso="impersonar",
              recurso_id=clinica.id, detalhes={"admin_email": admin_user.email})
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "clinica_id": clinica_id,
        "clinica_nome": clinica.nome,
        "role": admin_user.role,
        "usuario_nome": admin_user.nome or admin_user.email,
        "expira_em_minutos": 15,
    }


# ============================================================================
# Self-service da clínica logada (router_me) — JWT do dashboard
# ============================================================================

def _exigir_admin_clinica(usuario: Usuario) -> None:
    """RBAC: só admin da própria clínica pode mutar dados da clínica."""
    if usuario.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Só admin da clínica pode editar")


class ClinicaMeOut(BaseModel):
    id: str
    nome: str
    cnpj: str | None
    plano: str
    especialidade: str
    responsavel_tecnico: str | None
    registro_conselho: str | None
    endereco_rua: str | None
    endereco_numero: str | None
    endereco_complemento: str | None
    endereco_bairro: str | None
    endereco_cidade: str | None
    endereco_uf: str | None
    endereco_cep: str | None
    logo_key: str | None

    class Config:
        from_attributes = True


class ClinicaMeUpdate(BaseModel):
    nome: str | None = Field(None, min_length=2)
    cnpj: str | None = None
    especialidade: str | None = None
    responsavel_tecnico: str | None = None
    registro_conselho: str | None = None
    endereco_rua: str | None = None
    endereco_numero: str | None = None
    endereco_complemento: str | None = None
    endereco_bairro: str | None = None
    endereco_cidade: str | None = None
    endereco_uf: str | None = Field(None, max_length=2)
    endereco_cep: str | None = Field(None, max_length=8)
    config_paciente: dict | None = None


class EspecialidadeConfigOut(BaseModel):
    config: dict
    especialidades_disponiveis: list[str]
    clinica: dict


@router_me.get("/especialidade-config", response_model=EspecialidadeConfigOut)
def obter_especialidade_config(
    clinica: Clinica = Depends(clinica_atual),
):
    """Config efetiva da especialidade da clínica (merge default + override). Frontend cacheia."""
    return EspecialidadeConfigOut(
        config=especialidade_to_dict(config_efetiva(clinica)),
        especialidades_disponiveis=listar_slugs(),
        clinica={
            "nome": clinica.nome,
            "plano": clinica.plano,
            "responsavel_tecnico": clinica.responsavel_tecnico,
            "registro_conselho": clinica.registro_conselho,
            "logo_key": clinica.logo_key,
            "endereco_rua": clinica.endereco_rua,
            "endereco_numero": clinica.endereco_numero,
            "endereco_complemento": clinica.endereco_complemento,
            "endereco_bairro": clinica.endereco_bairro,
            "endereco_cidade": clinica.endereco_cidade,
            "endereco_uf": clinica.endereco_uf,
            "endereco_cep": clinica.endereco_cep,
        },
    )


class TemplatesPadraoOut(BaseModel):
    vertical: str
    templates: dict[str, str]


@router_me.get("/templates-padrao", response_model=TemplatesPadraoOut)
def templates_padrao(clinica: Clinica = Depends(clinica_atual)):
    """Templates default de mensagem WhatsApp da vertical da clínica.

    Útil pro dashboard mostrar "ver mensagens padrão da sua vertical" antes do
    cliente editar os textos persistidos em Configuracao.
    """
    cfg = get_especialidade(clinica.especialidade)
    return TemplatesPadraoOut(vertical=cfg.slug, templates=dict(cfg.mensagens_whatsapp))


class PlanoDisponivelOut(BaseModel):
    slug: str
    nome: str
    preco_mensal_reais: float
    link_hotmart: str | None


class TrialStatusOut(BaseModel):
    em_trial: bool
    expirado: bool
    expira_em: date | None
    dias_restantes: int
    planos_disponiveis: list[PlanoDisponivelOut]


@router_me.get("/trial-status", response_model=TrialStatusOut)
def trial_status(clinica: Clinica = Depends(clinica_atual)):
    from core.trial import esta_em_trial, trial_expirado, dias_restantes
    from core.planos import LIMITES
    planos = []
    for slug, limite in LIMITES.items():
        if slug == "trial":
            continue
        # link Hotmart vem de env (HOTMART_LINK_<SLUG>) — settings opcional
        link = getattr(__import__("config").settings, f"HOTMART_LINK_{slug.upper()}", None) or None
        planos.append(PlanoDisponivelOut(
            slug=slug, nome=limite.nome_amigavel,
            preco_mensal_reais=limite.preco_mensal / 100, link_hotmart=link,
        ))
    return TrialStatusOut(
        em_trial=esta_em_trial(clinica),
        expirado=trial_expirado(clinica),
        expira_em=clinica.trial_expira_em,
        dias_restantes=dias_restantes(clinica),
        planos_disponiveis=planos,
    )


@router_me.get("", response_model=ClinicaMeOut)
def obter_clinica_me(
    clinica: Clinica = Depends(clinica_atual),
):
    """Dados completos da clínica logada (sem api_key / Evolution interno)."""
    return ClinicaMeOut.model_validate(clinica)


@router_me.put("", response_model=ClinicaMeOut)
def atualizar_clinica_me(
    payload: ClinicaMeUpdate,
    db: Session = Depends(get_db_dependency),
    usuario: Usuario = Depends(usuario_atual),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
):
    """Edita dados da clínica logada. Só admin da clínica."""
    _exigir_admin_clinica(usuario)

    campos_mudados: dict = {}
    dados = payload.model_dump(exclude_unset=True)

    # Validação de especialidade (deve estar entre os slugs conhecidos)
    if "especialidade" in dados and dados["especialidade"] is not None:
        if dados["especialidade"] not in listar_slugs():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Especialidade inválida. Use uma de: {listar_slugs()}",
            )

    for campo, valor in dados.items():
        atual = getattr(clinica, campo, None)
        if atual != valor:
            setattr(clinica, campo, valor)
            campos_mudados[campo] = True

    if campos_mudados:
        audit.log(
            db, **ctx,
            acao=AcaoAudit.UPDATE,
            recurso="clinica",
            recurso_id=clinica.id,
            detalhes={"campos": list(campos_mudados.keys())},
        )
        db.commit()
        db.refresh(clinica)

    return ClinicaMeOut.model_validate(clinica)


@router_me.post("/logo")
@limiter.limit("20/minute")  # decode+reencode Pillow é caro — mesma lógica dos PDFs
async def upload_logo_clinica(
    request: Request,
    arquivo: UploadFile = File(...),
    db: Session = Depends(get_db_dependency),
    usuario: Usuario = Depends(usuario_atual),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
):
    """Upload do logo da clínica (usado em cabeçalhos de PDF). Só admin."""
    _exigir_admin_clinica(usuario)

    # Early-abort: lê em chunks pra não estourar memória se enviarem arquivo gigante
    raw = bytearray()
    while True:
        chunk = await arquivo.read(64 * 1024)
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Arquivo maior que {MAX_UPLOAD_BYTES // (1024*1024)}MB",
            )

    try:
        meta = salvar_logo(clinica.id, bytes(raw))
    except FotoError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))

    clinica.logo_key = meta.key
    audit.log(
        db, **ctx,
        acao=AcaoAudit.CREATE,
        recurso="clinica_logo",
        recurso_id=clinica.id,
        detalhes={"tamanho_bytes": meta.tamanho_bytes, "sha256": meta.sha256},
    )
    db.commit()
    return {"logo_key": meta.key, "tamanho_bytes": meta.tamanho_bytes}


@router_me.get("/logo")
def baixar_logo_clinica(
    clinica: Clinica = Depends(clinica_atual),
):
    """Retorna bytes do logo (image/webp). Qualquer usuário logado pode ver."""
    dados = ler_logo(clinica.id)
    if dados is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Logo não encontrado")
    return Response(
        content=dados,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=600",
            "X-Content-Type-Options": "nosniff",
            "Vary": "Authorization",
        },
    )


@router_me.delete("/logo", status_code=status.HTTP_204_NO_CONTENT)
def remover_logo_clinica(
    db: Session = Depends(get_db_dependency),
    usuario: Usuario = Depends(usuario_atual),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
):
    """Remove o logo da clínica. Só admin."""
    _exigir_admin_clinica(usuario)
    deletar_logo(clinica.id)
    clinica.logo_key = None
    audit.log(
        db, **ctx,
        acao=AcaoAudit.DELETE,
        recurso="clinica_logo",
        recurso_id=clinica.id,
        detalhes={"acao": "deletar_logo"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================================
# Sprint 3 — Recall automático (self-service da clínica)
# ============================================================================

class RecallConfigOut(BaseModel):
    ativo: bool
    intervalo_dias: int
    template: str | None
    procedimento_chave: str
    recalls_ultimos_30d: int
    whatsapp_conectado: bool


class RecallConfigUpdate(BaseModel):
    ativo: bool | None = None
    intervalo_dias: int | None = Field(None, ge=30, le=365)
    template: str | None = Field(None, max_length=2000)
    procedimento_chave: str | None = Field(None, min_length=2, max_length=80)


class RecallCandidatoOut(BaseModel):
    paciente_id: str
    paciente_nome: str
    telefone_mascarado: str  # só últimos 4 dígitos visíveis (LGPD na UI)
    ultimo_procedimento: str | None
    ultima_visita: datetime


class RecallPreviewOut(BaseModel):
    total: int
    candidatos: list[RecallCandidatoOut]
    elegivel: bool  # ativa + WhatsApp conectado
    motivo_inelegivel: str | None


class RecallDispararOut(BaseModel):
    enviados: int
    falhas: int
    candidatos: int
    skip_whatsapp_offline: bool


@router_me.get("/recall", response_model=RecallConfigOut)
def obter_recall_config(
    db: Session = Depends(get_db_dependency),
    clinica: Clinica = Depends(clinica_atual),
):
    """Config atual de recall + contador dos últimos 30d (pra dashboard mostrar uso)."""
    from core.recall import contar_recalls_recentes
    return RecallConfigOut(
        ativo=clinica.recall_ativo,
        intervalo_dias=clinica.recall_intervalo_dias,
        template=clinica.recall_template,
        procedimento_chave=clinica.recall_procedimento_chave,
        recalls_ultimos_30d=contar_recalls_recentes(db, clinica.id, 30),
        whatsapp_conectado=clinica.evolution_conectado,
    )


@router_me.put("/recall", response_model=RecallConfigOut)
def atualizar_recall_config(
    payload: RecallConfigUpdate,
    db: Session = Depends(get_db_dependency),
    usuario: Usuario = Depends(usuario_atual),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
):
    """Admin da clínica edita config. Audit UPDATE."""
    _exigir_admin_clinica(usuario)
    dados = payload.model_dump(exclude_unset=True)
    campos_mudados: dict = {}

    mapping = {
        "ativo": "recall_ativo",
        "intervalo_dias": "recall_intervalo_dias",
        "template": "recall_template",
        "procedimento_chave": "recall_procedimento_chave",
    }
    for chave_in, campo_db in mapping.items():
        if chave_in in dados:
            novo = dados[chave_in]
            if campo_db == "recall_procedimento_chave" and novo is not None:
                novo = novo.strip().lower()
                if not novo:
                    raise HTTPException(422, "procedimento_chave não pode ser vazio")
            atual = getattr(clinica, campo_db)
            if atual != novo:
                setattr(clinica, campo_db, novo)
                campos_mudados[campo_db] = True

    if campos_mudados:
        audit.log(
            db, **ctx,
            acao=AcaoAudit.UPDATE,
            recurso="recall_config",
            recurso_id=clinica.id,
            detalhes={"campos": list(campos_mudados.keys())},
        )
        db.commit()
        db.refresh(clinica)

    from core.recall import contar_recalls_recentes
    return RecallConfigOut(
        ativo=clinica.recall_ativo,
        intervalo_dias=clinica.recall_intervalo_dias,
        template=clinica.recall_template,
        procedimento_chave=clinica.recall_procedimento_chave,
        recalls_ultimos_30d=contar_recalls_recentes(db, clinica.id, 30),
        whatsapp_conectado=clinica.evolution_conectado,
    )


def _mascarar_telefone(tel: str) -> str:
    """Mostra só últimos 4 dígitos pra UI de preview (LGPD: minimização)."""
    if not tel:
        return "—"
    s = "".join(c for c in tel if c.isdigit())
    return f"***{s[-4:]}" if len(s) >= 4 else "****"


@router_me.get("/recall/preview", response_model=RecallPreviewOut)
def preview_recall(
    db: Session = Depends(get_db_dependency),
    clinica: Clinica = Depends(clinica_atual),
):
    """Lista candidatos SEM enviar. Usado pela UI pra mostrar "X pacientes serão lembrados"."""
    from core.recall import candidatos_recall
    hoje = agora_utc()
    motivo = None
    if not clinica.recall_ativo:
        motivo = "Recall desativado"
    elif not clinica.evolution_conectado:
        motivo = "WhatsApp desconectado"
    candidatos = candidatos_recall(db, clinica, hoje)
    cards = [
        RecallCandidatoOut(
            paciente_id=p.id,
            paciente_nome=p.nome,
            telefone_mascarado=_mascarar_telefone(p.telefone),
            ultimo_procedimento=(pr.procedimentos_realizados or "")[:120] or None,
            ultima_visita=pr.criado_em,
        )
        for (p, pr) in candidatos
    ]
    return RecallPreviewOut(
        total=len(cards),
        candidatos=cards,
        elegivel=(motivo is None),
        motivo_inelegivel=motivo,
    )


@router_me.post("/recall/disparar", response_model=RecallDispararOut)
@limiter.limit("1/hour")
def disparar_recall_manual(
    request: Request,
    db: Session = Depends(get_db_dependency),
    usuario: Usuario = Depends(usuario_atual),
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
):
    """Dispara recall AGORA (botão admin). Rate-limit 1/hora pra não virar gatilho de flood."""
    _exigir_admin_clinica(usuario)
    from core.recall import processar_recall
    stats = processar_recall(db, clinica, hoje=agora_utc())
    audit.log(
        db, **ctx,
        acao=AcaoAudit.CREATE,
        recurso="recall_disparo_manual",
        recurso_id=clinica.id,
        detalhes=stats.to_dict(),
    )
    db.commit()
    return RecallDispararOut(**stats.to_dict())
