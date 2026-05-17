"""Catálogo de especialidades suportadas (Sprint 2).

Cada vertical tem:
- Lista de campos do Paciente que são obrigatórios/visíveis (override do default opcional)
- Labels customizados ("Paciente" vs "Cliente")
- Documentos disponíveis (atestado, declaração de comparecimento, prontuário, receituário)
- Template inicial de anamnese (placeholder de prontuário novo)

Vet fica FORA do Sprint 2 — exige refactor de Paciente em "Animal + Tutor".

Configuração é HARD-CODED aqui (metadado de produto, não de usuário). Cliente pode
fazer override pontual via `Clinica.config_paciente` (JSON) — aplicado por
`config_efetiva()` em runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional


# ============================================================================
# Campos do Paciente (lista canônica)
# ============================================================================

# Campos sempre presentes (não-customizáveis por especialidade): nome, telefone, email, notas.
# Estes vão pra Paciente sempre; nome/telefone obrigatórios no schema.

# Campos customizáveis por especialidade:
CAMPOS_CUSTOMIZAVEIS = {
    "data_nascimento", "cpf", "rg", "sexo", "profissao", "estado_civil",
    "endereco_rua", "endereco_numero", "endereco_complemento", "endereco_bairro",
    "endereco_cidade", "endereco_uf", "endereco_cep", "telefone_fixo",
    "diagnostico_breve", "observacoes_clinicas",
    "foto_key",  # foto destacada em estética, escondida em psico
}


@dataclass(frozen=True)
class CampoConfig:
    obrigatorio: bool = False
    visivel: bool = True
    label: Optional[str] = None  # None = usa label default


@dataclass(frozen=True)
class EspecialidadeConfig:
    slug: str
    nome: str                          # label amigável: "Odontologia"
    label_paciente: str = "Paciente"   # "Paciente" | "Cliente"
    label_atendimento: str = "Consulta"  # "Consulta" | "Sessão" | "Atendimento"
    campos: dict[str, CampoConfig] = field(default_factory=dict)
    documentos: list[str] = field(default_factory=list)  # tipos de PDF disponíveis
    foto_destaque: bool = False        # se a foto do paciente aparece grande no card
    anamnese_template: str = ""        # placeholder pra prontuário novo
    procedimentos_sugeridos: list[str] = field(default_factory=list)  # chips no prontuário
    # Templates de mensagem WhatsApp por tom de voz da vertical.
    # Chaves: "confirmacao", "lembrete_24h", "reagendamento", "recall", "boas_vindas".
    # Placeholders suportados pelo renderer (services/scheduler._render):
    #   {nome}, {clinica}, {data_hora}, {opcoes}.
    # NÃO use {data}/{hora}/{profissional} isolados — o renderer não substitui.
    mensagens_whatsapp: dict[str, str] = field(default_factory=dict)


# ============================================================================
# Defaults por vertical
# ============================================================================

_ANAMNESE_ODONTO = """Queixa principal:

Histórico odontológico:

Medicamentos em uso:

Alergias:

Condições sistêmicas relevantes:
"""

_ANAMNESE_FISIO = """Queixa principal (QP):

História da doença atual (HDA):

História patológica pregressa:

Medicamentos em uso:

Exames complementares:

Plano de tratamento:
"""

_ANAMNESE_MEDICO = """Queixa principal:

História da doença atual:

Antecedentes pessoais:

Antecedentes familiares:

Medicações em uso:

Alergias:

Hipótese diagnóstica:

Conduta:
"""

_ANAMNESE_ESTETICA = """Queixa estética principal:

Histórico de procedimentos anteriores:

Medicamentos em uso:

Alergias (anestésico, ácidos, fragrâncias):

Gestante/lactante:

Fototipo de pele:

