import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, Date, DateTime, Text, JSON,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from database import Base


def gen_id() -> str:
    return str(uuid.uuid4())


# ============================================================================
# Status e tipos (enums informais — strings pra evitar migration pesada)
# ============================================================================

class Status:
    PENDENTE = "pendente"
    CONFIRMADO = "confirmado"
    CANCELADO = "cancelado"
    REAGENDADO = "reagendado"
    REALIZADO = "realizado"
    NO_SHOW = "no_show"


class TipoInteracao:
    CONFIRMACAO = "confirmacao"
    LEMBRETE = "lembrete"
    RESPOSTA = "resposta"
    CANCELAMENTO = "cancelamento"
    REAGENDAMENTO = "reagendamento"
    OPT_OUT = "opt_out"  # paciente pediu pra parar


class Plano:
    ESSENCIAL = "essencial"      # R$ 97/mês — 2 prof, 100 pacientes
    PRO = "pro"                  # R$ 197/mês — 5 prof, ilimitado
    ENTERPRISE = "enterprise"    # R$ 497/mês — multi-unidade, ilimitado
    TRIAL = "trial"              # 7-30 dias grátis (acesso Pro)


class AcaoAudit:
    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    EXPORT = "EXPORT"  # LGPD Art. 18
    LOGIN = "LOGIN"
    SETUP = "SETUP"


# ============================================================================
# Tenant root
# ============================================================================

class Clinica(Base):
    """Tenant. Cada clínica é isolada por clinica_id em todas as tabelas."""
    __tablename__ = "clinicas"

    id = Column(String, primary_key=True, default=gen_id)
    nome = Column(String, nullable=False)
    cnpj = Column(String, index=True)
    plano = Column(String, default=Plano.TRIAL, nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)

    # Sprint 2 — multi-vertical
    especialidade = Column(String(20), default="odonto", nullable=False)
    config_paciente = Column(JSON, default=dict, nullable=False)  # override de required/labels da especialidade
    logo_key = Column(String(80))                                  # /data/logos/{clinica_id}.webp
    responsavel_tecnico = Column(String(120))                      # nome do responsável p/ emitir atestado
    registro_conselho = Column(String(30))                         # CRM/CRO/CREFITO/CRP/CRMV

    # Endereço da clínica (cabeçalho de PDFs, atestados)
    endereco_rua = Column(String(120))
    endereco_numero = Column(String(20))
    endereco_complemento = Column(String(60))
    endereco_bairro = Column(String(80))
    endereco_cidade = Column(String(80))
    endereco_uf = Column(String(2))
    endereco_cep = Column(String(8))

    # WhatsApp via Evolution API
    evolution_instance_name = Column(String, unique=True)
    evolution_conectado = Column(Boolean, default=False, nullable=False)

    # Sprint 3 — Recall automático (lembrete pós-procedimento via WhatsApp)
    # Opt-in explícito: clínica precisa ativar. LGPD: respeita Paciente.opt_out sempre.
    recall_ativo = Column(Boolean, default=False, nullable=False)
    recall_intervalo_dias = Column(Integer, default=180, nullable=False)  # 6 meses default
    recall_template = Column(Text)  # null => usa template default genérico
    recall_procedimento_chave = Column(String(80), default="limpeza", nullable=False)

    # API key tenant-scoped (usada por integrações automáticas da clínica)
    api_key = Column(String, unique=True, nullable=False, default=gen_id)

    # Self-signup + trial 7 dias (Sprint 4)
    # trial_expira_em null = pagante (sem trial, acesso normal pelo plano)
    trial_expira_em = Column(Date, nullable=True, index=True)
    # Origem do cadastro: "admin_master" (criada pelo Ewerton) ou "signup_publico" (self-serve)
    origem_cadastro = Column(String(20), default="admin_master", nullable=False)

    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    usuarios = relationship("Usuario", back_populates="clinica", cascade="all, delete-orphan")
    pacientes = relationship("Paciente", back_populates="clinica", cascade="all, delete-orphan")
    agendamentos = relationship("Agendamento", back_populates="clinica", cascade="all, delete-orphan")
    configuracoes = relationship("Configuracao", back_populates="clinica", cascade="all, delete-orphan")


# ============================================================================
# Auth
# ============================================================================

