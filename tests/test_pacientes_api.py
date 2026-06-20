"""Testes CRUD de pacientes + ISOLAMENTO MULTI-TENANT (crítico).

A regra de ouro: clínica A NUNCA pode ver/editar/deletar paciente da clínica B.
"""
import pytest


def _criar_paciente(client, headers, nome="Maria Silva", telefone="5511999990000"):
    return client.post(
        "/api/pacientes",
        headers=headers,
        json={"nome": nome, "telefone": telefone, "email": "maria@x.com"},
    )


# ===========================================================================
# CRUD básico (scoped pra clínica do token)
# ===========================================================================

class TestPacienteCRUD:
    def test_criar_paciente(self, client, auth_headers_a):
        resp = _criar_paciente(client, auth_headers_a)
        assert resp.status_code == 201
        body = resp.json()
        assert body["nome"] == "Maria Silva"
        assert body["telefone"] == "5511999990000"
        assert body["email"] == "maria@x.com"
        assert "id" in body

    def test_criar_paciente_sem_nome_rejeita(self, client, auth_headers_a):
        resp = client.post(
            "/api/pacientes", headers=auth_headers_a,
            json={"telefone": "5511999990000"},
        )
        assert resp.status_code == 422

    def test_criar_paciente_telefone_curto_rejeita(self, client, auth_headers_a):
        resp = client.post(
            "/api/pacientes", headers=auth_headers_a,
            json={"nome": "Joana", "telefone": "123"},
        )
        assert resp.status_code == 422

    def test_listar_pacientes(self, client, auth_headers_a):
        _criar_paciente(client, auth_headers_a, "Ana", "5511991111111")
        _criar_paciente(client, auth_headers_a, "Bia", "5511992222222")
        resp = client.get("/api/pacientes", headers=auth_headers_a)
        assert resp.status_code == 200
        nomes = [p["nome"] for p in resp.json()]
        assert "Ana" in nomes
        assert "Bia" in nomes

    def test_obter_paciente(self, client, auth_headers_a):
        criado = _criar_paciente(client, auth_headers_a).json()
        resp = client.get(f"/api/pacientes/{criado['id']}", headers=auth_headers_a)
        assert resp.status_code == 200
        assert resp.json()["id"] == criado["id"]

    def test_obter_paciente_inexistente_404(self, client, auth_headers_a):
        resp = client.get("/api/pacientes/id-fake", headers=auth_headers_a)
        assert resp.status_code == 404

    def test_atualizar_paciente(self, client, auth_headers_a):
        criado = _criar_paciente(client, auth_headers_a).json()
        resp = client.put(
            f"/api/pacientes/{criado['id']}",
            headers=auth_headers_a,
            json={"nome": "Maria Editada", "telefone": "5511988888888"},
        )
        assert resp.status_code == 200
        assert resp.json()["nome"] == "Maria Editada"
        assert resp.json()["telefone"] == "5511988888888"

    def test_deletar_paciente(self, client, auth_headers_a):
        criado = _criar_paciente(client, auth_headers_a).json()
        resp = client.delete(f"/api/pacientes/{criado['id']}", headers=auth_headers_a)
        assert resp.status_code == 204
        # Soft-delete (LGPD): some da listagem padrão (que filtra deletado_em).
        lista = client.get("/api/pacientes", headers=auth_headers_a).json()
        assert criado["id"] not in [p["id"] for p in lista]


# ===========================================================================
# ISOLAMENTO MULTI-TENANT (CRITICO)
# ===========================================================================

