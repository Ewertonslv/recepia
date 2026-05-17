"""Testes da função mascara_pii — LGPD CRÍTICO.

Antes de qualquer texto ir pra Groq (servidores EUA), PII brasileiro
DEVE ser substituído por placeholders.
"""
import pytest

from services.processor import mascara_pii


class TestMascaraTelefone:
    def test_telefone_completo_com_ddd_e_9(self):
        out = mascara_pii("meu zap é 11 99999-8888")
        assert "[TELEFONE]" in out
        assert "99999" not in out

    def test_telefone_sem_traco(self):
        out = mascara_pii("liga 11999998888")
        assert "[TELEFONE]" in out

    def test_telefone_com_parenteses(self):
        out = mascara_pii("(11) 99999-8888")
        assert "[TELEFONE]" in out

    def test_telefone_fixo(self):
        out = mascara_pii("o fixo é 3232-1010")
        assert "[TELEFONE]" in out


class TestMascaraCPF:
    def test_cpf_formatado(self):
        out = mascara_pii("meu cpf 123.456.789-00")
        assert "[CPF]" in out
        assert "123.456.789" not in out

    def test_cpf_sem_formatacao(self):
        out = mascara_pii("cpf 12345678900")
        assert "[CPF]" in out


class TestMascaraEmail:
    def test_email_simples(self):
        out = mascara_pii("manda no email maria@gmail.com")
        assert "[EMAIL]" in out
        assert "maria@gmail.com" not in out

    def test_email_com_pontos(self):
        out = mascara_pii("maria.silva.santos@empresa.com.br")
        assert "[EMAIL]" in out


class TestMascaraCEP:
    def test_cep_com_traco(self):
        out = mascara_pii("meu cep é 01310-100")
        assert "[CEP]" in out
        assert "01310-100" not in out

    def test_cep_sem_traco(self):
        out = mascara_pii("01310100")
        assert "[CEP]" in out


class TestMascaraMultipla:
    def test_mensagem_com_multiplos_pii(self):
        texto = "Oi, sou a Maria, cpf 123.456.789-00, email maria@x.com, fone 11 99999-1234"
        out = mascara_pii(texto)
        assert "[CPF]" in out
        assert "[EMAIL]" in out
        assert "[TELEFONE]" in out
        assert "123.456.789" not in out
        assert "maria@x.com" not in out
        assert "Maria" in out  # nome NÃO é mascarado (intencional pra contexto)

    def test_texto_sem_pii_inalterado(self):
        texto = "sim, pode confirmar"
        assert mascara_pii(texto) == texto

    def test_texto_vazio(self):
        assert mascara_pii("") == ""
        assert mascara_pii(None) == ""