class Usuario(Base):
    """Pessoa que loga no dashboard de uma clínica."""
    __tablename__ = "usuarios"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    # B4: email NÃO mais unique global — agora compound (clinica_id, email).
    # Mesma pessoa pode ter login em N clínicas (gestora multi-marca).
    email = Column(String, nullable=False, index=True)
    senha_hash = Column(String, nullable=False)
    nome = Column(String)
    role = Column(String, default="admin", nullable=False)  # admin | operador
    ativo = Column(Boolean, default=True, nullable=False)
    # Self-signup (Sprint 4) — contato do responsável + aceite LGPD
    telefone = Column(String(20), nullable=True)
    aceitou_termos_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)

    clinica = relationship("Clinica", back_populates="usuarios")

    __table_args__ = (
        UniqueConstraint("clinica_id", "email", name="uq_usuario_clinica_email"),
    )


# ============================================================================
# Dados de operação (tudo com clinica_id)
# ============================================================================

class Paciente(Base):
    __tablename__ = "pacientes"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = Column(String, nullable=False)
    telefone = Column(String, nullable=False, index=True)  # sempre normalizado: 55DDDNNNNNNNNN
    email = Column(String)
    notas = Column(Text)
    # LGPD Art. 8 §5: paciente pediu pra parar de receber mensagens
    opt_out = Column(Boolean, default=False, nullable=False)
    opt_out_em = Column(DateTime)
    # Soft delete (LGPD Art. 18, grace period antes do hard delete)
    deletado_em = Column(DateTime, index=True)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Sprint 2 — campos expandidos pra suportar multi-vertical (odonto/fisio/medico/estetica/psico).
    # Obrigatoriedade vem de core/especialidades.py em runtime, não do schema.
    foto_key = Column(String(80))                    # paciente_id.webp em /data/fotos_paciente/{clinica_id}/
    data_nascimento = Column(Date, index=True)       # pra aniversariantes + cálculo idade
    cpf = Column(String(11), index=True)             # 11 dígitos sem máscara
    rg = Column(String(20))
    sexo = Column(String(1))                         # M | F | O | N
    profissao = Column(String(80))
    estado_civil = Column(String(20))
    endereco_rua = Column(String(120))
    endereco_numero = Column(String(20))
    endereco_complemento = Column(String(60))
    endereco_bairro = Column(String(80))
    endereco_cidade = Column(String(80))
    endereco_uf = Column(String(2))
    endereco_cep = Column(String(8))
    telefone_fixo = Column(String(20))
    diagnostico_breve = Column(Text)
    observacoes_clinicas = Column(Text)
    campos_extras = Column(JSON, default=dict, nullable=False)  # custom da clínica (sem UI ainda)
    # Sprint 2 — odontograma (só faz sentido pra clínicas com especialidade=odonto).
    # Estado atual de cada dente FDI. Estrutura:
    # {"<fdi_num>": {"estado": str, "observacao": str|None, "atualizado_em": iso, "atualizado_por": usuario_id|None}}
    # Histórico fica no audit_log (não criamos tabela versionada).
    odontograma = Column(JSON, default=dict, nullable=False)

    clinica = relationship("Clinica", back_populates="pacientes")
    agendamentos = relationship("Agendamento", back_populates="paciente", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_pacientes_clinica_telefone", "clinica_id", "telefone"),
    )


