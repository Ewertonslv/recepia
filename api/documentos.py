"""Geração de documentos PDF por paciente — multi-tenant, scoped por clinica_id.

Sprint 2 — geração on-demand de PDFs (prontuário, atestado, declaração de
comparecimento, receituário). Templates Jinja2 em /app/templates/pdfs/.

Hardenings LGPD Art. 11/37/46 (dado sensível de saúde):
- Audit EXPORT obrigatório em TODA geração de PDF (LGPD Art. 18 — rastreabilidade).
- Cross-tenant: paciente / prontuario sempre validados contra clinica_id atual.
- Disponibilidade do tipo gatekeepada por config_efetiva(clinica).documentos
  (ex: receituário só em clínica médica).
- Headers `Cache-Control: no-store, private` — proxy não cacheia dado sensível.
- `Content-Disposition: attachment` força download e impede sniffing.
- `X-Content-Type-Options: nosniff` reforça MIME.
- Rate limit 10/min/IP — geração de PDF é cara (WeasyPrint).
- Nome do gerador (`gerado_por`) entra no template via watermark p/ rastreio de vazamento.
- Foto de prontuário capada em 6 no PDF (peso + privacidade — não exporta o álbum inteiro).
- Listagem GET / lê do audit_log (não cria registro físico — PDF é volátil).
"""
from datetime import date, datetime
from core.timezones import agora_utc
from typing import Optional
import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import audit
from core.deps import audit_context, clinica_atual, usuario_atual
from core.especialidades import config_efetiva
from core.foto_storage import ler_avatar, ler_logo, ler as fs_ler_foto
from core.limiter import limiter
from core.pdf import gerar_pdf
from database import get_db_dependency
from models import (
    AcaoAudit, AuditLog, Clinica,
    Paciente, Profissional, Prontuario, Usuario,
)


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pacientes/{paciente_id}/documentos", tags=["documentos"])


# ============================================================================
# Constantes
# ============================================================================

TIPOS_VALIDOS = {"prontuario", "atestado", "declaracao_comparecimento", "receituario", "termo_consentimento", "recibo"}
MAX_PRESCRICAO = 4000
MAX_FOTOS_PDF = 6  # cap pra não inchar PDF nem exportar álbum inteiro


# ============================================================================
# Schemas
# ============================================================================

class DocumentoGeradoOut(BaseModel):
    tipo: str
    gerado_em: datetime
    gerado_por_nome: Optional[str] = None
    prontuario_id: Optional[str] = None


# ============================================================================
# Helpers
# ============================================================================

def _carregar_paciente(db: Session, paciente_id: str, clinica_id: str) -> Paciente:
    p = db.query(Paciente).filter(
        Paciente.id == paciente_id,
        Paciente.clinica_id == clinica_id,
        Paciente.deletado_em.is_(None),
    ).first()
    if not p:
        raise HTTPException(404, "Paciente não encontrado")
    return p


def _to_dataurl(bytes_: bytes, mime: str = "image/webp") -> str:
    return f"data:{mime};base64,{base64.b64encode(bytes_).decode()}"


def _safe_logo_dataurl(clinica_id: str) -> Optional[str]:
    """Lê logo da clínica como data URL. Retorna None se não existe."""
    try:
        b = ler_logo(clinica_id)
    except Exception:
        log.exception("documentos: falha ao ler logo da clinica %s", clinica_id)
        return None
    if not b:
        return None
    return _to_dataurl(b)


def _safe_avatar_dataurl(clinica_id: str, paciente: Paciente) -> Optional[str]:
    """Lê foto do paciente como data URL. Retorna None se não existe."""
    if not paciente.foto_key:
        return None
    try:
        b = ler_avatar(clinica_id, paciente.id)
    except Exception:
        log.exception("documentos: falha ao ler avatar paciente=%s clinica=%s", paciente.id, clinica_id)
        return None
    return _to_dataurl(b)


def _safe_foto_prontuario_dataurl(clinica_id: str, prontuario_id: str, key: str) -> Optional[str]:
    try:
        b = fs_ler_foto(clinica_id, prontuario_id, key)
    except Exception:
        log.exception("documentos: falha ao ler foto prontuario=%s key=%s", prontuario_id, key)
        return None
    return _to_dataurl(b)


