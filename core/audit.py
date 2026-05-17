"""Helper pra registrar audit log (LGPD Art. 37)."""
from sqlalchemy.orm import Session
from models import AuditLog


def log(
    db: Session,
    *,
    clinica_id: str | None,
    usuario_id: str | None,
    acao: str,
    recurso: str,
    recurso_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detalhes: dict | None = None,
) -> None:
    """Não comita — assume que a chamada faz commit no final da transação."""
    entry = AuditLog(
        clinica_id=clinica_id,
        usuario_id=usuario_id,
        acao=acao,
        recurso=recurso,
        recurso_id=recurso_id,
        ip=ip,
        user_agent=user_agent,
        detalhes=detalhes or {},
    )
    db.add(entry)
