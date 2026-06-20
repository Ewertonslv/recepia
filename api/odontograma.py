"""Odontograma — estado atual dos dentes por paciente (1 odontograma POR PACIENTE).

Sprint 2 — específico pra clínicas com `especialidade=odonto`.

Decisões arquiteturais:
- 1 odontograma por paciente (NÃO por consulta). Estado vigente sobrescreve.
- Storage: campo JSON `Paciente.odontograma` no formato:
    {"<fdi_num>": {"estado": str, "observacao": str|None,
                   "atualizado_em": iso, "atualizado_por": usuario_id|None}}
- Numeração FDI: quadrantes 1-4 (permanentes 11-18, 21-28, 31-38, 41-48)
  + quadrantes 5-8 (decíduos 51-55, 61-65, 71-75, 81-85).
- Histórico vive no audit_log (sem tabela versionada).

Hardenings LGPD (dado de saúde — Art. 11):
- Audit READ em GET (1 entry agregada por chamada).
- Audit UPDATE por dente com `{dente, estado_anterior, estado_novo}`.
- HTTP 422 se a clínica não for odonto (não faz sentido endpoint).
- Validação rigorosa de FDI (regex) e enum de estado.
- `flag_modified` no JSON pra SQLAlchemy detectar mutação in-place.
"""
import re
from datetime import datetime
from core.timezones import agora_utc
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from core.deps import audit_context, clinica_atual, requer_clinica_ativa, usuario_atual
from core import audit
from database import get_db_dependency
from models import AcaoAudit, Clinica, Paciente, Usuario


router = APIRouter(
    prefix="/api/pacientes/{paciente_id}/odontograma",
    tags=["odontograma"],
)


# ============================================================================
# Constantes — numeração FDI + estados válidos
# ============================================================================

# Regex FDI: 2 dígitos, primeiro 1-8 (quadrante), segundo 1-8 (posição).
# Permanentes: quadrantes 1-4 (1x molar até 8x molar).
# Decíduos: quadrantes 5-8 (até posição 5 — molares decíduos).
_RE_FDI = re.compile(r"^[1-8][1-8]$")

ESTADOS_VALIDOS = {
    "hígido",
    "cárie",
    "restauração",
    "ausente",
    "coroa",
    "canal",
    "extração_indicada",
    "prótese",
    "implante",
}

MAX_OBS_LEN = 500
MAX_LOTE_ITENS = 64  # 32 permanentes + 20 decíduos = 52 dentes máx + margem


# ============================================================================
# Schemas
# ============================================================================

class DenteUpdate(BaseModel):
    dente: str = Field(..., description="Código FDI do dente (ex: '11', '36', '52')")
    estado: str = Field(..., description="Um dos estados válidos")
    observacao: Optional[str] = Field(None, max_length=MAX_OBS_LEN)

    @field_validator("dente")
    @classmethod
    def _valida_fdi(cls, v: str) -> str:
        v = v.strip()
        if not _RE_FDI.match(v):
            raise ValueError("Código FDI inválido (esperado 2 dígitos 1-8, ex: '11')")
        # Quadrantes decíduos (5-8) só vão até posição 5
        quadrante = int(v[0])
        posicao = int(v[1])
        if quadrante >= 5 and posicao > 5:
            raise ValueError(f"Quadrante decíduo {quadrante} só tem posições 1-5")
        return v

    @field_validator("estado")
    @classmethod
    def _valida_estado(cls, v: str) -> str:
        v = v.strip()
        if v not in ESTADOS_VALIDOS:
            raise ValueError(
                f"Estado inválido. Use um de: {sorted(ESTADOS_VALIDOS)}"
            )
        return v

    @field_validator("observacao")
    @classmethod
    def _normaliza_obs(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class LoteUpdate(BaseModel):
    atualizacoes: list[DenteUpdate] = Field(
        ..., min_length=1, max_length=MAX_LOTE_ITENS,
    )


# ============================================================================
# Helpers
# ============================================================================

def _exigir_odonto(clinica: Clinica) -> None:
    """Odontograma só faz sentido pra clínica de odontologia."""
    if (clinica.especialidade or "").strip().lower() != "odonto":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Odontograma disponível apenas para clínicas com especialidade=odonto",
        )


