"""LGPD Art. 18 V — o export de portabilidade deve incluir TODAS as interações
do paciente, inclusive as fora de fluxo (agendamento_id NULL: boas-vindas,
opt-out, agendamento pelo bot), que antes ficavam de fora.
"""
from models import Interacao, Paciente


def _paciente(db_session, clinica):
    p = Paciente(clinica_id=clinica.id, nome="Ana", telefone="5511966665555")
    db_session.add(p)
    db_session.flush()
    return p


class TestExportInteracoesSemAgendamento:
    def test_interacao_sem_agendamento_entra_no_export(self, client, db_session, clinica_fake, auth_headers_a):
        clinica = clinica_fake["clinica"]
        p = _paciente(db_session, clinica)
        # Mensagem de boas-vindas: ligada ao paciente, SEM agendamento.
        db_session.add(Interacao(
            clinica_id=clinica.id, paciente_id=p.id, agendamento_id=None,
            tipo="resposta", mensagem_recebida="oi, quero marcar",
        ))
        db_session.commit()

        r = client.get(f"/api/pacientes/{p.id}/exportar", headers=auth_headers_a)
        assert r.status_code == 200
        interacoes = r.json()["interacoes"]
        assert len(interacoes) == 1
        assert interacoes[0]["mensagem_recebida"] == "oi, quero marcar"
        assert interacoes[0]["agendamento_id"] is None

    def test_timeline_inclui_interacao_sem_agendamento(self, client, db_session, clinica_fake, auth_headers_a):
        clinica = clinica_fake["clinica"]
        p = _paciente(db_session, clinica)
        db_session.add(Interacao(
            clinica_id=clinica.id, paciente_id=p.id, agendamento_id=None,
            tipo="opt_out", mensagem_recebida="parar",
        ))
        db_session.commit()

        r = client.get(f"/api/pacientes/{p.id}/timeline", headers=auth_headers_a)
        assert r.status_code == 200
        tipos = [i["tipo"] for i in r.json()["itens"]]
        assert "interacao" in tipos

    def test_interacao_de_outro_paciente_nao_vaza(self, client, db_session, clinica_fake, auth_headers_a):
        clinica = clinica_fake["clinica"]
        p1 = _paciente(db_session, clinica)
        p2 = Paciente(clinica_id=clinica.id, nome="Outro", telefone="5511955554444")
        db_session.add(p2)
        db_session.flush()
        db_session.add(Interacao(
            clinica_id=clinica.id, paciente_id=p2.id, agendamento_id=None,
            tipo="resposta", mensagem_recebida="segredo do p2",
        ))
        db_session.commit()

        r = client.get(f"/api/pacientes/{p1.id}/exportar", headers=auth_headers_a)
        assert r.status_code == 200
        assert r.json()["interacoes"] == []
