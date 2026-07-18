"""Normalização de telefone (B9).

Política: TUDO armazenado como `55DDDNNNNNNNNN` (só dígitos, com DDI 55).
- Entrada: aceita qualquer formato (+55 11 99999-9999, (11) 99999-9999, 11999999999, etc).
- Saída: sempre 13 dígitos no padrão E.164 sem o `+`.
- Evolution API recebe e devolve nesse mesmo formato (sem `+`).
"""
import re


_RE_NAO_DIGITOS = re.compile(r"\D+")

# DDDs que existem no plano de numeração da Anatel. A faixa 11-99 aceitava
# dezenas de códigos inexistentes (20, 23, 25, 26, 29, 30, 36...) — typo no
# cadastro virava número impossível e o envio falhava silenciosamente.
_DDDS_VALIDOS = frozenset({
    11, 12, 13, 14, 15, 16, 17, 18, 19,          # SP
    21, 22, 24, 27, 28,                           # RJ / ES
    31, 32, 33, 34, 35, 37, 38,                   # MG
    41, 42, 43, 44, 45, 46, 47, 48, 49,           # PR / SC
    51, 53, 54, 55,                               # RS
    61, 62, 63, 64, 65, 66, 67, 68, 69,           # DF / GO / TO / MT / MS / AC / RO
    71, 73, 74, 75, 77, 79,                       # BA / SE
    81, 82, 83, 84, 85, 86, 87, 88, 89,           # PE / AL / PB / RN / CE / PI
    91, 92, 93, 94, 95, 96, 97, 98, 99,           # PA / AM / RR / AP / MA
})


class TelefoneInvalido(ValueError):
    pass


def normalizar(telefone: str) -> str:
    """`(11) 99999-9999` -> `5511999999999`. Lança TelefoneInvalido se não bater.

    Aceita:
    - `5511999999999` (já correto)
    - `+55 11 99999-9999`
    - `(11) 99999-9999`
    - `11999999999` (sem DDI, adiciona 55)
    - `+5511988887777` etc

    Rejeita:
    - Menos de 10 ou mais de 13 dígitos
    - Sem DDD válido (11-99)
    """
    if not telefone:
        raise TelefoneInvalido("telefone vazio")

    digitos = _RE_NAO_DIGITOS.sub("", telefone)

    if not digitos:
        raise TelefoneInvalido("nenhum dígito encontrado")

    # Se tem 10 ou 11 dígitos, adiciona DDI 55
    if len(digitos) in (10, 11):
        digitos = "55" + digitos

    if len(digitos) not in (12, 13):
        raise TelefoneInvalido(f"comprimento inválido: {len(digitos)} dígitos")

    if not digitos.startswith("55"):
        raise TelefoneInvalido("DDI deve ser 55 (Brasil)")

    ddd = digitos[2:4]
    if int(ddd) not in _DDDS_VALIDOS:
        raise TelefoneInvalido(f"DDD inválido: {ddd}")

    # 13 dígitos = celular (9 dígitos no assinante) — o 5º dígito TEM que ser 9.
    # Sem essa checagem, `5511812345678` passava como se fosse um número real.
    if len(digitos) == 13 and digitos[4] != "9":
        raise TelefoneInvalido("número de 13 dígitos precisa do 9º dígito (celular)")

    # 9º dígito (B9): a Evolution/Baileys às vezes entrega o celular SEM o 9
    # (12 dígitos: 55 + DDD + 8), enquanto o paciente foi salvo COM o 9 (13).
    # Sem reconciliar, o webhook trata um paciente conhecido como novo contato e
    # duplica o cadastro. Canonicalizamos celulares sempre PARA 13 dígitos:
    # um número de 8 dígitos começando em 6-9 é celular que perdeu o 9 → inserimos.
    # (2-5 é fixo — mantém 12.)
    if len(digitos) == 12 and digitos[4] in "6789":
        digitos = digitos[:4] + "9" + digitos[4:]

    return digitos


def tenta_normalizar(telefone: str) -> str | None:
    """Versão silenciosa. Retorna None se inválido."""
    try:
        return normalizar(telefone)
    except TelefoneInvalido:
        return None
