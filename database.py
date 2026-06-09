from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from config import settings

# pool_size/max_overflow só valem pro QueuePool (Postgres). O SQLite usado nos
# testes (sqlite:///:memory:) usa SingletonThreadPool, que não aceita max_overflow.
_engine_kwargs = {"pool_pre_ping": True}
if not settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@contextmanager
def get_db() -> Session:
    """Context manager para uso fora do FastAPI (jobs, scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_dependency():
    """Dependency injection do FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Cria todas as tabelas + aplica migrations idempotentes. Use Alembic em produção."""
    import models  # noqa: F401 — registra as classes no Base
    Base.metadata.create_all(bind=engine)
    _aplicar_migracoes_idempotentes()


def _aplicar_migracoes_idempotentes() -> None:
    """Migrations que precisam alterar tabelas existentes (create_all não faz ALTER).

    Cada bloco é idempotente — pode rodar 100x sem efeito colateral.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        # Sprint 1 D5: agendamentos.profissional_id (FK pra profissionais.id)
        conn.execute(text("""
            ALTER TABLE agendamentos
            ADD COLUMN IF NOT EXISTS profissional_id VARCHAR
            REFERENCES profissionais(id) ON DELETE SET NULL
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agendamentos_profissional_id "
            "ON agendamentos (profissional_id)"
        ))
        # Backfill best-effort: agendamentos com profissional (string) não-vazio e SEM
        # profissional_id → tenta achar profissional com nome exato na mesma clínica.
        # Faz nada se o nome não bater — mantém string original.
        conn.execute(text("""
            UPDATE agendamentos a
            SET profissional_id = p.id
            FROM profissionais p
            WHERE a.profissional_id IS NULL
              AND a.profissional IS NOT NULL
              AND TRIM(a.profissional) <> ''
              AND p.clinica_id = a.clinica_id
              AND LOWER(TRIM(p.nome)) = LOWER(TRIM(a.profissional))
        """))

        # Sprint 2 — clinicas: especialidade + endereço + logo + responsável técnico
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS especialidade VARCHAR(20) DEFAULT 'odonto' NOT NULL"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS config_paciente JSON DEFAULT '{}' NOT NULL"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS logo_key VARCHAR(80)"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS responsavel_tecnico VARCHAR(120)"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS registro_conselho VARCHAR(30)"))
        for col, tipo in [
            ("endereco_rua", "VARCHAR(120)"), ("endereco_numero", "VARCHAR(20)"),
            ("endereco_complemento", "VARCHAR(60)"), ("endereco_bairro", "VARCHAR(80)"),
            ("endereco_cidade", "VARCHAR(80)"), ("endereco_uf", "VARCHAR(2)"),
            ("endereco_cep", "VARCHAR(8)"),
        ]:
            conn.execute(text(f"ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS {col} {tipo}"))

        # Sprint 2 — pacientes: campos expandidos (todos nullable, validação por especialidade em runtime)
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS foto_key VARCHAR(80)"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS data_nascimento DATE"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cpf VARCHAR(11)"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS rg VARCHAR(20)"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS sexo VARCHAR(1)"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS profissao VARCHAR(80)"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS estado_civil VARCHAR(20)"))
        for col, tipo in [
            ("endereco_rua", "VARCHAR(120)"), ("endereco_numero", "VARCHAR(20)"),
            ("endereco_complemento", "VARCHAR(60)"), ("endereco_bairro", "VARCHAR(80)"),
            ("endereco_cidade", "VARCHAR(80)"), ("endereco_uf", "VARCHAR(2)"),
            ("endereco_cep", "VARCHAR(8)"), ("telefone_fixo", "VARCHAR(20)"),
        ]:
            conn.execute(text(f"ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS {col} {tipo}"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS diagnostico_breve TEXT"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS observacoes_clinicas TEXT"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS campos_extras JSON DEFAULT '{}' NOT NULL"))
        conn.execute(text("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS odontograma JSON DEFAULT '{}' NOT NULL"))
        # Índices úteis (CPF único parcial por clínica + aniversariantes)
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_pacientes_clinica_cpf ON pacientes(clinica_id, cpf) WHERE cpf IS NOT NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pacientes_clinica_dn ON pacientes(clinica_id, data_nascimento)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pacientes_cpf ON pacientes(cpf)"))

        # Sprint 3 — Planos de tratamento (N por paciente; sessões = prontuários vinculados)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS planos_tratamento (
                id VARCHAR PRIMARY KEY,
                clinica_id VARCHAR NOT NULL REFERENCES clinicas(id) ON DELETE CASCADE,
                paciente_id VARCHAR NOT NULL REFERENCES pacientes(id) ON DELETE CASCADE,
                nome VARCHAR(120) NOT NULL,
                descricao TEXT,
                sessoes_previstas INTEGER NOT NULL DEFAULT 1,
                status VARCHAR(20) NOT NULL DEFAULT 'ativo',
                profissional_id VARCHAR REFERENCES profissionais(id) ON DELETE SET NULL,
                criado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                atualizado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                concluido_em TIMESTAMP
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_planos_tratamento_clinica_id ON planos_tratamento(clinica_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_planos_tratamento_paciente_id ON planos_tratamento(paciente_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_planos_clinica_paciente ON planos_tratamento(clinica_id, paciente_id)"))

        # Sessões executadas vinculam-se ao plano via prontuarios.plano_tratamento_id
        conn.execute(text("""
            ALTER TABLE prontuarios
            ADD COLUMN IF NOT EXISTS plano_tratamento_id VARCHAR
            REFERENCES planos_tratamento(id) ON DELETE SET NULL
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prontuarios_plano ON prontuarios(plano_tratamento_id)"))

        # Sprint 3 — Recall automático: colunas em clinicas (tabela
        # recalls_enviados já vem via create_all).
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS recall_ativo BOOLEAN DEFAULT FALSE NOT NULL"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS recall_intervalo_dias INTEGER DEFAULT 180 NOT NULL"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS recall_template TEXT"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS recall_procedimento_chave VARCHAR(80) DEFAULT 'limpeza' NOT NULL"))

        # Sprint 4 — Self-signup + trial 7 dias
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS trial_expira_em DATE"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS origem_cadastro VARCHAR(20) DEFAULT 'admin_master' NOT NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clinicas_trial_expira ON clinicas(trial_expira_em) WHERE trial_expira_em IS NOT NULL"))
        conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS telefone VARCHAR(20)"))
        conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS aceitou_termos_em TIMESTAMP"))

        # Sprint 6 — blacklist de JWTs admin revogados (logout / rotação)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_tokens_revogados (
                id VARCHAR PRIMARY KEY,
                jti VARCHAR UNIQUE NOT NULL,
                revogado_em TIMESTAMP NOT NULL,
                expira_em TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_tokens_jti ON admin_tokens_revogados(jti)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_tokens_expira ON admin_tokens_revogados(expira_em)"))

        # Sprint 8 — anamnese estruturada (1 por paciente)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS anamneses (
                id VARCHAR PRIMARY KEY,
                clinica_id VARCHAR NOT NULL REFERENCES clinicas(id) ON DELETE CASCADE,
                paciente_id VARCHAR NOT NULL REFERENCES pacientes(id) ON DELETE CASCADE,
                respostas JSON NOT NULL DEFAULT '{}',
                preenchida_por VARCHAR REFERENCES usuarios(id) ON DELETE SET NULL,
                criado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                atualizado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                CONSTRAINT uq_anamnese_paciente UNIQUE (clinica_id, paciente_id)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anamneses_clinica_id ON anamneses(clinica_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anamneses_paciente_id ON anamneses(paciente_id)"))

        # Sprint 9 — catálogo de procedimentos
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS procedimentos (
                id VARCHAR PRIMARY KEY,
                clinica_id VARCHAR NOT NULL REFERENCES clinicas(id) ON DELETE CASCADE,
                nome VARCHAR(120) NOT NULL,
                duracao_minutos INTEGER NOT NULL DEFAULT 30,
                cor VARCHAR(7) NOT NULL DEFAULT '#E8B4B8',
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                criado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                atualizado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
                CONSTRAINT uq_procedimento_clinica_nome UNIQUE (clinica_id, nome)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_procedimentos_clinica_id ON procedimentos(clinica_id)"))

        # Sprint 9 — bloqueios de agenda
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bloqueios_agenda (
                id VARCHAR PRIMARY KEY,
                clinica_id VARCHAR NOT NULL REFERENCES clinicas(id) ON DELETE CASCADE,
                profissional_id VARCHAR REFERENCES profissionais(id) ON DELETE CASCADE,
                inicio TIMESTAMP NOT NULL,
                fim TIMESTAMP NOT NULL,
                motivo VARCHAR(120) NOT NULL DEFAULT 'Bloqueio',
                criado_em TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bloqueios_clinica_id ON bloqueios_agenda(clinica_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bloqueios_clinica_inicio ON bloqueios_agenda(clinica_id, inicio)"))

        # Sprint 9 — Google OAuth + admin improvements
        conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS google_id VARCHAR(128) UNIQUE"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_usuarios_google_id ON usuarios(google_id) WHERE google_id IS NOT NULL"))
        conn.execute(text("ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS notas_internas TEXT"))
