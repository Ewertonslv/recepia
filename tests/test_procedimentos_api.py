"""Testes do catálogo de procedimentos (Sprint 9).

Cobre: CRUD, unicidade case-insensitive por clínica, soft-deactivate +
filtro apenas_ativos, validações e isolamento multi-tenant.
"""


def _criar(client, headers, nome="Limpeza", duracao=30, cor="#E8B4B8"):
    return client.post(
        "/api/procedimentos", headers=headers,
        json={"nome": nome, "duracao_minutos": duracao, "cor": cor},
    )


class TestProcedimentoCRUD:
    def test_criar(self, client, auth_headers_a):
        resp = _criar(client, auth_headers_a)
        assert resp.status_code == 201
        body = resp.json()
        assert body["nome"] == "Limpeza"
        assert body["duracao_minutos"] == 30
        assert body["ativo"] is True

    def test_nome_duplicado_case_insensitive_409(self, client, auth_headers_a):
        _criar(client, auth_headers_a, "Limpeza")
        resp = _criar(client, auth_headers_a, "limpeza")  # mesmo nome, caixa diferente
        assert resp.status_code == 409

    def test_nome_curto_rejeita(self, client, auth_headers_a):
        resp = _criar(client, auth_headers_a, "L")
        assert resp.status_code == 422

    def test_cor_invalida_rejeita(self, client, auth_headers_a):
        resp = _criar(client, auth_headers_a, "Canal", cor="vermelho")
        assert resp.status_code == 422

    def test_listar(self, client, auth_headers_a):
        _criar(client, auth_headers_a, "Limpeza")
        _criar(client, auth_headers_a, "Avaliacao")
        resp = client.get("/api/procedimentos", headers=auth_headers_a)
        assert resp.status_code == 200
        assert {p["nome"] for p in resp.json()} == {"Limpeza", "Avaliacao"}

    def test_atualizar(self, client, auth_headers_a):
        proc = _criar(client, auth_headers_a).json()
        resp = client.put(
            f"/api/procedimentos/{proc['id']}", headers=auth_headers_a,
            json={"duracao_minutos": 60},
        )
        assert resp.status_code == 200
        assert resp.json()["duracao_minutos"] == 60

    def test_remover(self, client, auth_headers_a):
        proc = _criar(client, auth_headers_a).json()
        resp = client.delete(f"/api/procedimentos/{proc['id']}", headers=auth_headers_a)
        assert resp.status_code == 204


class TestProcedimentoSoftDeactivate:
    def test_inativo_some_da_listagem_padrao(self, client, auth_headers_a):
        proc = _criar(client, auth_headers_a).json()
        client.put(
            f"/api/procedimentos/{proc['id']}", headers=auth_headers_a,
            json={"ativo": False},
        )
        ativos = client.get("/api/procedimentos", headers=auth_headers_a).json()
        assert proc["id"] not in [p["id"] for p in ativos]

        todos = client.get(
            "/api/procedimentos?apenas_ativos=false", headers=auth_headers_a
        ).json()
        assert proc["id"] in [p["id"] for p in todos]


class TestProcedimentoIsolamento:
    def test_listar_so_da_propria_clinica(self, client, auth_headers_a, auth_headers_b):
        _criar(client, auth_headers_a, "Da A")
        _criar(client, auth_headers_b, "Da B")
        nomes_a = {p["nome"] for p in client.get("/api/procedimentos", headers=auth_headers_a).json()}
        assert "Da A" in nomes_a
        assert "Da B" not in nomes_a  # CRITICAL

    def test_atualizar_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        proc = _criar(client, auth_headers_a).json()
        resp = client.put(
            f"/api/procedimentos/{proc['id']}", headers=auth_headers_b,
            json={"duracao_minutos": 99},
        )
        assert resp.status_code == 404

    def test_remover_de_outra_clinica_404(self, client, auth_headers_a, auth_headers_b):
        proc = _criar(client, auth_headers_a).json()
        resp = client.delete(f"/api/procedimentos/{proc['id']}", headers=auth_headers_b)
        assert resp.status_code == 404
