"""CRUD de pacientes — multi-tenant, scoped por clinica_id.

Hardenings:
- F9: audit_context preenche usuario_id/ip/user_agent.
- F20/G4: soft delete + endpoint /exportar (LGPD Art. 18 V e VI).
- LGPD opt-out: paciente que pediu pra sair fica visível mas marcado.

Sprint 2:
- Campos expandidos pra multi-vertical (data_nascimento, cpf, endereço, etc).
- Validação de obrigatoriedade depende da especialidade da clínica
  (core/especialidades.config_efetiva).
- /timeline merge agendamentos + prontuarios + interacoes (audit READ — LGPD).
"""
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import case, func, literal, select, union_all
from sqlalchemy.orm import Session

from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from core.documentos import sanitizar_cep, sanitizar_cpf, validar_cpf
from core.especialidades import config_efetiva, validar_paciente_para_especialidade
from core.phones import TelefoneInvalido, normalizar as normalizar_telefone
from core.timezones import agora_utc
from core import audit
from database import get_db_dependency
from models import AcaoAudit, Agendamento, Clinica, Interacao, Paciente, Profissional, Prontuario, Status

router = APIRouter(prefix="/api/pacientes", tags=["pacientes"])


_SEXOS_VALIDOS = {"M", "F", "O", "N"}


class PacienteIn(BaseModel):
    nome: str = Field(..., min_length=2)
    telefone: str = Field(..., min_length=8)
    email: EmailStr | None = None
    notas: str | None = None

    # Sprint 2 — todos opcionais no schema; obrigatoriedade vem da especialidade.
    data_nascimento: date | None = None
    cpf: str | None = None
    rg: str | None = None
    sexo: str | None = None
    profissao: str | None = None
    estado_civil: str | None = None
    telefone_fixo: str | None = None
    diagnostico_breve: str | None = None
    observacoes_clinicas: str | None = None
    endereco_rua: str | None = None
    endereco_numero: str | None = None
    endereco_complemento: str | None = None
    endereco_bairro: str | None = None
    endereco_cidade: str | None = None
    endereco_uf: str | None = None
    endereco_cep: str | None = None

    @field_validator("cpf", mode="before")
    @classmethod
    def _val_cpf(cls, v):
        if v is None or v == "":
            return None
        sanitizado = sanitizar_cpf(v)
        if not sanitizado or not validar_cpf(sanitizado):
            raise ValueError("CPF inválido")
        return sanitizado

    @field_validator("sexo", mode="before")
    @classmethod
    def _val_sexo(cls, v):
        if v is None or v == "":
            return None
        if v not in _SEXOS_VALIDOS:
            raise ValueError("Sexo deve ser M, F, O ou N")
        return v

    @field_validator("endereco_uf", mode="before")
    @classmethod
    def _val_uf(cls, v):
        if v is None or v == "":
            return None
        v = v.strip().upper()
        if len(v) != 2:
            raise ValueError("UF deve ter 2 caracteres")
        return v

    @field_validator("endereco_cep", mode="before")
    @classmethod
    def _val_cep(cls, v):
        if v is None or v == "":
            return None
        sanitizado = sanitizar_cep(v)
        if not sanitizado:
            raise ValueError("CEP inválido")
        return sanitizado


class PacienteOut(BaseModel):
    id: str
    nome: str
    telefone: str
    email: str | None
    notas: str | None
    opt_out: bool
    criado_em: datetime

    # Sprint 2 — campos expandidos
    foto_key: str | None = None
    data_nascimento: date | None = None
    cpf: str | None = None
    rg: str | None = None
    sexo: str | None = None
    profissao: str | None = None
    estado_civil: str | None = None
    telefone_fixo: str | None = None
    diagnostico_breve: str | None = None
    observacoes_clinicas: str | None = None
    endereco_rua: str | None = None
    endereco_numero: str | None = None
    endereco_complemento: str | None = None
    endereco_bairro: str | None = None
    endereco_cidade: str | None = None
    endereco_uf: str | None = None
    endereco_cep: str | None = None

    class Config:
        from_attributes = True


class PacienteOutCompleto(PacienteOut):
    stats: dict
    foto_url: str | None = None


# Campos do PacienteIn que se mapeiam 1:1 pro model. Lista usada por criar/atualizar.
_CAMPOS_EXPANDIDOS = (
    "data_nascimento", "cpf", "rg", "sexo", "profissao", "estado_civil",
    "telefone_fixo", "diagnostico_breve", "observacoes_clinicas",
    "endereco_rua", "endereco_numero", "endereco_complemento", "endereco_bairro",
    "endereco_cidade", "endereco_uf", "endereco_cep",
)


