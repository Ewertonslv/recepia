"""Utilitários de documentos brasileiros (CPF, CEP).

CPF é armazenado SEM máscara (11 dígitos). Aceita input com máscara mas sanitiza.
Validação dos dígitos verificadores evita lixo na base e abre caminho pra NF-e futuro.
"""
import re


def sanitizar_cpf(s: str | None) -> str | None:
    if not s:
        return None
    so_digitos = re.sub(r"\D", "", s)
    return so_digitos or None


def validar_cpf(cpf: str) -> bool:
    """Valida dígitos verificadores. Espera 11 dígitos sem máscara."""
    if not cpf or len(cpf) != 11 or not cpf.isdigit():
        return False
    if cpf == cpf[0] * 11:  # 11111111111 etc são inválidos
        return False
    for i in (9, 10):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        dig = (soma * 10) % 11
        if dig == 10:
            dig = 0
        if dig != int(cpf[i]):
            return False
    return True


def formatar_cpf(cpf: str | None) -> str | None:
    """Volta máscara pra exibição. None se inválido."""
    if not cpf or len(cpf) != 11:
        return None
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def sanitizar_cep(s: str | None) -> str | None:
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    return d if len(d) == 8 else None


def formatar_cep(cep: str | None) -> str | None:
    if not cep or len(cep) != 8:
        return None
    return f"{cep[:5]}-{cep[5:]}"
