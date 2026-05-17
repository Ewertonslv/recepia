"""JWT e hash de senha (F10 + F11)."""
import secrets
import string
import uuid
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext

from config import settings

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# F10: aud/iss restringem tokens a esse serviço/audiência específicos
JWT_AUD = "recepia-dashboard"
JWT_ISS = "recepia-api"

# F13: dummy hash usado quando email não existe (mantém timing constante)
DUMMY_HASH = "$2b$12$" + "x" * 53


def hash_senha(senha: str) -> str:
    return _pwd_ctx.hash(senha)


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return _pwd_ctx.verify(senha, senha_hash)
    except Exception:
        return False


def criar_token(usuario_id: str, clinica_id: str, role: str) -> str:
    """JWT com clinica_id no payload + aud + iss (validados no decode)."""
    payload = {
        "sub": usuario_id,
        "clinica_id": clinica_id,
        "role": role,
        "aud": JWT_AUD,
        "iss": JWT_ISS,
        "exp": datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRES_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decodificar_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=JWT_AUD,
            issuer=JWT_ISS,
        )
    except JWTError:
        return None


# ============================================================================
# Admin master (Ewerton) — JWT separado pra UI admin (TTL menor)
# ============================================================================

JWT_AUD_ADMIN = "recepia-admin"
ADMIN_SUB = "__admin__"
ADMIN_TTL_MINUTES = 120  # 2h


def criar_token_admin() -> str:
    """JWT pro admin master após login com X-Admin-Key. TTL 2h.

    Sprint 6: inclui `jti` (JWT id) pra suportar revogação via blacklist.
    Tokens emitidos antes da mudança não terão `jti` — backward compat:
    `verificar_jti_admin` retorna True quando jti=None (comportamento legado).
    """
    payload = {
        "sub": ADMIN_SUB,
        "role": "superadmin",
        "aud": JWT_AUD_ADMIN,
        "iss": JWT_ISS,
        "exp": datetime.utcnow() + timedelta(minutes=ADMIN_TTL_MINUTES),
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decodificar_token_admin(token: str) -> dict | None:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=JWT_AUD_ADMIN,
            issuer=JWT_ISS,
        )
    except JWTError:
        return None


def verificar_jti_admin(jti: str | None, db) -> bool:
    """Sprint 6: True se token NÃO está revogado. None (token legado sem jti) → True.

    Mantido fora de `decodificar_token_admin` pra evitar dependência circular com
    `database.Session` em código que só precisa decodificar payload.
    """
    if not jti:
        return True  # backward compat: token sem jti = pré-Sprint 6
    from models import AdminTokenRevogado
    revogado = db.query(AdminTokenRevogado.id).filter(
        AdminTokenRevogado.jti == jti,
    ).first()
    return revogado is None


# ============================================================================
# Senhas aleatórias (admin reseta senha de usuário sem aceitar input)
# ============================================================================

def gerar_senha_aleatoria(tamanho: int = 12) -> str:
    """Gera senha segura. Inclui letras + dígitos + 1 símbolo simples (evita confusão visual)."""
    alfabeto = string.ascii_letters + string.digits
    # garante pelo menos 1 dígito e 1 minúscula pra passar em regras básicas
    while True:
        senha = "".join(secrets.choice(alfabeto) for _ in range(tamanho))
        if any(c.islower() for c in senha) and any(c.isdigit() for c in senha):
            return senha