class TestIsolamentoMultiTenant:
    def test_listar_so_retorna_pacientes_da_propria_clinica(
        self, client, auth_headers_a, auth_headers_b
    ):
        _criar_paciente(client, auth_headers_a, "Paciente A", "5511990000001")
        _criar_paciente(client, auth_headers_b, "Paciente B", "5511990000002")

        resp_a = client.get("/api/pacientes", headers=auth_headers_a)
        resp_b = client.get("/api/pacientes", headers=auth_headers_b)

        nomes_a = {p["nome"] for p in resp_a.json()}
        nomes_b = {p["nome"] for p in resp_b.json()}

        assert "Paciente A" in nomes_a
        assert "Paciente A" not in nomes_b  # CRITICAL
        assert "Paciente B" in nomes_b
        assert "Paciente B" not in nomes_a  # CRITICAL

    def test_obter_paciente_de_outra_clinica_404(
        self, client, auth_headers_a, auth_headers_b
    ):
        criado_a = _criar_paciente(client, auth_headers_a, "Da clinica A").json()
        # Clínica B tenta acessar paciente da A
        resp = client.get(f"/api/pacientes/{criado_a['id']}", headers=auth_headers_b)
        assert resp.status_code == 404  # NÃO pode vazar nem 403/200

    def test_atualizar_paciente_de_outra_clinica_404(
        self, client, auth_headers_a, auth_headers_b
    ):
        criado_a = _criar_paciente(client, auth_headers_a).json()
        resp = client.put(
            f"/api/pacientes/{criado_a['id']}",
            headers=auth_headers_b,
            json={"nome": "HACK", "telefone": "5511900000000"},
        )
        assert resp.status_code == 404

    def test_deletar_paciente_de_outra_clinica_404(
        self, client, auth_headers_a, auth_headers_b
    ):
        criado_a = _criar_paciente(client, auth_headers_a).json()
        resp = client.delete(f"/api/pacientes/{criado_a['id']}", headers=auth_headers_b)
        assert resp.status_code == 404
        # confirma que NÃO foi apagado
        check = client.get(f"/api/pacientes/{criado_a['id']}", headers=auth_headers_a)
        assert check.status_code == 200


# ===========================================================================
# VALIDAÇÃO DE CAMPOS POR ESPECIALIDADE
# Odonto exige CPF + data_nascimento (sem o override que clinica_fake aplica).
# ===========================================================================

@pytest.fixture
def headers_odonto_estrita(db_session):
    """Clínica odonto SEM override de config_paciente → cpf/nascimento obrigatórios."""
    from models import Clinica, Usuario
    from core.security import criar_token, hash_senha
    from seeds import aplicar_configuracoes_default

    clinica = Clinica(
        nome="Odonto Estrita", cnpj="33333333000133",
        especialidade="odonto", config_paciente={},
    )
    db_session.add(clinica)
    db_session.flush()
    clinica.evolution_instance_name = f"clinica-{clinica.id[:8]}"
    usuario = Usuario(
        clinica_id=clinica.id, email="admin@odontoestrita.com",
        senha_hash=hash_senha("senha12345"), nome="Admin Odonto", role="admin",
    )
    db_session.add(usuario)
    aplicar_configuracoes_default(db_session, clinica.id)
    db_session.commit()
    db_session.refresh(usuario)
    return {"Authorization": f"Bearer {criar_token(usuario.id, usuario.clinica_id, usuario.role)}"}


class TestValidacaoPorEspecialidade:
    def test_odonto_sem_campos_obrigatorios_rejeita(self, client, headers_odonto_estrita):
        resp = client.post(
            "/api/pacientes", headers=headers_odonto_estrita,
            json={"nome": "Maria Silva", "telefone": "5511999990000"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "campos_faltantes" in detail
        assert "cpf" in detail["campos_faltantes"]
        assert "data_nascimento" in detail["campos_faltantes"]

    def test_odonto_com_cpf_e_nascimento_cria(self, client, headers_odonto_estrita):
        resp = client.post(
            "/api/pacientes", headers=headers_odonto_estrita,
            json={
                "nome": "Maria Silva", "telefone": "5511999990000",
                "cpf": "11144477735",  # CPF de teste válido (passa no dígito verificador)
                "data_nascimento": "1990-01-01",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["cpf"] == "11144477735"
