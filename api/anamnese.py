"""Anamnese — questionário clínico estruturado por paciente.

GET retorna o template (perguntas por especialidade da clínica) + respostas atuais.
PUT salva respostas (cria ou atualiza — 1 anamnese por paciente).

LGPD Art. 11 (dado sensível de saúde): audit READ em GET, audit UPDATE em PUT.
"""
from core.timezones import agora_utc
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import audit
from core.anamnese import template_para_especialidade, todas_as_chaves
from core.deps import audit_context, clinica_atual, requer_clinica_ativa
from database import get_db_dependency
from models import AcaoAudit, Anamnese, Clinica, Paciente

router = APIRouter(prefix="/api/pacientes", tags=["anamnese"])


class AnamneseIn(BaseModel):
    # dict["<key>", {"resposta": bool|str, "observacao": str|None}]
    respostas: dict[str, Any]


@router.get("/{paciente_id}/anamnese")
def obter(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
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

    template = template_para_especialidade(clinica.especialidade or "odonto")
    anamnese = (
        db.query(Anamnese)
        .filter(Anamnese.clinica_id == clinica.id, Anamnese.paciente_id == paciente_id)
        .first()
    )

    audit.log(
        db, **ctx,
        acao=AcaoAudit.READ, recurso="anamnese", recurso_id=anamnese.id if anamnese else paciente_id,
        detalhes={"paciente_id": paciente_id, "preenchida": bool(anamnese)},
    )
    db.commit()

    return {
        "paciente_id": paciente_id,
        "especialidade": clinica.especialidade or "odonto",
        "template": template,
        "respostas": anamnese.respostas if anamnese else {},
        "preenchida": bool(anamnese),
        "atualizado_em": anamnese.atualizado_em.isoformat() + "Z" if anamnese else None,
    }


@router.put("/{paciente_id}/anamnese")
def salvar(
    paciente_id: str,
    payload: AnamneseIn,
    clinica: Clinica = Depends(requer_clinica_ativa),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
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

    # Filtra respostas: aceita só keys que existem no template atual da especialidade.
    # Evita lixo de pergunta removida ou tentativa de injection com keys arbitrárias.
    chaves_validas = todas_as_chaves(clinica.especialidade or "odonto")
    respostas_filtradas = {
        k: v for k, v in (payload.respostas or {}).items()
        if k in chaves_validas and isinstance(v, dict)
    }

    anamnese = (
        db.query(Anamnese)
        .filter(Anamnese.clinica_id == clinica.id, Anamnese.paciente_id == paciente_id)
        .first()
    )

    if anamnese:
        anamnese.respostas = respostas_filtradas
        anamnese.preenchida_por = ctx.get("usuario_id") or anamnese.preenchida_por
        anamnese.atualizado_em = agora_utc()
        acao = AcaoAudit.UPDATE
    else:
        anamnese = Anamnese(
            clinica_id=clinica.id,
            paciente_id=paciente_id,
            respostas=respostas_filtradas,
            preenchida_por=ctx.get("usuario_id"),
        )
        db.add(anamnese)
        acao = AcaoAudit.CREATE

    db.flush()
    audit.log(
        db, **ctx,
        acao=acao, recurso="anamnese", recurso_id=anamnese.id,
        detalhes={"paciente_id": paciente_id, "qtde_respostas": len(respostas_filtradas)},
    )
    db.commit()
    db.refresh(anamnese)

    return {
        "id": anamnese.id,
        "paciente_id": paciente_id,
        "respostas": anamnese.respostas,
        "atualizado_em": anamnese.atualizado_em.isoformat() + "Z",
    }
