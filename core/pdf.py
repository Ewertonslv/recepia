"""Gerador de PDFs via WeasyPrint + Jinja2.

Templates em `/app/templates/pdfs/`. Cabeçalho com logo da clínica + dados.
LGPD: watermark com {usuario_nome} + timestamp pra rastreabilidade de vazamento.
"""
from pathlib import Path
from core.timezones import agora_utc
from typing import Any
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "pdfs"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# Filtros úteis
def _br_data(d):
    if d is None: return "—"
    if hasattr(d, 'strftime'): return d.strftime("%d/%m/%Y")
    return str(d)

def _br_data_hora(d):
    if d is None: return "—"
    if hasattr(d, 'strftime'): return d.strftime("%d/%m/%Y %H:%M")
    return str(d)

def _idade(data_nasc):
    if not data_nasc: return None
    from datetime import date as _d
    hoje = _d.today()
    anos = hoje.year - data_nasc.year - ((hoje.month, hoje.day) < (data_nasc.month, data_nasc.day))
    return anos

def _format_cpf(c):
    if not c or len(c) != 11: return c
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"

def _format_cep(c):
    if not c or len(c) != 8: return c
    return f"{c[:5]}-{c[5:]}"

_env.filters['br_data'] = _br_data
_env.filters['br_data_hora'] = _br_data_hora
_env.filters['idade'] = _idade
_env.filters['cpf'] = _format_cpf
_env.filters['cep'] = _format_cep


def gerar_pdf(tipo: str, contexto: dict[str, Any]) -> bytes:
    """Renderiza template Jinja2 + converte pra PDF.

    `tipo` deve ser um de: prontuario, atestado, declaracao_comparecimento, receituario
    `contexto` precisa ter: clinica, paciente, gerado_por (str), gerado_em (datetime),
                            + dados específicos do tipo.
    """
    template = _env.get_template(f"{tipo}.html")
    contexto.setdefault("gerado_em", agora_utc())
    html_str = template.render(**contexto)
    # Import tardio: WeasyPrint exige libs nativas (Pango/Cairo). Importar só aqui
    # mantém o resto do app (e os testes) importáveis sem essas libs instaladas.
    from weasyprint import HTML
    return HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf()