def _contexto_base(
    clinica: Clinica,
    paciente: Paciente,
    usuario: Usuario,
) -> dict:
    """Bloco comum a todos os templates (clínica + paciente + metadata)."""
    return {
        "clinica": {
            "nome": clinica.nome,
            "cnpj": clinica.cnpj,
            "endereco_rua": clinica.endereco_rua or "—",
            "endereco_numero": clinica.endereco_numero or "—",
            "endereco_complemento": clinica.endereco_complemento or "",
            "endereco_bairro": clinica.endereco_bairro or "—",
            "endereco_cidade": clinica.endereco_cidade or "—",
            "endereco_uf": clinica.endereco_uf or "—",
            "endereco_cep": clinica.endereco_cep or "—",
            "responsavel_tecnico": clinica.responsavel_tecnico or "—",
            "registro_conselho": clinica.registro_conselho or "—",
            "logo_dataurl": _safe_logo_dataurl(clinica.id),
        },
        "paciente": {
            "id": paciente.id,
            "nome": paciente.nome,
            "cpf": paciente.cpf,
            "rg": paciente.rg,
            "data_nascimento": paciente.data_nascimento,
            "sexo": paciente.sexo,
            "telefone": paciente.telefone,
            "email": paciente.email,
            "endereco_rua": paciente.endereco_rua,
            "endereco_numero": paciente.endereco_numero,
            "endereco_complemento": paciente.endereco_complemento,
            "endereco_bairro": paciente.endereco_bairro,
            "endereco_cidade": paciente.endereco_cidade,
            "endereco_uf": paciente.endereco_uf,
            "endereco_cep": paciente.endereco_cep,
            "foto_dataurl": _safe_avatar_dataurl(clinica.id, paciente),
        },
        "gerado_por": (usuario.nome or usuario.email),
        "gerado_em": agora_utc(),
        "data_emissao": date.today(),
    }


# ============================================================================
# Endpoints
# ============================================================================

@router.get("", response_model=list[DocumentoGeradoOut])
def listar_documentos_gerados(
    paciente_id: str,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    """Lista PDFs já gerados pra este paciente (via audit_log, recurso='documento_pdf').

    Limit 50. A geração em si é volátil — não persistimos o PDF, só o evento
    de export (LGPD Art. 37). Listagem aqui é para auditoria/UI mostrar
    "documentos emitidos recentemente".
    """
    # Garante que paciente existe nesta clínica (não vaza existência de outras)
    _carregar_paciente(db, paciente_id, clinica.id)

    # Filtra audit_log: clínica + recurso='documento_pdf' + recurso_id=paciente_id.
    # Aproveita que escrevemos recurso_id=paciente_id no audit de export.
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.clinica_id == clinica.id,
            AuditLog.recurso == "documento_pdf",
            AuditLog.recurso_id == paciente_id,
            AuditLog.acao == AcaoAudit.EXPORT,
        )
        .order_by(AuditLog.quando.desc())
        .limit(50)
        .all()
    )

    # Pré-busca nomes de usuários pra não fazer N+1 (set pequeno).
    user_ids = {r.usuario_id for r in rows if r.usuario_id}
    nomes: dict[str, str] = {}
    if user_ids:
        for u in db.query(Usuario.id, Usuario.nome, Usuario.email).filter(
            Usuario.id.in_(user_ids),
            Usuario.clinica_id == clinica.id,
        ).all():
            nomes[u.id] = u.nome or u.email

    out: list[DocumentoGeradoOut] = []
    for r in rows:
        det = r.detalhes or {}
        tipo = det.get("tipo")
        if not tipo:
            continue  # entrada legacy/malformada — ignora
        out.append(DocumentoGeradoOut(
            tipo=tipo,
            gerado_em=r.quando,
            gerado_por_nome=nomes.get(r.usuario_id) if r.usuario_id else None,
            prontuario_id=det.get("prontuario_id"),
        ))
    return out


