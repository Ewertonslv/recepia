from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://recepia:recepia@localhost:5432/recepia"

    # Auth — SEM defaults seguros. Pydantic falha boot se não vier do .env.
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_MINUTES: int = 60 * 24 * 7  # 7 dias
    ADMIN_API_KEY: str

    # Evolution API (WhatsApp self-hosted)
    EVOLUTION_API_URL: str = "http://localhost:8080"
    EVOLUTION_API_KEY: str = ""
    # URL pública da Recepia (usada pra apontar webhook do Evolution pra cá)
    # Ex: https://recepia.app.br ou https://abc.trycloudflare.com
    PUBLIC_WEBHOOK_URL: str = ""
    # Secret HMAC pro Evolution assinar o body do webhook (F2)
    # Gera com: openssl rand -hex 32
    EVOLUTION_WEBHOOK_SECRET: str = ""

    # Groq (IA classificadora de respostas)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Operação
    INTERVALO_CONFIRMACAO_HORAS: int = 24
    INTERVALO_LEMBRETE_HORAS: int = 2
    TIMEZONE: str = "America/Sao_Paulo"

    # Ambiente
    DEBUG: bool = False
    ALLOWED_ORIGINS: str = "https://recepia.app.br,https://app.recepia.app.br"

    # ----------------------------------------------------------------- validators

    @field_validator("JWT_SECRET", "ADMIN_API_KEY", "EVOLUTION_API_KEY")
    @classmethod
    def rejeitar_defaults_change_me(cls, v: str, info) -> str:
        if not v:
            return v  # EVOLUTION_API_KEY pode ser vazia em dev
        v_lower = v.lower().strip()
        if v_lower.startswith("change-me") or v_lower in ("changeme", "dev-key", "test", "secret", "password"):
            raise ValueError(
                f"{info.field_name} usa valor inseguro ('{v[:20]}...'). "
                "Gere com `openssl rand -hex 32` e configure no .env."
            )
        # JWT precisa ser longo o suficiente pra HS256 (256 bits = 32 bytes hex = 64 chars)
        if info.field_name == "JWT_SECRET" and len(v) < 32:
            raise ValueError(
                f"JWT_SECRET muito curto ({len(v)} chars). Mínimo 32. "
                "Gere com `openssl rand -hex 32`."
            )
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def origens_cors() -> list[str]:
    raw = settings.ALLOWED_ORIGINS or ""
    origens = [o.strip() for o in raw.split(",") if o.strip()]
    if settings.DEBUG:
        origens += ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"]
    return origens
