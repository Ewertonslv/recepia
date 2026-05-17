"""Testes do classificador IAProcessor.

GROQ_API_KEY vazia (definida no conftest) força o fallback regex,
então não chamamos a API externa.
"""
import pytest

from services.processor import (
    INTENCAO_NAO_ENTENDIDO,
    INTENCAO_REAGENDAR,
    IAProcessor,
)
from models import Status


@pytest.fixture
def processor():
    return IAProcessor()


# ===========================================================================
# CONFIRMADO
# ===========================================================================

class TestClassificarConfirmado:
    @pytest.mark.parametrize("msg", [
        "sim",
        "SIM",
        "Sim",
        "ok",
        "blz",
        "perfeito",
        "claro",
        "bom",
        "certo",
        "confirme",
        "Confirmo sim",
    ])
    def test_variantes_de_sim(self, processor, msg):
        assert processor.classificar_resposta(msg) == Status.CONFIRMADO


# ===========================================================================
# CANCELADO
# ===========================================================================

class TestClassificarCancelado:
    @pytest.mark.parametrize("msg", [
        "não",
        "nao",
        "NÃO",
        "cancelar",
        "cancelado",
        "não posso",
        "nao posso",
        "impossível",
        "impossivel",
    ])
    def test_variantes_de_nao(self, processor, msg):
        assert processor.classificar_resposta(msg) == Status.CANCELADO


# ===========================================================================
# REAGENDAR
# ===========================================================================

class TestClassificarReagendar:
    @pytest.mark.parametrize("msg", [
        "reagendar",
        "outro horário",
        "outro horario",
        "outro dia",
        "remarcar",
        "podemos mudar?",
    ])
    def test_variantes_de_reagendar(self, processor, msg):
        assert processor.classificar_resposta(msg) == INTENCAO_REAGENDAR


# ===========================================================================
# NAO ENTENDIDO
# ===========================================================================

class TestClassificarConfuso:
    @pytest.mark.parametrize("msg", [
        "talvez",
        "hmmm",
        "que horas mesmo?",
        "...",
        "asdfgh",
        "",
        "   ",
    ])
    def test_mensagem_confusa_retorna_nao_entendido(self, processor, msg):
        assert processor.classificar_resposta(msg) == INTENCAO_NAO_ENTENDIDO


# ===========================================================================
# Prioridade: reagendar tem precedência se houver ambiguidade
# ===========================================================================

class TestPrioridadeRegex:
    def test_reagendar_tem_prioridade_sobre_sim(self, processor):
        """Mensagem 'sim, mas em outro horário' deve cair em REAGENDAR."""
        result = processor.classificar_resposta("sim mas outro horário")
        assert result == INTENCAO_REAGENDAR

    def test_cancelado_tem_prioridade_sobre_confirmado(self, processor):
        """A regra atual avalia REAGENDAR > CANCELADO > CONFIRMADO."""
        result = processor.classificar_resposta("não confirmo")
        assert result == Status.CANCELADO


# ===========================================================================
# Cliente Groq não inicializado quando GROQ_API_KEY vazia
# ===========================================================================

class TestGroqInit:
    def test_sem_api_key_cliente_eh_none(self, processor):
        assert processor._client is None