def _valida_especialidade_ou_422(clinica: Clinica, dados: dict):
    """Se faltarem campos obrigatórios pra especialidade da clínica, 422."""
    cfg = config_efetiva(clinica)
    faltantes = validar_paciente_para_especialidade(cfg, dados)
    if faltantes:
        raise HTTPException(
            status_code=422,
            detail={
                "campos_faltantes": faltantes,
                "especialidade": cfg.slug,
                "mensagem": f"Campos obrigatórios faltando pra especialidade {cfg.nome}",
            },
        )


def _stats_paciente(db: Session, clinica_id: str, paciente_id: str) -> dict:
    """Agregação rápida de status pra mostrar no cabeçalho do prontuário.

    Performance: antes fazia 8 queries (1 count total + 6 por status + 1 prontuários).
    Agora faz 2 queries — 1 agregada em agendamentos + 1 count em prontuários.
    """
    row = db.query(
        func.count(Agendamento.id).label("total"),
        func.count(case((Agendamento.status == Status.REALIZADO, 1))).label("atendidos"),
        func.count(case((Agendamento.status == Status.NO_SHOW, 1))).label("nao_atendidos"),
        func.count(case((Agendamento.status == Status.CANCELADO, 1))).label("cancelados"),
        func.count(case((Agendamento.status == Status.REAGENDADO, 1))).label("remarcados"),
        func.count(case((Agendamento.status == Status.PENDENTE, 1))).label("pendentes"),
        func.count(case((Agendamento.status == Status.CONFIRMADO, 1))).label("confirmados"),
    ).filter(
        Agendamento.clinica_id == clinica_id,
        Agendamento.paciente_id == paciente_id,
    ).one()

    prontuarios_total = db.query(func.count(Prontuario.id)).filter(
        Prontuario.clinica_id == clinica_id,
        Prontuario.paciente_id == paciente_id,
    ).scalar() or 0

    return {
        "agendamentos_total": row.total or 0,
        "atendidos": row.atendidos or 0,
        "nao_atendidos": row.nao_atendidos or 0,
        "cancelados": row.cancelados or 0,
        "remarcados": row.remarcados or 0,
        "pendentes": row.pendentes or 0,
        "confirmados": row.confirmados or 0,
        "prontuarios_total": prontuarios_total,
    }


def _to_out_completo(paciente: Paciente, db: Session) -> PacienteOutCompleto:
    stats = _stats_paciente(db, paciente.clinica_id, paciente.id)
    foto_url = f"/api/pacientes/{paciente.id}/foto" if paciente.foto_key else None
    return PacienteOutCompleto(
        **PacienteOut.model_validate(paciente).model_dump(),
        stats=stats,
        foto_url=foto_url,
    )