class Agendamento(Base):
    __tablename__ = "agendamentos"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    paciente_id = Column(String, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    data_hora = Column(DateTime, nullable=False, index=True)  # sempre UTC naive
    duracao_minutos = Column(Integer, default=30, nullable=False)
    servico = Column(String)
    profissional = Column(String)  # legacy: nome livre. Mantido por compat com agendamentos antigos.
    # Sprint 1 D5: FK opcional pro Profissional cadastrado. `profissional` (string) é fallback histórico.
    profissional_id = Column(String, ForeignKey("profissionais.id", ondelete="SET NULL"), index=True)
    status = Column(String, default=Status.PENDENTE, nullable=False, index=True)
    confirmacao_enviada = Column(Boolean, default=False, nullable=False)
    segunda_confirmacao = Column(Boolean, default=False, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    clinica = relationship("Clinica", back_populates="agendamentos")
    paciente = relationship("Paciente", back_populates="agendamentos")
    interacoes = relationship("Interacao", back_populates="agendamento", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_agendamentos_clinica_data", "clinica_id", "data_hora"),
        Index("ix_agendamentos_clinica_status", "clinica_id", "status"),
    )


class Interacao(Base):
    """Log de cada mensagem trocada (enviada ou recebida).

    B2: agendamento_id agora é NULLABLE pra permitir registrar mensagens fora
    de fluxo (paciente sem agendamento pendente — vale pra histórico/LGPD).
    """
    __tablename__ = "interacoes"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    agendamento_id = Column(String, ForeignKey("agendamentos.id", ondelete="CASCADE"), nullable=True, index=True)
    tipo = Column(String, nullable=False)  # ver TipoInteracao
    mensagem_enviada = Column(Text)
    mensagem_recebida = Column(Text)
    # G9: dedup por message_id do Evolution
    evolution_message_id = Column(String, index=True)
    quando = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    agendamento = relationship("Agendamento", back_populates="interacoes")

    __table_args__ = (
        UniqueConstraint("evolution_message_id", name="uq_interacao_evolution_msg_id"),
    )


class Configuracao(Base):
    """Templates de mensagem e parâmetros por clínica."""
    __tablename__ = "configuracoes"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    chave = Column(String, nullable=False)
    valor = Column(Text, nullable=False)

    clinica = relationship("Clinica", back_populates="configuracoes")

    __table_args__ = (
        UniqueConstraint("clinica_id", "chave", name="uq_config_clinica_chave"),
    )


# ============================================================================
# Horário de funcionamento (G6) — base pra reagendamento real (G1)
# ============================================================================

class HorarioFuncionamento(Base):
    """Quando a clínica atende. 1 row por dia da semana.

    dia_semana: 0=segunda, 6=domingo (alinhado com datetime.weekday()).
    hora_inicio/fim: strings HH:MM (sem tz, hora local da clínica).
    """
    __tablename__ = "horarios_funcionamento"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    dia_semana = Column(Integer, nullable=False)  # 0-6
    hora_inicio = Column(String, nullable=False)  # "09:00"
    hora_fim = Column(String, nullable=False)     # "18:00"
    intervalo_slot_min = Column(Integer, default=60, nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("clinica_id", "dia_semana", name="uq_horario_clinica_dia"),
    )


# ============================================================================
# Estado de conversa (G1 — reagendamento real multi-step)
# ============================================================================

class EstadoConversa(Base):
    """Rastreia que um paciente está num fluxo multi-step (ex: escolhendo slot).

    Quando a paciente envia uma mensagem, o scheduler checa se existe estado
    ATIVO antes de classificar a intenção. Se sim, processa como continuação
    do fluxo (ex: paciente respondendo "2" pra escolher slot 2).
    """
    __tablename__ = "estado_conversa"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    paciente_id = Column(String, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    fluxo = Column(String, nullable=False)  # "reagendamento" | "novo_agendamento" | etc
    contexto = Column(JSON, nullable=False, default=dict)
    expira_em = Column(DateTime, nullable=False, index=True)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        # 1 estado ativo por paciente — se entrar em novo fluxo, sobrescreve
        UniqueConstraint("clinica_id", "paciente_id", name="uq_estado_clinica_paciente"),
    )


class FluxoConversa:
    REAGENDAMENTO = "reagendamento"
    NOVO_AGENDAMENTO = "novo_agendamento"


# ============================================================================
# Sprint 1 — Profissional + Prontuário
# ============================================================================

class Profissional(Base):
    """Pessoa que realiza atendimentos numa clínica.

    Diferente de Usuario (login no dashboard). Profissional pode não ter
    login (ex: técnica que só atende, não mexe no sistema).
    Limite por tier: Essencial=2, Pro=5, Enterprise=ilimitado.
    """
    __tablename__ = "profissionais"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = Column(String, nullable=False)
    email = Column(String)
    especialidade = Column(String)  # ex: "Esteticista", "Dermatologista", "Fisioterapeuta"
    # Comissão em % (0-100). Dado SENSÍVEL — só admin vê em response.
    comissao_percentual = Column(Integer, default=0, nullable=False)
    cor = Column(String, default="#E8B4B8")  # cor pra agenda visual
    ativo = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Prontuario(Base):
    """Registro de uma consulta/sessão de um paciente.

    LGPD: dado SENSÍVEL Art. 11 (saúde). Audit READ obrigatório.
    Fotos antes/depois são biométricas — consentimento específico.
    """
    __tablename__ = "prontuarios"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    paciente_id = Column(String, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    profissional_id = Column(String, ForeignKey("profissionais.id", ondelete="SET NULL"))
    agendamento_id = Column(String, ForeignKey("agendamentos.id", ondelete="SET NULL"))  # opcional

    anotacoes = Column(Text)
    procedimentos_realizados = Column(Text)
    alergias = Column(JSON, default=list)  # ["lidocaína", "ácido"]
    proxima_acao = Column(String)  # "retorno em 15 dias"

    # Fotos: lista de dicts com {key, sha256, mime, tamanho_bytes, descricao}
    # Arquivos ficam em /data/fotos/{clinica}/{prontuario}/{hash}.webp
    fotos = Column(JSON, default=list)

    # Vincula sessão executada a um plano de tratamento (opcional).
    plano_tratamento_id = Column(String, ForeignKey("planos_tratamento.id", ondelete="SET NULL"), index=True)

    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_prontuarios_clinica_paciente_data", "clinica_id", "paciente_id", "criado_em"),
    )


class PlanoTratamento(Base):
    """Plano de tratamento de um paciente (ex: canal, ortodontia, clareamento).

    1 paciente pode ter N planos. Cada plano tem N sessões previstas.
    Sessões executadas viram Prontuario.plano_tratamento_id == plano.id.
    Sem valor monetário (financeiro fora de escopo).
    """
    __tablename__ = "planos_tratamento"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    paciente_id = Column(String, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = Column(String(120), nullable=False)  # "Tratamento de canal dente 36"
    descricao = Column(Text)
    sessoes_previstas = Column(Integer, default=1, nullable=False)
    status = Column(String(20), default="ativo", nullable=False)  # ativo|concluido|cancelado
    profissional_id = Column(String, ForeignKey("profissionais.id", ondelete="SET NULL"))
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    concluido_em = Column(DateTime)

    __table_args__ = (
        Index("ix_planos_clinica_paciente", "clinica_id", "paciente_id"),
    )


# ============================================================================
# LGPD — Audit log
# ============================================================================

class RecallEnviado(Base):
    """Sprint 3: tracking de recalls disparados pra evitar flood / duplicação.

    Um registro por (clinica, paciente, prontuario_origem). Cron usa essa tabela
    pra garantir que não manda 2 recalls em 30 dias pro mesmo paciente.
    """
    __tablename__ = "recalls_enviados"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    paciente_id = Column(String, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    prontuario_origem_id = Column(String)  # qual prontuário "âncora" gerou o recall
    enviado_em = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    mensagem = Column(Text)

    __table_args__ = (
        Index("ix_recalls_clinica_paciente", "clinica_id", "paciente_id"),
    )


class Anamnese(Base):
    """Sprint 8: questionário clínico estruturado por paciente.

    1 anamnese por paciente (substitui ao re-preencher — versão é o atualizado_em).
    Template de perguntas vem de core/anamnese.py por especialidade da clínica.
    Respostas em JSON: {"<key>": {"resposta": bool|str, "observacao": str|None}}.
    """
    __tablename__ = "anamneses"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    paciente_id = Column(String, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True)
    respostas = Column(JSON, nullable=False, default=dict)
    preenchida_por = Column(String, ForeignKey("usuarios.id", ondelete="SET NULL"))
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("clinica_id", "paciente_id", name="uq_anamnese_paciente"),
    )


class Procedimento(Base):
    """Sprint 9: catálogo de procedimentos da clínica.

    Quando selecionado no agendamento, auto-preenche duracao_minutos e cor.
    """
    __tablename__ = "procedimentos"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = Column(String(120), nullable=False)
    duracao_minutos = Column(Integer, default=30, nullable=False)
    cor = Column(String(7), default="#E8B4B8")  # hex color for agenda block
    ativo = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("clinica_id", "nome", name="uq_procedimento_clinica_nome"),
    )


class BloqueioAgenda(Base):
    """Sprint 9: bloqueio de horário na agenda (almoço, férias, feriado).

    profissional_id NULL = bloqueio geral da clínica.
    inicio/fim = UTC naive (mesmo padrão dos agendamentos).
    """
    __tablename__ = "bloqueios_agenda"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, ForeignKey("clinicas.id", ondelete="CASCADE"), nullable=False, index=True)
    profissional_id = Column(String, ForeignKey("profissionais.id", ondelete="CASCADE"), nullable=True, index=True)
    inicio = Column(DateTime, nullable=False, index=True)
    fim = Column(DateTime, nullable=False)
    motivo = Column(String(120), default="Bloqueio")
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_bloqueios_clinica_inicio", "clinica_id", "inicio"),
    )


class AdminTokenRevogado(Base):
    """Sprint 6: blacklist de JWTs admin revogados (logout / rotação)."""
    __tablename__ = "admin_tokens_revogados"

    id = Column(String, primary_key=True, default=gen_id)
    jti = Column(String, unique=True, nullable=False, index=True)  # JWT id
    revogado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    expira_em = Column(DateTime, nullable=False, index=True)  # pra cleanup


class AuditLog(Base):
    """Toda alteração em dados sensíveis fica registrada aqui. LGPD Art. 37."""
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=gen_id)
    clinica_id = Column(String, index=True)  # SEM FK pra preservar log mesmo se clínica for deletada
    usuario_id = Column(String, index=True)
    acao = Column(String, nullable=False)  # ver AcaoAudit
    recurso = Column(String, nullable=False)  # "paciente" | "agendamento" | etc
    recurso_id = Column(String, index=True)
    ip = Column(String)
    user_agent = Column(String)
    detalhes = Column(JSON)
    quando = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