def _buscar_paciente(db: Session, paciente_id: str, clinica_id: str) -> Paciente:
    pac = (
        db.query(Paciente)
        .filter(
            Paciente.id == paciente_id,
            Paciente.clinica_id == clinica_id,
            Paciente.deletado_em.is_(None),
        )
        .first()
    )
    if not pac:
        raise HTTPException(404, "Paciente não encontrado")
    return pac


def _aplicar_update(
    odontograma: dict,
    dente: str,
    estado: str,
    observacao: Optional[str],
    usuario_id: str,
) -> tuple[Optional[str], str]:
    """Aplica update in-place no dict e retorna (estado_anterior, estado_novo)."""
    entry_anterior = odontograma.get(dente) or {}
    estado_anterior = entry_anterior.get("estado")
    odontograma[dente] = {
        "estado": estado,
        "observacao": observacao,
        "atualizado_em": agora_utc().isoformat(),
        "atualizado_por": usuario_id,
    }
    return estado_anterior, estado


# ============================================================================
# Endpoints
# ============================================================================

@router.get("")
def obter(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Retorna o odontograma do paciente. Dict vazio se nunca foi usado."""
    _exigir_odonto(clinica)
    pac = _buscar_paciente(db, paciente_id, clinica.id)

    # `default=dict` no model garante {} mas legacy rows podem ter NULL — normalizar.
    estado = pac.odontograma or {}

    audit.log(
        db, **ctx, acao=AcaoAudit.READ, recurso="odontograma",
        recurso_id=pac.id,
        detalhes={"paciente_id": pac.id, "dentes_registrados": len(estado)},
    )
    db.commit()
    return {"paciente_id": pac.id, "odontograma": estado}


@router.put("")
def atualizar_dente(
    paciente_id: str,
    payload: DenteUpdate,
    clinica: Clinica = Depends(requer_clinica_ativa),
    usuario: Usuario = Depends(usuario_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Atualiza UM dente, preservando os outros."""
    _exigir_odonto(clinica)
    pac = _buscar_paciente(db, paciente_id, clinica.id)

    odontograma = dict(pac.odontograma or {})
    estado_anterior, estado_novo = _aplicar_update(
        odontograma, payload.dente, payload.estado, payload.observacao, usuario.id,
    )
    pac.odontograma = odontograma
    flag_modified(pac, "odontograma")

    audit.log(
        db, **ctx, acao=AcaoAudit.UPDATE, recurso="odontograma",
        recurso_id=pac.id,
        detalhes={
            "dente": payload.dente,
            "estado_anterior": estado_anterior,
            "estado_novo": estado_novo,
        },
    )
    db.commit()
    db.refresh(pac)
    return {"paciente_id": pac.id, "odontograma": pac.odontograma}


@router.put("/lote")
def atualizar_lote(
    paciente_id: str,
    payload: LoteUpdate,
    clinica: Clinica = Depends(requer_clinica_ativa),
    usuario: Usuario = Depends(usuario_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Marca vários dentes de uma vez (ex: importar exame inicial).

    Audit log: 1 entry por dente alterado (rastreabilidade preservada).
    """
    _exigir_odonto(clinica)
    pac = _buscar_paciente(db, paciente_id, clinica.id)

    # Dedup por dente: última ocorrência vence (evita audit duplicado dentro do mesmo lote).
    por_dente: dict[str, DenteUpdate] = {}
    for item in payload.atualizacoes:
        por_dente[item.dente] = item

    odontograma = dict(pac.odontograma or {})
    for dente, item in por_dente.items():
        estado_anterior, estado_novo = _aplicar_update(
            odontograma, item.dente, item.estado, item.observacao, usuario.id,
        )
        audit.log(
            db, **ctx, acao=AcaoAudit.UPDATE, recurso="odontograma",
            recurso_id=pac.id,
            detalhes={
                "dente": dente,
                "estado_anterior": estado_anterior,
                "estado_novo": estado_novo,
                "lote": True,
            },
        )

    pac.odontograma = odontograma
    flag_modified(pac, "odontograma")
    db.commit()
    db.refresh(pac)
    return {
        "paciente_id": pac.id,
        "odontograma": pac.odontograma,
        "atualizados": len(por_dente),
    }