@router.post("", response_model=PacienteOut, status_code=status.HTTP_201_CREATED)
def criar(
    payload: PacienteIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    try:
        telefone_normalizado = normalizar_telefone(payload.telefone)
    except TelefoneInvalido as e:
        raise HTTPException(400, f"Telefone inválido: {e}")

    # Valida obrigatoriedade da especialidade ANTES de mexer no DB.
    # CPF/UF/CEP já saíram normalizados do pydantic (validators acima).
    _valida_especialidade_ou_422(clinica, payload.model_dump())

    paciente = Paciente(
        clinica_id=clinica.id,
        nome=payload.nome,
        telefone=telefone_normalizado,
        email=payload.email,
        notas=payload.notas,
    )
    for campo in _CAMPOS_EXPANDIDOS:
        setattr(paciente, campo, getattr(payload, campo))

    db.add(paciente)
    db.flush()
    audit.log(db, **ctx, acao=AcaoAudit.CREATE, recurso="paciente",
              recurso_id=paciente.id, detalhes={"nome": payload.nome})
    db.commit()
    db.refresh(paciente)
    return paciente


@router.get("", response_model=list[PacienteOut])
def listar(
    incluir_deletados: bool = False,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(Paciente).filter(Paciente.clinica_id == clinica.id)
    if not incluir_deletados:
        q = q.filter(Paciente.deletado_em.is_(None))
    return q.all()


@router.get("/{paciente_id}", response_model=PacienteOutCompleto)
def obter(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    paciente = (
        db.query(Paciente)
        .filter(Paciente.id == paciente_id, Paciente.clinica_id == clinica.id)
        .first()
    )
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado")
    return _to_out_completo(paciente, db)


@router.put("/{paciente_id}", response_model=PacienteOut)
def atualizar(
    paciente_id: str,
    payload: PacienteIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    paciente = (
        db.query(Paciente)
        .filter(Paciente.id == paciente_id, Paciente.clinica_id == clinica.id)
        .first()
    )
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado")

    try:
        telefone_normalizado = normalizar_telefone(payload.telefone)
    except TelefoneInvalido as e:
        raise HTTPException(400, f"Telefone inválido: {e}")

    # Monta dict "final" do paciente (estado atual + alterações) pra validar
    # obrigatoriedade da especialidade considerando o que vai persistir.
    alterados = payload.model_dump(exclude_unset=True)
    snapshot = {
        "nome": paciente.nome,
        "telefone": telefone_normalizado,
        "email": paciente.email,
        "notas": paciente.notas,
    }
    for campo in _CAMPOS_EXPANDIDOS:
        snapshot[campo] = getattr(paciente, campo)
    snapshot.update(alterados)
    snapshot["telefone"] = telefone_normalizado

    _valida_especialidade_ou_422(clinica, snapshot)

    # Aplica só os campos que vieram no payload (exclude_unset).
    paciente.nome = payload.nome
    paciente.telefone = telefone_normalizado
    if "email" in alterados:
        paciente.email = payload.email
    if "notas" in alterados:
        paciente.notas = payload.notas
    for campo in _CAMPOS_EXPANDIDOS:
        if campo in alterados:
            setattr(paciente, campo, getattr(payload, campo))

    audit.log(db, **ctx, acao=AcaoAudit.UPDATE, recurso="paciente", recurso_id=paciente.id)
    db.commit()
    db.refresh(paciente)
    return paciente


@router.delete("/{paciente_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_soft(
    paciente_id: str,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """LGPD Art. 18 — soft delete com grace de 30 dias. Hard delete via job.

    Após deletar:
    - Paciente fica oculto em listagens padrão (filtra deletado_em)
    - Não recebe mais mensagens automáticas (scheduler ignora)
    - Pode ser restaurado em até 30d setando deletado_em = NULL
    """
    paciente = (
        db.query(Paciente)
        .filter(Paciente.id == paciente_id, Paciente.clinica_id == clinica.id)
        .first()
    )
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado")
    paciente.deletado_em = agora_utc()
    paciente.opt_out = True  # paranoia: para de mandar mensagem imediatamente
    audit.log(
        db, **ctx,
        acao=AcaoAudit.DELETE, recurso="paciente", recurso_id=paciente.id,
        detalhes={"tipo": "soft_delete", "motivo": "lgpd_direito_esquecimento"},
    )
    db.commit()


@router.get("/{paciente_id}/historico")
def historico_paciente(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    """G3: histórico completo do paciente — agendamentos + todas as interações."""
    paciente = (
        db.query(Paciente)
        .filter(Paciente.id == paciente_id, Paciente.clinica_id == clinica.id)
        .first()
    )
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado")
    agendamentos = (
        db.query(Agendamento)
        .filter(
            Agendamento.paciente_id == paciente_id,
            Agendamento.clinica_id == clinica.id,
        )
        .order_by(Agendamento.data_hora.desc())
        .all()
    )
    return [
        {
            "id": a.id,
            "data_hora_utc": a.data_hora.isoformat() + "Z",
            "servico": a.servico,
            "status": a.status,
            "interacoes": [
                {
                    "tipo": i.tipo,
                    "mensagem_enviada": i.mensagem_enviada,
                    "mensagem_recebida": i.mensagem_recebida,
                    "quando_utc": i.quando.isoformat() + "Z",
                }
                for i in sorted(a.interacoes, key=lambda x: x.quando)
            ],
        }
        for a in agendamentos
    ]


@router.get("/{paciente_id}/timeline")
def timeline_paciente(
    paciente_id: str,
    limit: int = 50,
    offset: int = 0,
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Timeline unificada: agendamentos + prontuários + interações (desc por timestamp).

    LGPD: timeline expõe prontuários (dado sensível Art. 11) — audit READ obrigatório.
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    # Sprint 6: timeline NÃO deve expor paciente soft-deletado (LGPD direito esquecimento)
    paciente = (
        db.query(Paciente)
        .filter(
            Paciente.id == paciente_id,
            Paciente.clinica_id == clinica.id,
            Paciente.deletado_em.is_(None),
        )
        .first()
    )
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado")

    # Paginação no SQL via UNION ALL — antes carregava TUDO em memória e paginava
    # depois (OOM com pacientes de longa data). Cada SELECT projeta o mesmo shape
    # (id, tipo, quando) pra unir; depois buscamos só os detalhes dos N itens da
    # página atual.
    q_ag = select(
        Agendamento.id.label("id"),
        literal("agendamento").label("tipo"),
        Agendamento.data_hora.label("quando"),
    ).where(
        Agendamento.clinica_id == clinica.id,
        Agendamento.paciente_id == paciente_id,
    )
    q_pr = select(
        Prontuario.id.label("id"),
        literal("prontuario").label("tipo"),
        Prontuario.criado_em.label("quando"),
    ).where(
        Prontuario.clinica_id == clinica.id,
        Prontuario.paciente_id == paciente_id,
    )
    # Interacao tem clinica_id próprio (FK direto) — filtra por ele E pelo paciente
    # via join no Agendamento (interações de outros pacientes da mesma clínica não entram).
    q_in = (
        select(
            Interacao.id.label("id"),
            literal("interacao").label("tipo"),
            Interacao.quando.label("quando"),
        )
        .join(Agendamento, Interacao.agendamento_id == Agendamento.id)
        .where(
            Interacao.clinica_id == clinica.id,
            Agendamento.clinica_id == clinica.id,
            Agendamento.paciente_id == paciente_id,
        )
    )

    union_q = union_all(q_ag, q_pr, q_in).subquery()
    # Total separado (SQL count) — preserva contrato `{"total": N, ...}`.
    total = db.query(func.count()).select_from(union_q).scalar() or 0

    pagina_q = (
        select(union_q.c.id, union_q.c.tipo, union_q.c.quando)
        .order_by(union_q.c.quando.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(pagina_q).all()

    # Hidrata só os IDs que caíram na página (1 query por tipo presente).
    ids_ag = [r.id for r in rows if r.tipo == "agendamento"]
    ids_pr = [r.id for r in rows if r.tipo == "prontuario"]
    ids_in = [r.id for r in rows if r.tipo == "interacao"]
    map_ag = {a.id: a for a in db.query(Agendamento).filter(Agendamento.id.in_(ids_ag)).all()} if ids_ag else {}
    map_pr = {p.id: p for p in db.query(Prontuario).filter(Prontuario.id.in_(ids_pr)).all()} if ids_pr else {}
    map_in = {i.id: i for i in db.query(Interacao).filter(Interacao.id.in_(ids_in)).all()} if ids_in else {}

    pagina = []
    for r in rows:
        if r.tipo == "agendamento":
            a = map_ag.get(r.id)
            if not a:
                continue
            pagina.append({
                "tipo": "agendamento",
                "id": a.id,
                "quando": a.data_hora.isoformat() + "Z",
                "dados": {
                    "data_hora": a.data_hora.isoformat() + "Z",
                    "status": a.status,
                    "servico": a.servico,
                    "profissional": a.profissional,
                },
            })
        elif r.tipo == "prontuario":
            p = map_pr.get(r.id)
            if not p:
                continue
            pagina.append({
                "tipo": "prontuario",
                "id": p.id,
                "quando": p.criado_em.isoformat() + "Z",
                "dados": {
                    "anotacoes": p.anotacoes,
                    "fotos_count": len(p.fotos or []),
                    "alergias": p.alergias or [],
                },
            })
        elif r.tipo == "interacao":
            i = map_in.get(r.id)
            if not i:
                continue
            pagina.append({
                "tipo": "interacao",
                "id": i.id,
                "quando": i.quando.isoformat() + "Z",
                "dados": {
                    "tipo": i.tipo,
                    "mensagem_enviada": i.mensagem_enviada,
                    "mensagem_recebida": i.mensagem_recebida,
                },
            })

    audit.log(
        db, **ctx,
        acao=AcaoAudit.READ, recurso="paciente_timeline", recurso_id=paciente.id,
        detalhes={"itens_total": total, "retornados": len(pagina)},
    )
    db.commit()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "itens": pagina,
    }


@router.get("/{paciente_id}/exportar")
def exportar_dados(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """LGPD Art. 18 V (portabilidade) — exporta TODOS os dados do paciente em JSON.

    Inclui: dados pessoais, todos os agendamentos, todas as interações WhatsApp.
    """
    paciente = (
        db.query(Paciente)
        .filter(Paciente.id == paciente_id, Paciente.clinica_id == clinica.id)
        .first()
    )
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado")

    agendamentos = db.query(Agendamento).filter(
        Agendamento.paciente_id == paciente_id,
        Agendamento.clinica_id == clinica.id,
    ).all()
    agendamento_ids = [a.id for a in agendamentos]
    interacoes = (
        db.query(Interacao)
        .filter(
            Interacao.clinica_id == clinica.id,
            Interacao.agendamento_id.in_(agendamento_ids) if agendamento_ids else False,
        )
        .all()
    )
    # LGPD: prontuários (dado sensível Art. 11) + metadata de fotos (sem bytes — só keys).
    # User exporta cada foto via GET próprio se quiser baixar — bytes em JSON inflaria o payload.
    prontuarios = (
        db.query(Prontuario)
        .filter(
            Prontuario.clinica_id == clinica.id,
            Prontuario.paciente_id == paciente_id,
        )
        .order_by(Prontuario.criado_em.desc())
        .all()
    )
    prof_ids = {p.profissional_id for p in prontuarios if p.profissional_id}
    profs_map = {}
    if prof_ids:
        profs_map = {
            p.id: p.nome
            for p in db.query(Profissional).filter(
                Profissional.id.in_(prof_ids),
                Profissional.clinica_id == clinica.id,
            ).all()
        }

    audit.log(
        db, **ctx,
        acao=AcaoAudit.EXPORT, recurso="paciente", recurso_id=paciente.id,
        detalhes={
            "agendamentos": len(agendamentos),
            "interacoes": len(interacoes),
            "prontuarios": len(prontuarios),
            "fotos_total": sum(len(p.fotos or []) for p in prontuarios),
        },
    )
    db.commit()

    return {
        "exportado_em": agora_utc().isoformat() + "Z",
        "clinica": {"id": clinica.id, "nome": clinica.nome},
        "paciente": {
            "id": paciente.id,
            "nome": paciente.nome,
            "telefone": paciente.telefone,
            "email": paciente.email,
            "notas": paciente.notas,
            "opt_out": paciente.opt_out,
            "opt_out_em": paciente.opt_out_em.isoformat() + "Z" if paciente.opt_out_em else None,
            "criado_em": paciente.criado_em.isoformat() + "Z",
            "atualizado_em": paciente.atualizado_em.isoformat() + "Z",
            "deletado_em": paciente.deletado_em.isoformat() + "Z" if paciente.deletado_em else None,
        },
        "agendamentos": [
            {
                "id": a.id,
                "data_hora_utc": a.data_hora.isoformat() + "Z",
                "duracao_minutos": a.duracao_minutos,
                "servico": a.servico,
                "profissional": a.profissional,
                "status": a.status,
                "criado_em": a.criado_em.isoformat() + "Z",
            }
            for a in agendamentos
        ],
        "interacoes": [
            {
                "id": i.id,
                "agendamento_id": i.agendamento_id,
                "tipo": i.tipo,
                "mensagem_enviada": i.mensagem_enviada,
                "mensagem_recebida": i.mensagem_recebida,
                "quando_utc": i.quando.isoformat() + "Z",
            }
            for i in interacoes
        ],
        "prontuarios": [
            {
                "id": p.id,
                "profissional_id": p.profissional_id,
                "profissional_nome": profs_map.get(p.profissional_id),
                "agendamento_id": p.agendamento_id,
                "anotacoes": p.anotacoes,
                "procedimentos_realizados": p.procedimentos_realizados,
                "alergias": p.alergias or [],
                "proxima_acao": p.proxima_acao,
                "fotos": [
                    {
                        "key": f.get("key"),
                        "sha256": f.get("sha256"),
                        "mime": f.get("mime"),
                        "tamanho_bytes": f.get("tamanho_bytes"),
                        "descricao": f.get("descricao"),
                        "tipo": f.get("tipo"),
                        "criado_em": f.get("criado_em"),
                        "url_download": f"/api/prontuarios/{p.id}/fotos/{f.get('key')}",
                    }
                    for f in (p.fotos or [])
                ],
                "criado_em": p.criado_em.isoformat() + "Z",
                "atualizado_em": p.atualizado_em.isoformat() + "Z",
            }
            for p in prontuarios
        ],
    }
