"""Templates default de mensagem aplicados a cada nova clínica.

Linguagem otimizada pra clínica de estética: acolhedora, profissional,
não-robótica. Cada clínica pode editar via dashboard depois.

A partir do Sprint 3.1, `aplicar_configuracoes_default` busca os textos
da VERTICAL da clínica em `core.especialidades.<vertical>.mensagens_whatsapp`
e só cai pros textos abaixo (tom estética) se a vertical não tiver template
pra aquela chave. Mapeamento de chaves persistidas (`msg_*`) ↔ chaves da
vertical (curtas, ex: "confirmacao") está em `_CHAVE_VERTICAL_POR_CONFIGURACAO`.
"""

# Mapeia chave de Configuracao -> chave em EspecialidadeConfig.mensagens_whatsapp.
# `None` = não há equivalente por vertical; usa o texto genérico abaixo direto.
_CHAVE_VERTICAL_POR_CONFIGURACAO = {
    "msg_primeiro_contato": "boas_vindas",
    "msg_confirmacao_24h": "confirmacao",
    "msg_lembrete_2h": "lembrete_24h",  # mais próxima do que tem por vertical
    "msg_reagendar_opcoes": "reagendamento",
    "msg_recuperacao_sumida": "recall",
    "msg_confirmado": None,
    "msg_cancelado": None,
    "msg_nao_entendido": None,
    "msg_pos_atendimento": None,
    "msg_aniversario": None,
    "msg_pacote_lembrete": None,
}


TEMPLATES_DEFAULT = [
    (
        "msg_primeiro_contato",
        # LGPD Art. 9 — informar que é assistente virtual no primeiro contato
        "Oi {nome}! 👋 Aqui é da {clinica}. Vou te ajudar com seu agendamento através deste WhatsApp — sou uma assistente virtual com IA. Pra parar de receber estas mensagens automáticas a qualquer momento, é só responder SAIR. Combinado?",
    ),
    (
        "msg_confirmacao_24h",
        "Oi {nome}! 😊 Aqui é da {clinica}. Você tem um horário marcado amanhã ({data_hora}). Posso confirmar sua presença? Responde SIM, NAO ou REAGENDAR.",
    ),
    (
        "msg_lembrete_2h",
        "Oi {nome}, lembrete carinhoso: seu atendimento na {clinica} é em 2 horas ({data_hora}). Te espero! 💆‍♀️",
    ),
    (
        "msg_confirmado",
        "Perfeito, {nome}! Está confirmado pra {data_hora}. Qualquer coisa, é só me chamar por aqui. Até lá! ✨",
    ),
    (
        "msg_cancelado",
        "Tudo bem, {nome}. Cancelei seu horário de {data_hora}. Quando quiser reagendar, é só me chamar. 💛",
    ),
    (
        "msg_reagendar_opcoes",
        "Sem problemas, {nome}! 💛 Estes horários estão livres pra você:\n\n{opcoes}\n\nMe responde só com o número da opção que prefere.",
    ),
    (
        "msg_nao_entendido",
        "Desculpa, {nome}, não entendi direitinho. Você pode responder SIM pra confirmar, NAO pra cancelar, ou REAGENDAR pra mudar o horário?",
    ),
    (
        "msg_pos_atendimento",
        "Oi {nome}! Espero que tenha gostado do atendimento de hoje 💕 Se puder, deixa uma avaliação rápida? Significa muito pra gente!",
    ),
    (
        "msg_recuperacao_sumida",
        "Oi {nome}! Sentimos sua falta na {clinica}. Faz um tempinho que você não passa por aqui — quer que eu separe um horário pra você essa semana?",
    ),
    (
        "msg_aniversario",
        "Feliz aniversário, {nome}! 🎂 Como presente da {clinica}, preparei uma condição especial pra você este mês. Quer saber qual?",
    ),
    (
        "msg_pacote_lembrete",
        "Oi {nome}! Você ainda tem {sessoes_restantes} sessão(ões) do seu pacote. Que tal agendar a próxima? Tenho horários disponíveis essa semana 💆‍♀️",
    ),
]


def aplicar_configuracoes_default(db, clinica_id: str) -> None:
    """Chama após criar uma nova clínica — popula templates padrão.

    Ordem de fallback por chave:
      1. Texto da vertical da clínica (`mensagens_whatsapp[chave_vertical]`)
      2. Texto genérico estética em `TEMPLATES_DEFAULT` (último fallback)
    Idempotente: nunca sobrescreve config já existente.
    """
    from models import Clinica, Configuracao
    from core.especialidades import get_especialidade

    clinica = db.query(Clinica).filter(Clinica.id == clinica_id).first()
    msgs_vertical: dict[str, str] = {}
    if clinica is not None:
        try:
            msgs_vertical = dict(get_especialidade(clinica.especialidade).mensagens_whatsapp)
        except Exception:
            msgs_vertical = {}

    for chave, valor_fallback in TEMPLATES_DEFAULT:
        existe = (
            db.query(Configuracao)
            .filter(Configuracao.clinica_id == clinica_id, Configuracao.chave == chave)
            .first()
        )
        if existe:
            continue
        chave_vertical = _CHAVE_VERTICAL_POR_CONFIGURACAO.get(chave)
        valor = msgs_vertical.get(chave_vertical) if chave_vertical else None
        if not valor:
            valor = valor_fallback
        db.add(Configuracao(clinica_id=clinica_id, chave=chave, valor=valor))


def aplicar_horarios_default(db, clinica_id: str) -> None:
    """Cria horários default: segunda a sexta 09:00-18:00, slots de 60min.

    Sábado e domingo desativados por padrão. Clínica edita via dashboard depois.
    """
    from models import HorarioFuncionamento

    defaults = [
        (0, "09:00", "18:00", 60, True),   # segunda
        (1, "09:00", "18:00", 60, True),   # terça
        (2, "09:00", "18:00", 60, True),   # quarta
        (3, "09:00", "18:00", 60, True),   # quinta
        (4, "09:00", "18:00", 60, True),   # sexta
        (5, "09:00", "13:00", 60, False),  # sábado (criado mas inativo)
        (6, "09:00", "13:00", 60, False),  # domingo
    ]
    for dia, inicio, fim, slot, ativo in defaults:
        existe = (
            db.query(HorarioFuncionamento)
            .filter(
                HorarioFuncionamento.clinica_id == clinica_id,
                HorarioFuncionamento.dia_semana == dia,
            )
            .first()
        )
        if not existe:
            db.add(HorarioFuncionamento(
                clinica_id=clinica_id,
                dia_semana=dia,
                hora_inicio=inicio,
                hora_fim=fim,
                intervalo_slot_min=slot,
                ativo=ativo,
            ))
