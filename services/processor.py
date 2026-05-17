"""Classificador de respostas via Groq (Llama 3.3 70B) com fallback regex.

LGPD CRÍTICO: PII (telefone, CPF, email, CEP) é mascarada ANTES de enviar pro Groq,
que processa em servidores nos EUA. Pra classificação de intenção, contexto numérico
não importa — só a INTENÇÃO da mensagem.
"""
import re

from groq import Groq

from config import settings
from models import Status

# Status especiais que não estão no enum Status (são "ações" internas)
INTENCAO_REAGENDAR = "reagendar"
INTENCAO_NAO_ENTENDIDO = "nao_entendido"
INTENCAO_OPT_OUT = "opt_out"  # LGPD Art. 8 §5


# ============================================================================
# PII Masking (LGPD)
# ============================================================================

# F5: regex melhorada — pega E.164 BR (5511988887777), formatos com separador, e seq longa de dígitos
_RE_TELEFONE = re.compile(
    r"\+?\d{0,3}[\s.\-]?\(?\d{2,3}\)?[\s.\-]?\d{4,5}[\s.\-]?\d{4}"
    r"|\b\d{10,13}\b"
)
_RE_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}|\b\d{11}\b")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_CEP = re.compile(r"\d{5}-?\d{3}")


def mascara_pii(texto: str) -> str:
    """Substitui PII por placeholders antes de enviar a LLM externa."""
    if not texto:
        return ""
    texto = _RE_EMAIL.sub("[EMAIL]", texto)
    texto = _RE_CPF.sub("[CPF]", texto)
    texto = _RE_CEP.sub("[CEP]", texto)
    texto = _RE_TELEFONE.sub("[TELEFONE]", texto)
    return texto


# ============================================================================
# Classificação via Groq (Llama 3.3 70B)
# ============================================================================

_PROMPT_CLASSIFICAR = """Você classifica respostas de pacientes de clínica de estética sobre confirmação de consulta.

Categorias possíveis (responda APENAS o nome da categoria, em CAPS):
- CONFIRMADO  → paciente confirma presença (sim, ok, blz, confirmado, vou sim, tô indo, perfeito)
- CANCELADO   → paciente cancela (não, não posso, cancelar, não vou conseguir, infelizmente)
- REAGENDAR   → paciente quer mudar horário (reagendar, outro horário, outro dia, posso amanhã?)
- DUVIDA      → paciente faz pergunta ou está confuso (que horas mesmo?, quanto custa?, onde fica?)
- OPT_OUT     → paciente quer parar de receber mensagens automáticas (sair, parar, descadastrar, não me mande mais mensagem, cancelar inscrição)
- OUTRO       → mensagem fora do contexto, propaganda, spam

Mensagem do paciente: "{mensagem}"

Categoria:"""


class IAProcessor:
    def __init__(self):
        self._client: Groq | None = None
        if settings.GROQ_API_KEY:
            self._client = Groq(api_key=settings.GROQ_API_KEY)

    # ------------------------------------------------------------------ public

    def classificar_resposta(self, mensagem: str) -> str:
        """Retorna: Status.CONFIRMADO | Status.CANCELADO | 'reagendar' | 'nao_entendido'."""
        if not mensagem or not mensagem.strip():
            return INTENCAO_NAO_ENTENDIDO

        if self._client:
            try:
                resultado = self._classificar_via_groq(mensagem)
                if resultado:
                    return resultado
            except Exception:
                pass  # cai pro fallback regex

        return self._classificar_regex(mensagem)

    # ------------------------------------------------------------------ groq

    def _classificar_via_groq(self, mensagem: str) -> str | None:
        mensagem_segura = mascara_pii(mensagem)[:500]
        prompt = _PROMPT_CLASSIFICAR.format(mensagem=mensagem_segura)

        completion = self._client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        resposta = (completion.choices[0].message.content or "").strip().upper()

        mapa = {
            "OPT_OUT": INTENCAO_OPT_OUT,  # check antes pra não casar em "CONFIRMADO" se vier "NÃO QUERO MAIS"
            "CONFIRMADO": Status.CONFIRMADO,
            "CANCELADO": Status.CANCELADO,
            "REAGENDAR": INTENCAO_REAGENDAR,
            "DUVIDA": INTENCAO_NAO_ENTENDIDO,
            "OUTRO": INTENCAO_NAO_ENTENDIDO,
        }
        for chave, valor in mapa.items():
            if chave in resposta:
                return valor
        return None

    # ------------------------------------------------------------------ fallback regex

    _PATTERNS_CONFIRMADO = [r"\bsim\b", r"\bconfirme?\b", r"\bok\b", r"\bblz\b",
                            r"\bcerto\b", r"\bbom\b", r"\bperfeito\b", r"\bclaro\b"]
    _PATTERNS_CANCELADO = [r"\bn[ãa]o\b", r"\bcancelar?\b", r"\bcancelado\b",
                           r"\bnão posso\b", r"\bnao posso\b", r"\bimposs[ií]vel\b"]
    _PATTERNS_REAGENDAR = [r"\breagendar\b", r"\boutro hor[aá]rio\b",
                           r"\boutro dia\b", r"\bremarcar\b", r"\bmudar\b"]
    _PATTERNS_OPT_OUT = [r"\bsair\b", r"\bparar\b", r"\bn[aã]o me mande?\b",
                         r"\bdescadastra[rt]?\b", r"\bcancelar inscri", r"\bunsubscribe\b",
                         r"\bpare de mandar\b"]

    def _classificar_regex(self, mensagem: str) -> str:
        m = mensagem.lower().strip()
        # opt-out PRIMEIRO pra ter precedência sobre cancelado/confirmado
        for p in self._PATTERNS_OPT_OUT:
            if re.search(p, m):
                return INTENCAO_OPT_OUT
        for p in self._PATTERNS_REAGENDAR:
            if re.search(p, m):
                return INTENCAO_REAGENDAR
        for p in self._PATTERNS_CANCELADO:
            if re.search(p, m):
                return Status.CANCELADO
        for p in self._PATTERNS_CONFIRMADO:
            if re.search(p, m):
                return Status.CONFIRMADO
        return INTENCAO_NAO_ENTENDIDO
