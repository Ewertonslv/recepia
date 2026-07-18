"""Cron interno — usa APScheduler dentro do mesmo processo da API.

Roda como worker separado (Docker service: recepia-worker), chamando
SchedulerService diretamente (sem HTTP).

Jobs:
- confirmacoes: */15 min — manda confirmação 24h antes
- lembretes: */1h — lembrete 2h antes pra quem não respondeu
- no_show: */30 min — auto-marca PENDENTE/CONFIRMADO que já passou
- purgar_pacientes_deletados: diário 03:00 — hard delete pacientes com soft delete > 30d
"""
import logging
import os
import sys
from datetime import timedelta

# Garante que /app (raiz do projeto) está no sys.path quando rodando via `python cron/jobs.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from core.timezones import agora_utc
from database import get_db
from models import Agendamento, Clinica, EstadoConversa, Paciente, Status
from services.scheduler import SchedulerService

log = logging.getLogger("recepia.cron")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def job_enviar_confirmacoes():
    """Pega agendamentos próximos e dispara confirmação."""
    with get_db() as db:
        scheduler = SchedulerService(db)
        agendamentos = scheduler.buscar_agendamentos_pra_confirmar()
        enviados = sum(1 for ag in agendamentos if scheduler.enviar_confirmacao(ag))
        log.info("confirmacoes: avaliados=%d enviados=%d", len(agendamentos), enviados)


def job_enviar_lembretes():
    """Lembrete 2h antes pra quem não confirmou ainda."""
    with get_db() as db:
        scheduler = SchedulerService(db)
        agendamentos = scheduler.buscar_agendamentos_pra_lembrete()
        enviados = sum(1 for ag in agendamentos if scheduler.enviar_lembrete(ag))
        log.info("lembretes: avaliados=%d enviados=%d", len(agendamentos), enviados)


def job_marcar_no_show():
    """G8: auto-promove agendamentos que já passaram.

    - PENDENTE + 30min passado → NO_SHOW (não respondeu, não veio)
    - CONFIRMADO + 30min passado → REALIZADO (confirmou, presumimos que veio)

    Só mexe em agendamentos de clínicas ATIVAS: uma clínica desativada/em churn
    não deve continuar tendo status (e comissão/financeiro derivados) promovidos
    automaticamente com base numa presunção de presença.
    """
    with get_db() as db:
        threshold = agora_utc() - timedelta(minutes=30)

        pendentes = (
            db.query(Agendamento)
            .join(Clinica, Agendamento.clinica_id == Clinica.id)
            .filter(
                Clinica.ativo.is_(True),
                Agendamento.status == Status.PENDENTE,
                Agendamento.data_hora < threshold,
            )
            .all()
        )
        for a in pendentes:
            a.status = Status.NO_SHOW

        confirmados = (
            db.query(Agendamento)
            .join(Clinica, Agendamento.clinica_id == Clinica.id)
            .filter(
                Clinica.ativo.is_(True),
                Agendamento.status == Status.CONFIRMADO,
                Agendamento.data_hora < threshold,
            )
            .all()
        )
        for a in confirmados:
            a.status = Status.REALIZADO

        db.commit()
        log.info("no_show: NO_SHOW=%d REALIZADO=%d", len(pendentes), len(confirmados))


def job_purgar_pacientes_deletados():
    """LGPD: hard delete de pacientes soft-deletados há mais de 30 dias."""
    with get_db() as db:
        threshold = agora_utc() - timedelta(days=30)
        deletados = db.query(Paciente).filter(
            Paciente.deletado_em.isnot(None),
            Paciente.deletado_em < threshold,
        ).all()
        for p in deletados:
            db.delete(p)
        db.commit()
        log.info("purgar_pacientes: hard_deletados=%d", len(deletados))


def job_recall_diario():
    """Sprint 3: dispara recalls pra clínicas com recall_ativo=True.

    Vale pra QUALQUER vertical que ative o recall (odonto = limpeza 6m,
    estética = retoque botox 4m, fisio = manutenção 3m, etc). A regra
    fica por conta de `recall_intervalo_dias`/`recall_procedimento_chave`
    configurados pela clínica.
    """
    from core.recall import processar_recall  # import lazy: evita ciclo em startup

    with get_db() as db:
        clinicas = (
            db.query(Clinica)
            .filter(
                Clinica.ativo.is_(True),
                Clinica.recall_ativo.is_(True),
            )
            .all()
        )
        total_enviados = 0
        total_falhas = 0
        total_clinicas = 0
        for clinica in clinicas:
            try:
                stats = processar_recall(db, clinica, hoje=agora_utc())
            except Exception:  # noqa: BLE001
                log.exception("recall: clinica=%s falhou", clinica.id)
                continue
            total_clinicas += 1
            total_enviados += stats.enviados
            total_falhas += stats.falhas
            if stats.enviados or stats.falhas:
                log.info(
                    "recall: clinica=%s enviados=%d falhas=%d candidatos=%d",
                    clinica.id, stats.enviados, stats.falhas, stats.candidatos,
                )
        log.info(
            "recall_diario: clinicas=%d enviados=%d falhas=%d",
            total_clinicas, total_enviados, total_falhas,
        )


def job_limpar_estados_expirados():
    """G1: apaga EstadoConversa expirado (>24h). Roda a cada hora."""
    with get_db() as db:
        agora = agora_utc()
        expirados = db.query(EstadoConversa).filter(
            EstadoConversa.expira_em < agora,
        ).all()
        for e in expirados:
            db.delete(e)
        db.commit()
        log.info("limpar_estados: removidos=%d", len(expirados))


# Instância módulo-level: facilita inspeção / pré-flight (`scheduler.get_jobs()`).
# add_job é idempotente apenas pelo `id` quando `replace_existing=True`.
scheduler = BlockingScheduler(timezone="America/Sao_Paulo")
scheduler.add_job(job_enviar_confirmacoes, CronTrigger(minute="*/15"), id="confirmacoes", replace_existing=True)
scheduler.add_job(job_enviar_lembretes, CronTrigger(hour="*"), id="lembretes", replace_existing=True)
scheduler.add_job(job_marcar_no_show, CronTrigger(minute="*/30"), id="no_show", replace_existing=True)
scheduler.add_job(job_limpar_estados_expirados, CronTrigger(minute=10), id="limpar_estados", replace_existing=True)
scheduler.add_job(job_purgar_pacientes_deletados, CronTrigger(hour=3, minute=0), id="purgar", replace_existing=True)
# Sprint 3 — Recall: 1x/dia 10:00 BRT (horário comercial, voz humana ainda quente).
scheduler.add_job(job_recall_diario, CronTrigger(hour=10, minute=0), id="recall_dentista", replace_existing=True)


def start_scheduler():
    log.info("Recepia scheduler iniciado")
    scheduler.start()


if __name__ == "__main__":
    start_scheduler()
