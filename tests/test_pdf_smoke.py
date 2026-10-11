"""Smoke test do WeasyPrint: renderiza um template real e confere que saiu PDF.

WeasyPrint depende de libs nativas (Pango/Cairo). No CI elas estão instaladas e
o teste roda de verdade; numa máquina sem elas (ex.: Windows local) ele é pulado.
"""
import os
from datetime import date, datetime

import pytest


def test_gerar_pdf_declaracao_comparecimento():
    try:
        import weasyprint  # noqa: F401
    except OSError as e:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"libs nativas do WeasyPrint ausentes: {e}")

    from core.pdf import gerar_pdf

    pdf = gerar_pdf("declaracao_comparecimento", {
        "clinica": {
            "nome": "Clínica Teste",
            "endereco": "Rua A, 10",
            "endereco_cidade": "Caicó",
            "responsavel_tecnico": "Dra. Ana",
            "registro_conselho": "CRM 0000",
            "logo_dataurl": None,
        },
        "paciente": {"nome": "Maria Souza", "cpf": "12345678901"},
        "gerado_por": "Teste",
        "gerado_em": datetime(2026, 10, 11, 9, 0),
        "data": date(2026, 10, 11),
        "data_emissao": date(2026, 10, 11),
        "hora_inicio": "09:00",
        "hora_fim": "10:00",
        "tipo_atendimento": "consulta",
    })

    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
