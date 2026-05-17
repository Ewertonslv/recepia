"""Normalização de telefone (B9).

Política: TUDO armazenado como `55DDDNNNNNNNNN` (só dígitos, com DDI 55).
- Entrada: aceita qualquer formato (+55 11 99999-9999, (11) 99999-9999, 11999999999, etc).
- Saída: sempre 13 dígitos no padrão E.164 sem o `+`.
- Evolution API recebe e devolve nesse mesmo formato (sem `+`).
"""
import re


_RE_NAO_DIGITOS = re.compile(r"\D+")


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
    if not (11 <= int(ddd) <= 99):
        raise TelefoneInvalido(f"DDD inválido: {ddd}")

    return digitos


def tenta_normalizar(telefone: str) -> str | None:
    """Versão silenciosa. Retorna None se inválido."""
    try:
        return normalizar(telefone)
    except TelefoneInvalido:
        return None
