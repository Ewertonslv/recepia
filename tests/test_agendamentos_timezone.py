"""Regressão do filtro de data do GET /api/agendamentos (fuso BR vs UTC).

Bug: os filtros `data`/`data_inicio`/`data_fim` representam DIAS do calendário BR,
mas `data_hora` é armazenado em UTC. Um agendamento às 21h BR do dia 4 é 00h UTC
do dia 5 — antes ele aparecia no dia 5 (errado) e sumia do dia 4.
"""


def _criar_paciente(client, headers):
    r = client.post("/api/pacientes", headers=headers,
                    json={"nome": "Fuso", "telefone": "5511977777777"})
    return r.json()


def _criar(client, headers, paciente_id, data_hora_br):
    # data_hora naive → o backend assume horário BR (to_utc_naive).
    return client.post("/api/agendamentos", headers=headers, json={
        "paciente_id": paciente_id, "data_hora": data_hora_br,
        "duracao_minutos": 30, "servico": "Consulta",
    })


class TestFiltroDataFusoBR:
    def test_agendamento_21h_br_aparece_no_dia_br_nao_no_dia_utc(self, client, auth_headers_a):
        p = _criar_paciente(client, auth_headers_a)
        # 21h BR do dia 04 == 00h UTC do dia 05.
        r = _criar(client, auth_headers_a, p["id"], "2026-07-04T21:00:00")
        assert r.status_code == 201

        # Deve aparecer filtrando pelo DIA BR (04)...
        no_dia_04 = client.get("/api/agendamentos?data=2026-07-04", headers=auth_headers_a).json()
        assert len(no_dia_04) == 1

        # ...e NÃO no dia 05 (que é onde o UTC cairia).
        no_dia_05 = client.get("/api/agendamentos?data=2026-07-05", headers=auth_headers_a).json()
        assert len(no_dia_05) == 0

    def test_intervalo_inicio_fim_inclui_dia_final_inteiro(self, client, auth_headers_a):
        p = _criar_paciente(client, auth_headers_a)
        _criar(client, auth_headers_a, p["id"], "2026-07-10T08:00:00")
        _criar(client, auth_headers_a, p["id"], "2026-07-10T22:30:00")  # noite → limite do dia

        r = client.get("/api/agendamentos?data_inicio=2026-07-10&data_fim=2026-07-10",
                       headers=auth_headers_a).json()
        assert len(r) == 2  # data_fim é inclusivo (dia inteiro)
