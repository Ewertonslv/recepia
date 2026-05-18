"""Templates de Anamnese por especialidade.

A estrutura é fixa em código (não vem do DB) — o cliente preenche e a clínica vê.
Cada pergunta tem `key` (estável, usada no JSON de respostas), `label` (texto da pergunta)
e `tipo` (sim_nao | sim_nao_obs | texto).

Convenção de respostas no JSON do banco:
- sim_nao        → {"resposta": bool}
- sim_nao_obs    → {"resposta": bool, "observacao": str | None}
- texto          → {"resposta": str}

Quando uma pergunta nova entra no template, anamneses antigas continuam funcionando —
basta tratar campo ausente como "não respondido" na UI.
"""

# ---------------------------------------------------------------- Odontologia

_ANAMNESE_ODONTO = [
    {
        "secao": "Saúde geral",
        "perguntas": [
            {"key": "tem_doenca_atual",       "label": "Tem alguma doença atualmente?", "tipo": "sim_nao_obs"},
            {"key": "ja_internado",           "label": "Já esteve internado em hospital?", "tipo": "sim_nao_obs"},
            {"key": "ja_cirurgia",            "label": "Já fez alguma cirurgia?", "tipo": "sim_nao_obs"},
            {"key": "alergia_medicamento",    "label": "Tem alergia a algum medicamento?", "tipo": "sim_nao_obs"},
            {"key": "alergia_material",       "label": "Tem alergia a látex, anestésico ou outro material?", "tipo": "sim_nao_obs"},
            {"key": "medicacao_uso",          "label": "Faz uso contínuo de algum medicamento?", "tipo": "sim_nao_obs"},
            {"key": "transfusao_sangue",      "label": "Já recebeu transfusão de sangue?", "tipo": "sim_nao"},
        ],
    },
    {
        "secao": "Cardiovascular",
        "perguntas": [
            {"key": "pressao_alta",           "label": "Tem pressão alta?", "tipo": "sim_nao_obs"},
            {"key": "problema_cardiaco",      "label": "Tem ou teve problema cardíaco?", "tipo": "sim_nao_obs"},
            {"key": "febre_reumatica",        "label": "Já teve febre reumática?", "tipo": "sim_nao"},
            {"key": "sopro_no_coracao",       "label": "Tem sopro no coração?", "tipo": "sim_nao"},
        ],
    },
    {
        "secao": "Endocrinologia",
        "perguntas": [
            {"key": "diabetes",               "label": "Tem diabetes?", "tipo": "sim_nao_obs"},
            {"key": "tireoide",               "label": "Tem problema de tireoide?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Hábitos",
        "perguntas": [
            {"key": "fumante",                "label": "Fuma?", "tipo": "sim_nao_obs"},
            {"key": "bebida_alcoolica",       "label": "Consome bebida alcoólica?", "tipo": "sim_nao_obs"},
            {"key": "ranger_dentes",          "label": "Range os dentes ao dormir (bruxismo)?", "tipo": "sim_nao"},
            {"key": "morder_unha_objeto",     "label": "Costuma morder unhas, caneta ou outros objetos?", "tipo": "sim_nao"},
        ],
    },
    {
        "secao": "Saúde bucal",
        "perguntas": [
            {"key": "sangramento_gengival",   "label": "Sangra a gengiva ao escovar?", "tipo": "sim_nao"},
            {"key": "dor_dente",              "label": "Sente dor em algum dente?", "tipo": "sim_nao_obs"},
            {"key": "dor_mandibula",          "label": "Sente dor ou estalo na articulação da mandíbula (ATM)?", "tipo": "sim_nao"},
            {"key": "sensibilidade_quente_frio", "label": "Tem sensibilidade a quente ou frio?", "tipo": "sim_nao"},
            {"key": "ultima_consulta_odonto", "label": "Quando foi sua última consulta odontológica?", "tipo": "texto"},
        ],
    },
    {
        "secao": "Saúde feminina",
        "perguntas": [
            {"key": "gestante",               "label": "Está gestante?", "tipo": "sim_nao_obs"},
            {"key": "amamentando",            "label": "Está amamentando?", "tipo": "sim_nao"},
            {"key": "anticoncepcional",       "label": "Usa anticoncepcional?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Observações",
        "perguntas": [
            {"key": "observacoes_gerais",     "label": "Alguma observação adicional importante?", "tipo": "texto"},
        ],
    },
]

# ---------------------------------------------------------------- Estética

_ANAMNESE_ESTETICA = [
    {
        "secao": "Saúde geral",
        "perguntas": [
            {"key": "tem_doenca_atual",       "label": "Tem alguma doença atualmente?", "tipo": "sim_nao_obs"},
            {"key": "alergia_medicamento",    "label": "Tem alergia a algum medicamento?", "tipo": "sim_nao_obs"},
            {"key": "alergia_cosmetico",      "label": "Tem alergia a cosméticos, ácidos ou produtos químicos?", "tipo": "sim_nao_obs"},
            {"key": "medicacao_uso",          "label": "Faz uso contínuo de algum medicamento?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Pele",
        "perguntas": [
            {"key": "tipo_pele",              "label": "Como descreve sua pele? (oleosa, seca, mista, sensível)", "tipo": "texto"},
            {"key": "exposicao_solar",        "label": "Pega muito sol?", "tipo": "sim_nao"},
            {"key": "uso_protetor_solar",     "label": "Usa protetor solar diariamente?", "tipo": "sim_nao"},
            {"key": "rotina_skincare",        "label": "Tem rotina de skincare? Quais produtos usa?", "tipo": "texto"},
            {"key": "tratamentos_anteriores", "label": "Já fez algum tratamento estético antes?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Cardiovascular e endocrinologia",
        "perguntas": [
            {"key": "pressao_alta",           "label": "Tem pressão alta?", "tipo": "sim_nao_obs"},
            {"key": "diabetes",               "label": "Tem diabetes?", "tipo": "sim_nao_obs"},
            {"key": "tireoide",               "label": "Tem problema de tireoide?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Hábitos",
        "perguntas": [
            {"key": "fumante",                "label": "Fuma?", "tipo": "sim_nao_obs"},
            {"key": "agua_diaria",            "label": "Quanto de água bebe por dia?", "tipo": "texto"},
            {"key": "atividade_fisica",       "label": "Pratica atividade física? Com que frequência?", "tipo": "texto"},
        ],
    },
    {
        "secao": "Saúde feminina",
        "perguntas": [
            {"key": "gestante",               "label": "Está gestante?", "tipo": "sim_nao_obs"},
            {"key": "amamentando",            "label": "Está amamentando?", "tipo": "sim_nao"},
            {"key": "anticoncepcional",       "label": "Usa anticoncepcional?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Objetivo",
        "perguntas": [
            {"key": "queixa_principal",       "label": "Qual sua queixa ou objetivo principal?", "tipo": "texto"},
            {"key": "observacoes_gerais",     "label": "Alguma observação adicional importante?", "tipo": "texto"},
        ],
    },
]

# ---------------------------------------------------------------- Psicologia

_ANAMNESE_PSICOLOGIA = [
    {
        "secao": "Histórico clínico",
        "perguntas": [
            {"key": "tratamento_anterior",    "label": "Já fez terapia ou tratamento psicológico antes?", "tipo": "sim_nao_obs"},
            {"key": "uso_psiquiatrico",       "label": "Faz acompanhamento psiquiátrico?", "tipo": "sim_nao_obs"},
            {"key": "medicacao_psiquiatrica", "label": "Faz uso de medicação psiquiátrica?", "tipo": "sim_nao_obs"},
            {"key": "internacao_psiquiatrica","label": "Já foi internado por motivo psicológico?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Saúde física",
        "perguntas": [
            {"key": "doenca_cronica",         "label": "Tem alguma doença crônica?", "tipo": "sim_nao_obs"},
            {"key": "uso_medicamento",        "label": "Faz uso de algum medicamento (não psiquiátrico)?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Família e relacionamentos",
        "perguntas": [
            {"key": "estado_civil_atual",     "label": "Estado civil / situação afetiva atual", "tipo": "texto"},
            {"key": "filhos",                 "label": "Tem filhos? Quantos?", "tipo": "texto"},
            {"key": "rede_apoio",             "label": "Conta com rede de apoio (família/amigos)?", "tipo": "sim_nao_obs"},
            {"key": "historico_familiar_psi", "label": "Há histórico de transtorno mental na família?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Hábitos e rotina",
        "perguntas": [
            {"key": "qualidade_sono",         "label": "Como está o sono?", "tipo": "texto"},
            {"key": "alimentacao",            "label": "Como está a alimentação?", "tipo": "texto"},
            {"key": "atividade_fisica",       "label": "Pratica atividade física?", "tipo": "sim_nao_obs"},
            {"key": "uso_alcool_drogas",      "label": "Faz uso de álcool ou outras substâncias?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Demanda",
        "perguntas": [
            {"key": "queixa_principal",       "label": "O que te trouxe aqui? Qual a queixa principal?", "tipo": "texto"},
            {"key": "tempo_sintomas",         "label": "Há quanto tempo isso vem acontecendo?", "tipo": "texto"},
            {"key": "objetivo_terapia",       "label": "O que espera deste processo?", "tipo": "texto"},
            {"key": "observacoes_gerais",     "label": "Alguma observação adicional importante?", "tipo": "texto"},
        ],
    },
]

# ---------------------------------------------------------------- Nutrição

_ANAMNESE_NUTRICAO = [
    {
        "secao": "Saúde geral",
        "perguntas": [
            {"key": "doenca_atual",           "label": "Tem alguma doença atualmente?", "tipo": "sim_nao_obs"},
            {"key": "alergia_alimentar",      "label": "Tem alguma alergia ou intolerância alimentar?", "tipo": "sim_nao_obs"},
            {"key": "medicacao_uso",          "label": "Faz uso contínuo de algum medicamento?", "tipo": "sim_nao_obs"},
            {"key": "suplementacao",          "label": "Toma algum suplemento ou vitamina?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Medidas e composição",
        "perguntas": [
            {"key": "peso_atual",             "label": "Peso atual (kg)", "tipo": "texto"},
            {"key": "altura",                 "label": "Altura (cm)", "tipo": "texto"},
            {"key": "peso_objetivo",          "label": "Peso objetivo (kg)", "tipo": "texto"},
        ],
    },
    {
        "secao": "Hábitos alimentares",
        "perguntas": [
            {"key": "refeicoes_dia",          "label": "Quantas refeições faz por dia?", "tipo": "texto"},
            {"key": "alimentos_evita",        "label": "Tem algum alimento que evita ou não gosta?", "tipo": "texto"},
            {"key": "agua_diaria",            "label": "Quanto de água bebe por dia?", "tipo": "texto"},
            {"key": "bebida_alcoolica",       "label": "Consome bebida alcoólica? Com que frequência?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Atividade física",
        "perguntas": [
            {"key": "atividade_fisica",       "label": "Pratica atividade física? Qual?", "tipo": "sim_nao_obs"},
            {"key": "frequencia_treino",      "label": "Frequência semanal de treino", "tipo": "texto"},
        ],
    },
    {
        "secao": "Objetivo",
        "perguntas": [
            {"key": "objetivo_principal",     "label": "Qual seu objetivo principal?", "tipo": "texto"},
            {"key": "observacoes_gerais",     "label": "Alguma observação adicional importante?", "tipo": "texto"},
        ],
    },
]

# ---------------------------------------------------------------- Fisioterapia

_ANAMNESE_FISIOTERAPIA = [
    {
        "secao": "Histórico clínico",
        "perguntas": [
            {"key": "queixa_principal",       "label": "Qual a queixa principal?", "tipo": "texto"},
            {"key": "tempo_dor",              "label": "Há quanto tempo sente isso?", "tipo": "texto"},
            {"key": "causa_aparente",         "label": "Tem ideia do que causou (queda, esforço, postura)?", "tipo": "texto"},
            {"key": "tratamento_anterior",    "label": "Já fez fisioterapia ou tratamento para isso antes?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Saúde geral",
        "perguntas": [
            {"key": "doenca_atual",           "label": "Tem alguma doença atualmente?", "tipo": "sim_nao_obs"},
            {"key": "cirurgia_recente",       "label": "Fez cirurgia nos últimos 12 meses?", "tipo": "sim_nao_obs"},
            {"key": "medicacao_uso",          "label": "Faz uso contínuo de medicamento?", "tipo": "sim_nao_obs"},
            {"key": "pressao_alta",           "label": "Tem pressão alta?", "tipo": "sim_nao"},
            {"key": "diabetes",               "label": "Tem diabetes?", "tipo": "sim_nao"},
        ],
    },
    {
        "secao": "Atividade e rotina",
        "perguntas": [
            {"key": "atividade_fisica",       "label": "Pratica atividade física? Qual?", "tipo": "sim_nao_obs"},
            {"key": "profissao_postura",      "label": "Sua profissão exige postura prolongada ou esforço físico?", "tipo": "sim_nao_obs"},
            {"key": "dor_diaria",             "label": "A dor atrapalha atividades do dia a dia?", "tipo": "sim_nao_obs"},
        ],
    },
    {
        "secao": "Saúde feminina",
        "perguntas": [
            {"key": "gestante",               "label": "Está gestante?", "tipo": "sim_nao_obs"},
            {"key": "amamentando",            "label": "Está amamentando?", "tipo": "sim_nao"},
        ],
    },
    {
        "secao": "Observações",
        "perguntas": [
            {"key": "observacoes_gerais",     "label": "Alguma observação adicional importante?", "tipo": "texto"},
        ],
    },
]


_TEMPLATES = {
    "odonto":       _ANAMNESE_ODONTO,
    "estetica":     _ANAMNESE_ESTETICA,
    "psicologia":   _ANAMNESE_PSICOLOGIA,
    "nutricao":     _ANAMNESE_NUTRICAO,
    "fisioterapia": _ANAMNESE_FISIOTERAPIA,
}


def template_para_especialidade(slug: str) -> list[dict]:
    """Retorna o template de anamnese para a especialidade. Se desconhecida, usa odonto."""
    return _TEMPLATES.get(slug, _ANAMNESE_ODONTO)


def todas_as_chaves(slug: str) -> set[str]:
    """Set de todas as keys de pergunta da especialidade (pra validação no PUT)."""
    return {
        p["key"]
        for secao in template_para_especialidade(slug)
        for p in secao["perguntas"]
    }