@router.get("/{tipo}.pdf")
@limiter.limit("10/minute")
def gerar_documento(
    request: Request,
    paciente_id: str,
    tipo: str,
    # Específicos do tipo (todos opcionais — validação cruzada abaixo):
    prontuario_id: Optional[str] = Query(None),
    data: Optional[date] = Query(None),
    hora_inicio: str = Query("08:00", max_length=5),
    hora_fim: str = Query("09:00", max_length=5),
    dias_afastamento: int = Query(1, ge=1, le=365),
    cid_10: Optional[str] = Query(None, max_length=10),
    tipo_atendimento: str = Query("consulta", max_length=40),
    prescricao: Optional[str] = Query(None, max_length=MAX_PRESCRICAO),
    procedimento: Optional[str] = Query(None, min_length=2, max_length=300),
    riscos: Optional[str] = Query(None, max_length=2000),
    cuidados_pos: Optional[str] = Query(None, max_length=2000),
    servico_recibo: Optional[str] = Query(None, max_length=120),
    valor_recibo: Optional[str] = Query(None, max_length=30),
    profissional_recibo: Optional[str] = Query(None, max_length=120),
    hora_recibo: Optional[str] = Query(None, max_length=5),
    observacoes_recibo: Optional[str] = Query(None, max_length=1000),
    clinica: Clinica = Depends(clinica_atual),
    usuario: Usuario = Depends(usuario_atual),
    ctx: dict = Depends(audit_context),
    db: Session = Depends(get_db_dependency),
):
    """Gera um PDF on-demand. Cada chamada vira audit EXPORT (LGPD Art. 18/37)."""
    # 1) Validação do tipo (404 antes de tudo — não vaza nada).
    if tipo not in TIPOS_VALIDOS:
        raise HTTPException(404, "Tipo de documento desconhecido")

    # 2) Gate por especialidade (403 — disponibilidade depende do plano/vertical).
    cfg = config_efetiva(clinica)
    if tipo not in cfg.documentos:
        raise HTTPException(403, "Documento não disponível pra sua especialidade")

    # 3) Paciente da clínica logada (404 cross-tenant — não vaza).
    paciente = _carregar_paciente(db, paciente_id, clinica.id)

    # 4) Monta contexto base (clínica + paciente + metadata + watermark).
    contexto = _contexto_base(clinica, paciente, usuario)

    # 5) Parâmetros específicos por tipo + validações cross-tenant adicionais.
    prontuario_id_audit: Optional[str] = None

    if tipo == "prontuario":
        if not prontuario_id:
            raise HTTPException(422, "prontuario_id é obrigatório pra este tipo")
        pront = db.query(Prontuario).filter(
            Prontuario.id == prontuario_id,
            Prontuario.clinica_id == clinica.id,
            Prontuario.paciente_id == paciente.id,
        ).first()
        if not pront:
            raise HTTPException(404, "Prontuário não encontrado")
        prontuario_id_audit = pront.id

        # Profissional (read-only — só pra colocar nome no PDF).
        profissional_nome: Optional[str] = None
        if pront.profissional_id:
            prof = db.query(Profissional).filter(
                Profissional.id == pront.profissional_id,
                Profissional.clinica_id == clinica.id,
            ).first()
            if prof:
                profissional_nome = prof.nome

        # Fotos do prontuário (cap em MAX_FOTOS_PDF — peso + privacidade).
        fotos_dataurls: list[str] = []
        fotos_meta = pront.fotos or []
        for foto in fotos_meta[:MAX_FOTOS_PDF]:
            key = foto.get("key") if isinstance(foto, dict) else None
            if not key:
                continue
            du = _safe_foto_prontuario_dataurl(clinica.id, pront.id, key)
            if du:
                fotos_dataurls.append(du)

        contexto["prontuario"] = {
            "id": pront.id,
            "anotacoes": pront.anotacoes,
            "procedimentos_realizados": pront.procedimentos_realizados,
            "alergias": pront.alergias or [],
            "proxima_acao": pront.proxima_acao,
            "criado_em": pront.criado_em,
            "atualizado_em": pront.atualizado_em,
        }
        contexto["profissional_nome"] = profissional_nome
        contexto["fotos_dataurls"] = fotos_dataurls

    elif tipo == "atestado":
        contexto.update({
            "data": data or date.today(),
            "hora_inicio": hora_inicio,
            "hora_fim": hora_fim,
            "dias_afastamento": dias_afastamento,
            "cid_10": cid_10,
        })

    elif tipo == "declaracao_comparecimento":
        contexto.update({
            "data": data or date.today(),
            "hora_inicio": hora_inicio,
            "hora_fim": hora_fim,
            "tipo_atendimento": tipo_atendimento,
        })

    elif tipo == "receituario":
        if not prescricao or not prescricao.strip():
            raise HTTPException(422, "prescricao é obrigatória pra receituário")
        contexto["prescricao"] = prescricao

    elif tipo == "termo_consentimento":
        if not procedimento or not procedimento.strip():
            raise HTTPException(422, "procedimento é obrigatório pra termo de consentimento")
        contexto.update({
            "procedimento": procedimento.strip(),
            "riscos": (riscos or "").strip() or None,
            "cuidados_pos": (cuidados_pos or "").strip() or None,
            "data": data or date.today(),
        })

    elif tipo == "recibo":
        from datetime import date as _date
        contexto.update({
            "servico": (servico_recibo or "Atendimento").strip(),
            "profissional_nome": (profissional_recibo or "").strip() or None,
            "data_atendimento": data or _date.today(),
            "hora_atendimento": hora_recibo,
            "valor": (valor_recibo or "").strip() or None,
            "observacoes": (observacoes_recibo or "").strip() or None,
        })

    # 6) Render. WeasyPrint pode falhar em template malformado — surface 500
    #    com mensagem genérica (não vaza stack).
    log.info(
        "documentos: iniciando geração tipo=%s paciente=%s clinica=%s usuario=%s",
        tipo, paciente.id, clinica.id, usuario.id,
    )
    try:
        pdf_bytes = gerar_pdf(tipo, contexto)
    except Exception:
        log.exception(
            "documentos: falha ao gerar PDF tipo=%s paciente=%s clinica=%s",
            tipo, paciente.id, clinica.id,
        )
        raise HTTPException(500, "Falha ao gerar PDF")
    log.info(
        "documentos: PDF gerado tipo=%s paciente=%s tamanho_bytes=%d",
        tipo, paciente.id, len(pdf_bytes),
    )

    # 7) Audit EXPORT — LGPD Art. 18 (rastreabilidade total de saída de dados).
    audit.log(
        db, **ctx,
        acao=AcaoAudit.EXPORT,
        recurso="documento_pdf",
        recurso_id=paciente.id,
        detalhes={
            "tipo": tipo,
            "prontuario_id": prontuario_id_audit,
            "tamanho_bytes": len(pdf_bytes),
        },
    )
    db.commit()

    # 8) Nome do arquivo seguro (espaços viram _, sem caracteres exóticos).
    nome_seguro = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in (paciente.nome or "paciente").replace(" ", "_")
    )[:60] or "paciente"
    filename = f"{tipo}_{nome_seguro}_{date.today().isoformat()}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
