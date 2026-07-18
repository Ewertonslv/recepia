"""Testes de normalização de telefone (B9) — foco no 9º dígito de celular.

O bug real: a Evolution às vezes entrega o número SEM o 9 (12 dígitos), enquanto
o paciente foi salvo COM o 9 (13). Sem canonicalizar, o webhook duplica o cadastro.
"""
import pytest

from core.phones import TelefoneInvalido, normalizar, tenta_normalizar


class TestFormatosBasicos:
    @pytest.mark.parametrize("entrada,esperado", [
        ("5511999998888", "5511999998888"),      # já correto (13)
        ("+55 11 99999-8888", "5511999998888"),   # formatado
        ("(11) 99999-8888", "5511999998888"),      # sem DDI → adiciona 55
        ("11999998888", "5511999998888"),          # sem DDI
        ("+5511999998888", "5511999998888"),
    ])
    def test_normaliza_para_e164_sem_mais(self, entrada, esperado):
        assert normalizar(entrada) == esperado


class TestNonoDigito:
    def test_celular_sem_9_recebe_9(self):
        # 12 dígitos (55 + DDD 11 + 8), parte começa em 9 → celular sem o 9º dígito.
        assert normalizar("551199998888") == "5511999998888"

    def test_celular_sem_9_comecando_8(self):
        # celular legado começando em 8 também ganha o 9.
        assert normalizar("551188887777") == "5511988887777"

    def test_convergencia_com_e_sem_9(self):
        # As duas formas do MESMO número devem canonicalizar pro mesmo valor —
        # é isso que impede o cadastro duplicado no webhook.
        assert normalizar("551199998888") == normalizar("5511999998888")

    def test_fixo_8_digitos_nao_ganha_9(self):
        # Fixo (parte começa em 2-5) permanece com 12 dígitos.
        assert normalizar("551133334444") == "551133334444"


class TestInvalidos:
    @pytest.mark.parametrize("entrada", ["", "   ", "abc", "123", "5521" * 5])
    def test_rejeita(self, entrada):
        assert tenta_normalizar(entrada) is None

    def test_ddi_nao_br_rejeita(self):
        with pytest.raises(TelefoneInvalido):
            normalizar("441199998888")  # 12 dígitos mas DDI 44 (não é Brasil)

    def test_ddd_invalido_rejeita(self):
        with pytest.raises(TelefoneInvalido):
            normalizar("5500999998888")  # DDD 00
