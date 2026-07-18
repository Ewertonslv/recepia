"""job_marcar_no_show só deve promover agendamentos de clínicas ATIVAS.

Uma clínica desativada (churn) não pode continuar tendo CONFIRMADO→REALIZADO
promovido automaticamente (isso alimentaria comissão/financeiro por presunção).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from cron import jobs
from models import Agendamento, Status


def _ag(db, clinica_id, status):
    from models import Paciente
    p = Paciente(clinica_id=clinica_id, nome="X", telefone="5511900002222")
    db.add(p)
    db.flush()
    ag = Agendamento(
        clinica_id=clinica_id, paciente_id=p.id,
        data_hora=datetime.utcnow() - timedelta(hours=1),  # já passou > 30min
        status=status,
    )
    db.add(ag)
    db.flush()
    return ag


@pytest.fixture
def _patch_get_db(monkeypatch, db_session):
    @contextmanager
    def _fake_get_db():
        yield db_session
    monkeypatch.setattr(jobs, "get_db", _fake_get_db)


def test_promove_clinica_ativa(_patch_get_db, db_session, clinica_fake):
    c = clinica_fake["clinica"]
    assert c.ativo is True
    ag_p = _ag(db_session, c.id, Status.PENDENTE)
    ag_c = _ag(db_session, c.id, Status.CONFIRMADO)
    db_session.commit()

    jobs.job_marcar_no_show()

    db_session.expire_all()
    assert db_session.get(Agendamento, ag_p.id).status == Status.NO_SHOW
    assert db_session.get(Agendamento, ag_c.id).status == Status.REALIZADO


def test_nao_promove_clinica_inativa(_patch_get_db, db_session, clinica_fake):
    c = clinica_fake["clinica"]
    c.ativo = False
    db_session.flush()
    ag_p = _ag(db_session, c.id, Status.PENDENTE)
    ag_c = _ag(db_session, c.id, Status.CONFIRMADO)
    db_session.commit()

    jobs.job_marcar_no_show()

    db_session.expire_all()
    # Status intactos — clínica inativa é ignorada.
    assert db_session.get(Agendamento, ag_p.id).status == Status.PENDENTE
    assert db_session.get(Agendamento, ag_c.id).status == Status.CONFIRMADO
