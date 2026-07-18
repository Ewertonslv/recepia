"""CRUD de agendamentos — multi-tenant, scoped por clinica_id."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db_dependency
from models import AcaoAudit, Agendamento, Clinica, Interacao, Paciente, Profissional, Status
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core import audit
from core.timezones import to_utc_naive

router = APIRouter(prefix="/api/agendamentos", tags=["agendamentos"])


# Transições de status permitidas. Bloqueia mudanças sem sentido (ex: realizado → pendente).
# 'reabrir' é uma transição administrativa que volta pra pendente — útil pra desfazer erro humano.
_TRANSICOES_PERMITIDAS = {
    Status.PENDENTE:    {Status.CONFIRMADO, Status.CANCELADO, Status.REAGENDADO, Status.NO_SHOW, Status.REALIZADO},
    Status.CONFIRMADO:  {Status.REALIZADO, Status.NO_SHOW, Status.CANCELADO, Status.REAGENDADO, Status.PENDENTE},
    Status.REAGENDADO:  {Status.PENDENTE, Status.CONFIRMADO, Status.CANCELADO},
    # Estados "finais" só permitem reabrir pra pendente (correção de erro humano).
    Status.REALIZADO:   {Status.PENDENTE},
    Status.NO_SHOW:     {Status.PENDENTE},
    Status.CANCELADO:   {Status.PENDENTE},
}


def _validar_transicao_status(status_atual: str, novo_status: str) -> None:
    """Levanta 400 se transição não for permitida. Aceita mesmo status (no-op)."""
    if status_atual == novo_status:
        return
    permitidas = _TRANSICOES_PERMITIDAS.get(status_atual, set())
    if novo_status not in permitidas:
        raise HTTPException(
            400,
            f"Transição '{status_atual}' → '{novo_status}' não permitida. "
            f"Permitidas: {sorted(permitidas) or 'nenhuma'}"
        )


class AgendamentoIn(BaseModel):
    paciente_id: str
    data_hora: datetime
    duracao_minutos: int = 30
    servico: str | None = None
    profissional: str | None = None      # legacy: nome livre
    profissional_id: str | None = None   # preferred: FK pro Profissional


class AgendamentoUpdate(BaseModel):
    status: str | None = None
    data_hora: datetime | None = None
    servico: str | None = None
    profissional: str | None = None
    profissional_id: str | None = None


class AgendamentoOut(BaseModel):
    id: str
    paciente_id: str
    data_hora: datetime
    duracao_minutos: int
    servico: str | None
    profissional: str | None
    profissional_id: str | None = None
    status: str
    confirmacao_enviada: bool
    segunda_confirmacao: bool
    criado_em: datetime

    class Config:
        from_attributes = True


def _resolver_profissional(
    db: Session, clinica_id: str,
    profissional_id: str | None,
    profissional_nome: str | None,
) -> tuple[str | None, str | None]:
    """Decide (profissional_id final, profissional string final).

    Regras:
    - Se profissional_id veio: valida tenant (404 se outra clínica). Se válido,
      espelha nome no campo string pra UI continuar mostrando algo amigável.
    - Se só nome veio e bate exato (case-insensitive) com um Profissional ativo
      da clínica, auto-link FK + usa o nome canônico do cadastro.
    - Senão mantém só a string como veio.
    """
    if profissional_id:
        prof = db.query(Profissional).filter(
            Profissional.id == profissional_id,
            Profissional.clinica_id == clinica_id,
        ).first()
        if not prof:
            raise HTTPException(404, "Profissional não encontrado")
        return prof.id, prof.nome
    if profissional_nome and profissional_nome.strip():
        match = db.query(Profissional).filter(
            Profissional.clinica_id == clinica_id,
            Profissional.ativo == True,
        ).all()
        nome_norm = profissional_nome.strip().lower()
        for p in match:
            if p.nome.strip().lower() == nome_norm:
                return p.id, p.nome  # canônico
        return None, profissional_nome.strip()
    return None, None


@router.post("", response_model=AgendamentoOut, status_code=status.HTTP_201_CREATED)
def criar(
    payload: AgendamentoIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    # Valida que paciente pertence à mesma clínica (defense in depth)
    paciente = (
        db.query(Paciente)
        .filter(Paciente.id == payload.paciente_id, Paciente.clinica_id == clinica.id)
        .first()
    )
    if not paciente:
        raise HTTPException(400, "Paciente não encontrado nesta clínica")

    prof_id, prof_nome = _resolver_profissional(
        db, clinica.id, payload.profissional_id, payload.profissional,
    )
    if payload.profissional_id and not prof_id:
        raise HTTPException(404, "Profissional não pertence a esta clínica")
    agendamento = Agendamento(
        clinica_id=clinica.id,
        paciente_id=payload.paciente_id,
        data_hora=to_utc_naive(payload.data_hora),
        duracao_minutos=payload.duracao_minutos,
        servico=payload.servico,
        profissional=prof_nome,
        profissional_id=prof_id,
    )
    db.add(agendamento)
    db.flush()  # popula agendamento.id ANTES do audit (senão recurso_id fica None — gap LGPD)
    audit.log(db, **ctx, acao=AcaoAudit.CREATE, recurso="agendamento", recurso_id=agendamento.id,
              detalhes={"paciente_id": payload.paciente_id, "data_hora": payload.data_hora.isoformat()})
    db.commit()
    db.refresh(agendamento)
    return agendamento


@router.get("", response_model=list[AgendamentoOut])
def listar(
    status_filtro: str | None = None,
    data: str | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(Agendamento).filter(Agendamento.clinica_id == clinica.id)
    if status_filtro:
        q = q.filter(Agendamento.status == status_filtro)
    # Os filtros de data representam DIAS do calendário BR. Como data_hora é
    # armazenado em UTC naive, convertemos a janela [00:00 BR, 00:00 BR +1) pra
    # UTC antes de comparar — senão consultas perto da meia-noite caem no dia errado.
    if data:
        try:
            dia = datetime.fromisoformat(data).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            raise HTTPException(400, "Data inválida (use YYYY-MM-DD)")
        q = q.filter(
            Agendamento.data_hora >= to_utc_naive(dia),
            Agendamento.data_hora < to_utc_naive(dia + timedelta(days=1)),
        )
    if data_inicio:
        try:
            ini = datetime.fromisoformat(data_inicio).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            raise HTTPException(400, "data_inicio inválida (use YYYY-MM-DD)")
        q = q.filter(Agendamento.data_hora >= to_utc_naive(ini))
    if data_fim:
        try:
            fim = datetime.fromisoformat(data_fim).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            raise HTTPException(400, "data_fim inválida (use YYYY-MM-DD)")
        # fim é inclusivo (dia inteiro) → limite exterior é o começo do dia seguinte.
        q = q.filter(Agendamento.data_hora < to_utc_naive(fim + timedelta(days=1)))
    return q.order_by(Agendamento.data_hora).all()


@router.get("/{agendamento_id}", response_model=AgendamentoOut)
def obter(
    agendamento_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    a = (
        db.query(Agendamento)
        .filter(Agendamento.id == agendamento_id, Agendamento.clinica_id == clinica.id)
        .first()
    )
    if not a:
        raise HTTPException(404, "Agendamento não encontrado")
    return a


@router.put("/{agendamento_id}", response_model=AgendamentoOut)
def atualizar(
    agendamento_id: str,
    payload: AgendamentoUpdate,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    a = (
        db.query(Agendamento)
        .filter(Agendamento.id == agendamento_id, Agendamento.clinica_id == clinica.id)
        .first()
    )
    if not a:
        raise HTTPException(404, "Agendamento não encontrado")
    if payload.status is not None:
        _validar_transicao_status(a.status, payload.status)
        a.status = payload.status
    if payload.data_hora is not None:
        a.data_hora = to_utc_naive(payload.data_hora)
    if payload.servico is not None:
        a.servico = payload.servico
    if payload.profissional is not None or payload.profissional_id is not None:
        # Fix #4: se vem `profissional` string sem `profissional_id`, _resolver_profissional
        # já valida tenant — mas se nome não bate com ninguém da clínica, retorna a string
        # como veio (legado). Garantimos aqui que só aceita IDs válidos da clínica.
        prof_id, prof_nome = _resolver_profissional(
            db, clinica.id, payload.profissional_id, payload.profissional,
        )
        if payload.profissional_id and not prof_id:
            raise HTTPException(404, "Profissional não pertence a esta clínica")
        a.profissional_id = prof_id
        a.profissional = prof_nome
    audit.log(db, **ctx, acao=AcaoAudit.UPDATE, recurso="agendamento", recurso_id=a.id,
              detalhes=payload.model_dump(exclude_none=True, mode="json"))
    db.commit()
    db.refresh(a)
    return a


@router.get("/{agendamento_id}/interacoes")
def listar_interacoes(
    agendamento_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    """G3: dona da clínica vê a conversa completa que a IA teve com a paciente."""
    a = (
        db.query(Agendamento)
        .filter(Agendamento.id == agendamento_id, Agendamento.clinica_id == clinica.id)
        .first()
    )
    if not a:
        raise HTTPException(404, "Agendamento não encontrado")
    interacoes = (
        db.query(Interacao)
        .filter(
            Interacao.clinica_id == clinica.id,
            Interacao.agendamento_id == agendamento_id,
        )
        .order_by(Interacao.quando.asc())
        .all()
    )
    return [
        {
            "id": i.id,
            "tipo": i.tipo,
            "mensagem_enviada": i.mensagem_enviada,
            "mensagem_recebida": i.mensagem_recebida,
            "quando": i.quando.isoformat() + "Z",
        }
        for i in interacoes
    ]


@router.delete("/{agendamento_id}")
def cancelar(
    agendamento_id: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    a = (
        db.query(Agendamento)
        .filter(Agendamento.id == agendamento_id, Agendamento.clinica_id == clinica.id)
        .first()
    )
    if not a:
        raise HTTPException(404, "Agendamento não encontrado")
    a.status = Status.CANCELADO
    audit.log(db, **ctx, acao=AcaoAudit.UPDATE, recurso="agendamento", recurso_id=a.id,
              detalhes={"acao": "cancelar"})
    db.commit()
    return {"status": "cancelado"}