Plano de procedimentos:
"""

_MSGS_ODONTO = {
    "confirmacao": "Oi {nome}, tudo bem? 😊 Confirma sua consulta odontológica de {data_hora}? Responde SIM ou NÃO. — {clinica}",
    "lembrete_24h": "Oi {nome}! Lembrete: sua consulta odontológica é amanhã, {data_hora}. Te espero! — {clinica}",
    "reagendamento": "Sem problema, {nome}. Tenho esses horários abertos:\n\n{opcoes}\n\nQual prefere?",
    "recall": "Oi {nome}! 💛 Faz uns meses desde sua última limpeza aqui na {clinica}. Que tal marcar a próxima? Sua saúde bucal agradece 🦷",
    "boas_vindas": "Bem-vindo(a) à {clinica}! 💛 Sou a recepcionista virtual. Posso te ajudar a agendar uma consulta, confirmar horário ou tirar dúvidas. Manda aí!",
}

_MSGS_MEDICO = {
    "confirmacao": "Sr(a). {nome}, confirma sua consulta médica de {data_hora}? Responda SIM ou NÃO. — {clinica}",
    "lembrete_24h": "Sr(a). {nome}, lembrete: sua consulta na {clinica} é amanhã, {data_hora}. Não esqueça de trazer seus exames recentes.",
    "reagendamento": "Sem problema, {nome}. Posso reagendar pra:\n\n{opcoes}\n\nQual horário fica melhor?",
    "recall": "Sr(a). {nome}, sua última consulta na {clinica} foi há um tempo. Recomendamos retorno periódico — gostaria de agendar?",
    "boas_vindas": "Olá, {nome}. Sou a assistente virtual de {clinica}. Posso agendar consultas, confirmar horários e responder dúvidas sobre seu atendimento.",
}

_MSGS_FISIO = {
    "confirmacao": "Oi {nome}! 💪 Confirma sua sessão de fisioterapia de {data_hora}? Responde SIM ou NÃO. — {clinica}",
    "lembrete_24h": "Oi {nome}! Sua sessão de fisio é amanhã, {data_hora}. Vem com roupa confortável 😊 — {clinica}",
    "reagendamento": "Tudo bem {nome}, tenho esses horários:\n\n{opcoes}\n\nQual fica melhor?",
    "recall": "Oi {nome}! 💪 Faz um tempo desde sua última sessão na {clinica}. Que tal continuar de onde paramos? Sua reabilitação merece!",
    "boas_vindas": "Bem-vindo(a) à {clinica}! 💪 Sou a recepção da equipe. Posso ajudar a agendar sua avaliação ou sessão de fisio. Bora começar?",
}

_MSGS_ESTETICA = {
    "confirmacao": "Oi {nome}! ✨ Confirma sua sessão de {data_hora}? Te espero linda(o)! — {clinica}",
    "lembrete_24h": "Oi {nome}! Amanhã, {data_hora}, é seu momento aqui na {clinica} ✨ Vem com a pele limpinha, sem maquiagem 💛",
    "reagendamento": "Sem stress, {nome}! Tenho esses horários abertos:\n\n{opcoes}\n\nQual encaixa melhor na sua agenda?",
    "recall": "Oi {nome}! ✨ Faz uns meses do seu último tratamento aqui na {clinica}. Que tal voltar pra cuidar de você? Tenho horários ótimos essa semana 💛",
    "boas_vindas": "Bem-vinda à {clinica}! ✨ Sou a recepção. Posso te ajudar a agendar limpeza de pele, harmonização, peeling ou tirar qualquer dúvida sobre os procedimentos 💛",
}

_MSGS_PSICO = {
    "confirmacao": "Olá, {nome}. Confirma sua sessão de {data_hora}? Pode responder SIM ou NÃO. — {clinica}",
    "lembrete_24h": "Olá, {nome}. Lembrete da sua sessão amanhã, {data_hora}. Qualquer coisa, é só me avisar. — {clinica}",
    "reagendamento": "Sem problema, {nome}. Posso oferecer:\n\n{opcoes}\n\nQual horário funciona pra você?",
    "recall": "Olá, {nome}. Notei que faz um tempo desde nossa última sessão na {clinica}. Se sentir vontade de retomar, estou aqui. Sem pressão.",
    "boas_vindas": "Olá, {nome}. Bem-vindo(a) à {clinica}. Sou a recepção. Posso ajudar a agendar uma sessão ou esclarecer dúvidas — fique à vontade pra escrever quando quiser.",
}


_ANAMNESE_PSICO = """Demanda inicial:

História de vida (resumo):

Vínculos significativos:

Histórico de tratamento anterior:

Medicações em uso:

Observações da sessão:
"""


ESPECIALIDADES: dict[str, EspecialidadeConfig] = {
    "odonto": EspecialidadeConfig(
        slug="odonto",
        nome="Odontologia",
        label_paciente="Paciente",
        label_atendimento="Consulta",
        campos={
            "data_nascimento": CampoConfig(obrigatorio=True),
            "cpf": CampoConfig(obrigatorio=True),
            "rg": CampoConfig(),
            "profissao": CampoConfig(),
            "endereco_rua": CampoConfig(),
            "endereco_numero": CampoConfig(),
            "endereco_bairro": CampoConfig(),
            "endereco_cidade": CampoConfig(),
            "endereco_uf": CampoConfig(),
            "endereco_cep": CampoConfig(),
            "telefone_fixo": CampoConfig(),
            "diagnostico_breve": CampoConfig(label="Histórico odontológico"),
            "foto_key": CampoConfig(visivel=True),
        },
        documentos=["prontuario", "atestado", "declaracao_comparecimento", "receituario", "termo_consentimento"],
        foto_destaque=False,
        anamnese_template=_ANAMNESE_ODONTO,
        procedimentos_sugeridos=[
            "Limpeza", "Restauração", "Tratamento de canal", "Extração",
            "Clareamento", "Aplicação de flúor", "Selante", "Coroa",
            "Aparelho fixo", "Manutenção ortodôntica", "Profilaxia",
            "Raspagem subgengival", "Implante", "Prótese parcial",
            "Prótese total", "Cirurgia de siso",
        ],
        mensagens_whatsapp=_MSGS_ODONTO,
    ),
    "fisio": EspecialidadeConfig(
        slug="fisio",
        nome="Fisioterapia",
        label_paciente="Paciente",
        label_atendimento="Sessão",
        campos={
            "data_nascimento": CampoConfig(obrigatorio=True),
            "cpf": CampoConfig(obrigatorio=True),
            "rg": CampoConfig(),
            "profissao": CampoConfig(obrigatorio=True, label="Profissão (importante p/ LER)"),
            "endereco_rua": CampoConfig(),
            "endereco_numero": CampoConfig(),
            "endereco_bairro": CampoConfig(),
            "endereco_cidade": CampoConfig(),
            "endereco_uf": CampoConfig(),
            "endereco_cep": CampoConfig(),
            "telefone_fixo": CampoConfig(),
            "diagnostico_breve": CampoConfig(label="Diagnóstico médico (CID-10 se disponível)"),
            "observacoes_clinicas": CampoConfig(label="Plano terapêutico"),
            "foto_key": CampoConfig(visivel=True),
        },
        documentos=["prontuario", "atestado", "declaracao_comparecimento"],
        foto_destaque=False,
        anamnese_template=_ANAMNESE_FISIO,
        procedimentos_sugeridos=[
            "Avaliação postural", "RPG", "Pilates terapêutico",
            "Eletroterapia (TENS)", "Crioterapia", "Termoterapia",
            "Mobilização articular", "Liberação miofascial",
            "Reeducação neuromuscular", "Exercícios respiratórios",
            "Drenagem linfática", "Pompage",
        ],
        mensagens_whatsapp=_MSGS_FISIO,
    ),
    "medico": EspecialidadeConfig(
        slug="medico",
        nome="Medicina",
        label_paciente="Paciente",
        label_atendimento="Consulta",
        campos={
            "data_nascimento": CampoConfig(obrigatorio=True),
            "cpf": CampoConfig(obrigatorio=True),
            "rg": CampoConfig(obrigatorio=True),
            "sexo": CampoConfig(obrigatorio=True),
            "profissao": CampoConfig(),
            "endereco_rua": CampoConfig(obrigatorio=True),
            "endereco_numero": CampoConfig(obrigatorio=True),
            "endereco_bairro": CampoConfig(),
            "endereco_cidade": CampoConfig(obrigatorio=True),
            "endereco_uf": CampoConfig(obrigatorio=True),
            "endereco_cep": CampoConfig(),
            "telefone_fixo": CampoConfig(),
            "diagnostico_breve": CampoConfig(),
            "observacoes_clinicas": CampoConfig(),
            "foto_key": CampoConfig(visivel=True),
        },
        documentos=["prontuario", "atestado", "declaracao_comparecimento", "receituario"],
        foto_destaque=False,
        anamnese_template=_ANAMNESE_MEDICO,
        procedimentos_sugeridos=[
            "Consulta clínica", "Aferição PA", "Eletrocardiograma", "Sutura",
            "Curativo", "Aplicação intramuscular", "Nebulização",
            "Receituário", "Solicitação de exames", "Encaminhamento",
        ],
        mensagens_whatsapp=_MSGS_MEDICO,
    ),
    "estetica": EspecialidadeConfig(
        slug="estetica",
        nome="Estética",
        label_paciente="Cliente",
        label_atendimento="Sessão",
        campos={
            "data_nascimento": CampoConfig(obrigatorio=True),
            "cpf": CampoConfig(),
            "profissao": CampoConfig(),
            "estado_civil": CampoConfig(),
            "endereco_rua": CampoConfig(),
            "endereco_numero": CampoConfig(),
            "endereco_bairro": CampoConfig(),
            "endereco_cidade": CampoConfig(),
            "endereco_uf": CampoConfig(),
            "endereco_cep": CampoConfig(),
            "telefone_fixo": CampoConfig(),
            "diagnostico_breve": CampoConfig(label="Avaliação estética"),
            "observacoes_clinicas": CampoConfig(label="Plano de procedimentos"),
            "foto_key": CampoConfig(visivel=True),
        },
        documentos=["prontuario", "declaracao_comparecimento"],
        foto_destaque=True,  # foto antes/depois é central
        anamnese_template=_ANAMNESE_ESTETICA,
        procedimentos_sugeridos=[
            "Limpeza de pele", "Peeling químico", "Microagulhamento",
            "Drenagem linfática", "Massagem modeladora", "Radiofrequência",
            "Criofrequência", "Hidratação facial", "Depilação a laser",
            "Toxina botulínica", "Preenchimento", "Bioestimulador",
        ],
        mensagens_whatsapp=_MSGS_ESTETICA,
    ),
    "psico": EspecialidadeConfig(
        slug="psico",
        nome="Psicologia",
        label_paciente="Paciente",
        label_atendimento="Sessão",
        campos={
            "data_nascimento": CampoConfig(obrigatorio=True),
            "cpf": CampoConfig(),
            "profissao": CampoConfig(),
            "estado_civil": CampoConfig(),
            "endereco_cidade": CampoConfig(),
            "endereco_uf": CampoConfig(),
            "telefone_fixo": CampoConfig(),
            "observacoes_clinicas": CampoConfig(label="Observações da sessão"),
            # foto_key escondida em psico — sigilo profissional reforçado
            "foto_key": CampoConfig(visivel=False),
        },
        documentos=["prontuario", "declaracao_comparecimento"],
        foto_destaque=False,
        anamnese_template=_ANAMNESE_PSICO,
        procedimentos_sugeridos=[
            "Sessão individual", "Sessão de casal", "Sessão familiar",
            "Avaliação psicológica", "Testagem (aplicação)", "Devolutiva",
            "Orientação vocacional", "Atendimento de urgência",
        ],
        mensagens_whatsapp=_MSGS_PSICO,
    ),
}


# ============================================================================
# API pública
# ============================================================================

def get_especialidade(slug: str) -> EspecialidadeConfig:
    """Retorna config da especialidade. Fallback pra odonto se slug inválido."""
    return ESPECIALIDADES.get(slug) or ESPECIALIDADES["odonto"]


def config_efetiva(clinica) -> EspecialidadeConfig:
    """Combina o default da especialidade com overrides em Clinica.config_paciente.

    `Clinica.config_paciente` é um dict no formato:
        {"campos": {"cpf": {"obrigatorio": false}, "rg": {"visivel": false}}}
    Permite a clínica desligar um campo específico sem mudar a especialidade inteira.
    """
    base = get_especialidade(clinica.especialidade)
    overrides = (clinica.config_paciente or {}).get("campos", {})
    if not overrides:
        return base
    campos_efetivos = dict(base.campos)
    for campo, conf in overrides.items():
        if campo not in CAMPOS_CUSTOMIZAVEIS:
            continue  # ignora chave inválida silenciosamente
        atual = campos_efetivos.get(campo, CampoConfig())
        campos_efetivos[campo] = replace(
            atual,
            obrigatorio=conf.get("obrigatorio", atual.obrigatorio),
            visivel=conf.get("visivel", atual.visivel),
            label=conf.get("label", atual.label),
        )
    return replace(base, campos=campos_efetivos)


def to_dict(cfg: EspecialidadeConfig) -> dict:
    """Serialização pro frontend (cache `window.especialidadeConfig`)."""
    return {
        "slug": cfg.slug,
        "nome": cfg.nome,
        "label_paciente": cfg.label_paciente,
        "label_atendimento": cfg.label_atendimento,
        "campos": {
            k: {"obrigatorio": v.obrigatorio, "visivel": v.visivel, "label": v.label}
            for k, v in cfg.campos.items()
        },
        "documentos": list(cfg.documentos),
        "foto_destaque": cfg.foto_destaque,
        "anamnese_template": cfg.anamnese_template,
        "procedimentos_sugeridos": list(cfg.procedimentos_sugeridos),
        "mensagens_whatsapp": dict(cfg.mensagens_whatsapp),
    }


def listar_slugs() -> list[str]:
    return list(ESPECIALIDADES.keys())


def validar_paciente_para_especialidade(cfg: EspecialidadeConfig, dados: dict) -> list[str]:
    """Retorna lista de campos faltantes obrigatórios. Vazia = OK."""
    faltantes = []
    for nome_campo, conf in cfg.campos.items():
        if not conf.obrigatorio:
            continue
        valor = dados.get(nome_campo)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            faltantes.append(nome_campo)
    return faltantes
