"""Sprint 3 — Recall automático pós-procedimento.

Conceito: paciente que fez um procedimento âncora (ex.: "limpeza") há mais
de N dias e NÃO tem agendamento futuro recebe um lembrete no WhatsApp.

LGPD:
- NUNCA dispara pra `Paciente.opt_out=True` ou `deletado_em IS NOT NULL`.
- Toda mensagem disparada gera AuditLog (recurso="recall_whatsapp").
- Tabela `recalls_enviados` impede repetição em < 30 dias (anti-flood).

Saída assíncrona via job APScheduler em `cron/jobs.py` (job `recall_diario`).
Pré-condições por clínica: `recall_ativo=True`, `ativo=True`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from core.timezones import agora_utc

from sqlalchemy import func
from sqlalchemy.orm import Session

from core import audit
from models import (
    AcaoAudit,
    Agendamento,
    Clinica,
    Paciente,
    Prontuario,
    RecallEnviado,
)

log = logging.getLogger("recepia.recall")

# Hard limit por clínica/dia pra evitar flood/ban no WhatsApp da clínica.
MAX_RECALLS_POR_CLINICA_DIA = 50
# Janela de cooldown: paciente que recebeu recall nos últimos N dias não recebe de novo.
COOLDOWN_DIAS = 30

TEMPLATE_DEFAULT = (
    "Olá {nome}! Já faz um tempinho desde sua última visita aqui na {clinica}. "
    "Que tal agendar sua próxima consulta? Estamos te esperando."
)


@dataclass(frozen=True)
class RecallStats:
    enviados: int
    falhas: int
    candidatos: int
    skip_whatsapp_offline: bool = False

    def to_dict(self) -> dict:
        return {
            "enviados": self.enviados,
            "falhas": self.falhas,
            "candidatos": self.candidatos,
            "skip_whatsapp_offline": self.skip_whatsapp_offline,
        }


def _template_efetivo(clinica: Clinica) -> str:
    """Escolhe o template de recall na ordem de prioridade:
      1. `Clinica.recall_template` (override explícito da clínica)
      2. Template da vertical da clínica (`mensagens_whatsapp['recall']`)
      3. Genérico hard-coded `TEMPLATE_DEFAULT` (último fallback)
    """
    if clinica.recall_template:
        return clinica.recall_template
    # Import lazy: evita carregar especialidades em qualquer chamada de recall
    # (e mantém core/recall.py utilizável em testes que não usam o catálogo).
    from core.especialidades import get_especialidade
    try:
        cfg = get_especialidade(clinica.especialidade)
        tpl = cfg.mensagens_whatsapp.get("recall")
        if tpl:
            return tpl
    except Exception:  # noqa: BLE001 — defensivo, nunca quebra o cron
        log.warning("recall: falha lendo template da vertical clinica=%s", clinica.id)
    return TEMPLATE_DEFAULT


def _renderizar(template: str | None, paciente: Paciente, clinica: Clinica) -> str:
    """Renderiza template com placeholders {nome} e {clinica}. Tolerante a templates malformados.

    `template` é o valor BRUTO de `Clinica.recall_template` (pode ser None). Quando
    None, cai pra template da vertical e, por fim, pro `TEMPLATE_DEFAULT` genérico.
    """
    tpl = template or _template_efetivo(clinica)
    try:
        return tpl.format(nome=paciente.nome, clinica=clinica.nome)
    except (KeyError, IndexError, ValueError):
        # Template do cliente quebrado — fallback seguro pro default
        log.warning("recall: template inválido em clinica=%s, usando default", clinica.id)
        return TEMPLATE_DEFAULT.format(nome=paciente.nome, clinica=clinica.nome)


def candidatos_recall(
    db: Session,
    clinica: Clinica,
    hoje: datetime,
) -> list[tuple[Paciente, Prontuario]]:
    """Retorna pacientes elegíveis pra recall agora.

    Critérios (AND):
      1. Procedimento âncora (ILIKE %chave%) registrado em Prontuario há >= intervalo_dias.
      2. Paciente.opt_out=False e Paciente.deletado_em IS NULL.
      3. Sem Agendamento futuro (data_hora > hoje).
      4. Sem RecallEnviado nos últimos `COOLDOWN_DIAS` dias.

    Retorna até `MAX_RECALLS_POR_CLINICA_DIA` tuplas (paciente, prontuario_origem).
    O prontuário retornado é o MAIS RECENTE âncora (>= intervalo). Garantimos
    1 entrada por paciente.
    """
    chave = (clinica.recall_procedimento_chave or "limpeza").strip()
    if not chave:
        return []
    intervalo = max(1, int(clinica.recall_intervalo_dias or 180))
    limite_data = hoje - timedelta(days=intervalo)
    cooldown_ts = hoje - timedelta(days=COOLDOWN_DIAS)

    # Subquery: pacientes com recall recente (dentro do cooldown) — pra excluir
    recall_recente_ids = (
        db.query(RecallEnviado.paciente_id)
        .filter(
            RecallEnviado.clinica_id == clinica.id,
            RecallEnviado.enviado_em >= cooldown_ts,
        )
    )

    # Subquery: pacientes com agendamento futuro — pra excluir
    com_agendamento_futuro_ids = (
        db.query(Agendamento.paciente_id)
        .filter(
            Agendamento.clinica_id == clinica.id,
            Agendamento.data_hora > hoje,
        )
    )

    # Pega o prontuário âncora MAIS RECENTE por paciente (que ainda satisfaça o intervalo).
    # Pega tudo que casa, em ordem desc por criado_em; em Python dedup por paciente_id.
    pattern = f"%{chave}%"
    rows = (
        db.query(Prontuario, Paciente)
        .join(Paciente, Paciente.id == Prontuario.paciente_id)
        .filter(
            Prontuario.clinica_id == clinica.id,
            Prontuario.procedimentos_realizados.isnot(None),
            Prontuario.procedimentos_realizados.ilike(pattern),
            Prontuario.criado_em <= limite_data,
            Paciente.opt_out.is_(False),
            Paciente.deletado_em.is_(None),
            ~Paciente.id.in_(recall_recente_ids),
            ~Paciente.id.in_(com_agendamento_futuro_ids),
        )
        .order_by(Prontuario.criado_em.desc())
        .limit(MAX_RECALLS_POR_CLINICA_DIA * 5)  # buffer p/ dedup
        .all()
    )

    vistos: set[str] = set()
    out: list[tuple[Paciente, Prontuario]] = []
    for prontuario, paciente in rows:
        if paciente.id in vistos:
            continue
        vistos.add(paciente.id)
        out.append((paciente, prontuario))
        if len(out) >= MAX_RECALLS_POR_CLINICA_DIA:
            break
    return out


def processar_recall(db: Session, clinica: Clinica, hoje: datetime | None = None) -> RecallStats:
    """Dispara recalls pra todos os candidatos da clínica. Retorna stats.

    - Skip silencioso se WhatsApp da clínica não está conectado (sem erro).
    - Cada envio = 1 Interacao não é criada (Recall é canal próprio, registramos em
      `RecallEnviado` + AuditLog). Mantém `interacoes` focada em conversas com
      agendamento.
    - NÃO comita por candidato — comita 1x no fim. Mais rápido + atômico por clínica.
    """
    if hoje is None:
        hoje = agora_utc()

    if not clinica.recall_ativo or not clinica.ativo:
        return RecallStats(enviados=0, falhas=0, candidatos=0)

    if not clinica.evolution_conectado:
        log.info("recall: clinica=%s WhatsApp offline, skip", clinica.id)
        return RecallStats(enviados=0, falhas=0, candidatos=0, skip_whatsapp_offline=True)

    candidatos = candidatos_recall(db, clinica, hoje)
    if not candidatos:
        return RecallStats(enviados=0, falhas=0, candidatos=0)

    # Import lazy pra evitar custo de httpx em import-time + facilitar testes
    from services.whatsapp import WhatsAppService

    ws = WhatsAppService()
    enviados = 0
    falhas = 0

    for paciente, prontuario in candidatos:
        # Defesa em profundidade: re-checa opt_out (poderia ter mudado)
        if paciente.opt_out or paciente.deletado_em is not None:
            continue
        mensagem = _renderizar(clinica.recall_template, paciente, clinica)
        # Captura antes do commit (expire_on_commit invalidaria os objetos ORM).
        instance_name = clinica.evolution_instance_name
        telefone = paciente.telefone

        # Claim-before-send: grava o RecallEnviado e COMMITA antes de disparar.
        # Se o envio falhar/der timeout, o paciente entra no cooldown e NÃO recebe
        # o recall duplicado — preferimos perder um envio a mandar duas vezes.
        db.add(RecallEnviado(
            clinica_id=clinica.id,
            paciente_id=paciente.id,
            prontuario_origem_id=prontuario.id,
            enviado_em=hoje,
            mensagem=mensagem,
        ))
        audit.log(
            db,
            clinica_id=clinica.id,
            usuario_id=None,  # disparado por cron, sem usuário
            acao=AcaoAudit.CREATE,
            recurso="recall_whatsapp",
            recurso_id=paciente.id,
            detalhes={
                "prontuario_origem_id": prontuario.id,
                "telefone_hash": telefone[-4:],  # só últimos 4 dígitos no audit (LGPD)
                "intervalo_dias": clinica.recall_intervalo_dias,
                "chave": clinica.recall_procedimento_chave,
            },
        )
        db.commit()

        try:
            resultado = ws.enviar_mensagem(
                instance_name=instance_name,
                telefone=telefone,
                mensagem=mensagem,
            )
        except Exception as e:  # noqa: BLE001 — defensivo, não estoura cron
            log.exception("recall: erro enviando pra paciente=%s: %s", paciente.id, e)
            falhas += 1
            continue

        if not resultado.get("success"):
            log.warning(
                "recall: falha envio paciente=%s clinica=%s err=%s",
                paciente.id, clinica.id, resultado.get("error"),
            )
            falhas += 1
            continue

        enviados += 1

    return RecallStats(enviados=enviados, falhas=falhas, candidatos=len(candidatos))


def contar_recalls_recentes(db: Session, clinica_id: str, dias: int = 30) -> int:
    """Quantos recalls foram disparados pra essa clínica nos últimos N dias."""
    desde = agora_utc() - timedelta(days=dias)
    return (
        db.query(func.count(RecallEnviado.id))
        .filter(
            RecallEnviado.clinica_id == clinica_id,
            RecallEnviado.enviado_em >= desde,
        )
        .scalar()
        or 0
    )
