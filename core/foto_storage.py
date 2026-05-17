"""Storage de fotos de prontuário — pipeline Pillow + filesystem isolado.

LGPD Art. 11 (saúde) + Art. 7º §3º (biométrico):
- Strip EXIF (GPS, modelo, timestamp) ANTES de salvar.
- Resize 1920px max + WebP q=80 (economia + normalização).
- Apenas JPEG/PNG aceitos (corta HEIC/WebP — libheif/libwebp tem histórico CVE).
- UUIDv4 no path (não SHA256 — evita fingerprint), SHA256 vai no JSON pra detectar tampering.
- Cap 50M pixels (anti decompression bomb).

Layout: /data/fotos/{clinica_id}/{prontuario_id}/{uuid4}.webp
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

# Anti decompression bomb (default do Pillow é ~89M)
Image.MAX_IMAGE_PIXELS = 50_000_000

FOTOS_BASE_DIR = Path(os.getenv("FOTOS_BASE_DIR", "/data/fotos")).resolve()
AVATARS_BASE_DIR = Path(os.getenv("AVATARS_BASE_DIR", "/data/fotos_paciente")).resolve()
LOGOS_BASE_DIR = Path(os.getenv("LOGOS_BASE_DIR", "/data/logos")).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))   # 10 MB
MAX_FOTOS_POR_PRONTUARIO = int(os.getenv("MAX_FOTOS_POR_PRONTUARIO", 50))
MAX_DIMENSAO = 1920          # fotos de prontuário
MAX_DIMENSAO_AVATAR = 512    # foto do paciente (avatar)
MAX_DIMENSAO_LOGO = 800      # logo da clínica
WEBP_QUALITY = 80

# Tipos aceitos. Pillow.format é "JPEG"/"PNG", não MIME.
FORMATOS_ACEITOS = {"JPEG", "PNG"}
KEY_REGEX = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.webp$")


class FotoError(Exception):
    """Erro de processamento/validação de foto. Mensagem segura pra retornar ao user."""


@dataclass
class FotoMetadata:
    key: str           # uuid4.webp
    sha256: str        # hex digest do arquivo final (WebP)
    mime: str          # sempre "image/webp"
    tamanho_bytes: int


def _safe_path(clinica_id: str, prontuario_id: str, key: str | None = None) -> Path:
    """Resolve path seguro, garantindo que fica dentro de FOTOS_BASE_DIR.

    Defesa em profundidade contra path traversal: ainda que clinica_id/prontuario_id
    venham do JWT (confiáveis), garantimos que o path final não escape via symlink.
    """
    # IDs do banco são UUIDs — sanity check defensivo
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", clinica_id) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", prontuario_id):
        raise FotoError("ID inválido")
    base = FOTOS_BASE_DIR / clinica_id / prontuario_id
    if key:
        if not KEY_REGEX.match(key):
            raise FotoError("Key de foto inválida")
        candidato = (base / key).resolve()
        # garante que candidato fica dentro de FOTOS_BASE_DIR (anti symlink escape)
        try:
            candidato.relative_to(FOTOS_BASE_DIR)
        except ValueError:
            raise FotoError("Path traversal bloqueado")
        return candidato
    return base


def processar_upload(raw_bytes: bytes, dimensao_max: int = MAX_DIMENSAO, quality: int = WEBP_QUALITY) -> tuple[bytes, FotoMetadata]:
    """Recebe bytes do upload, retorna (webp_bytes, metadata) pronto pra salvar.

    `dimensao_max`: 1920 (foto de prontuário), 512 (avatar), 800 (logo).
    Raises FotoError em qualquer falha de validação.
    """
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise FotoError(f"Arquivo maior que {MAX_UPLOAD_BYTES // (1024*1024)}MB")
    if len(raw_bytes) < 100:
        raise FotoError("Arquivo muito pequeno")

    try:
        # 1) verify consome o stream — abrir 2x
        Image.open(BytesIO(raw_bytes)).verify()
        img = Image.open(BytesIO(raw_bytes))
    except (UnidentifiedImageError, OSError):
        raise FotoError("Imagem inválida ou corrompida")
    except Image.DecompressionBombError:
        raise FotoError("Imagem muito grande (decompression bomb)")

    if img.format not in FORMATOS_ACEITOS:
        raise FotoError(f"Formato {img.format} não aceito — use JPEG ou PNG")

    # 2) corrigir orientação ANTES de stripar EXIF (senão fica rotacionada errado)
    img = ImageOps.exif_transpose(img)

    # 3) resize mantendo aspect ratio (não faz upscale)
    img.thumbnail((dimensao_max, dimensao_max), Image.Resampling.LANCZOS)

    # 4) recriar imagem do zero pra eliminar 100% dos metadados residuais
    if img.mode != "RGB":
        img = img.convert("RGB")
    limpa = Image.new("RGB", img.size)
    limpa.paste(img)

    # 5) salvar como WebP
    buf = BytesIO()
    limpa.save(buf, format="WEBP", quality=quality, method=6)
    webp_bytes = buf.getvalue()

    key = f"{uuid.uuid4()}.webp"
    sha256 = hashlib.sha256(webp_bytes).hexdigest()
    return webp_bytes, FotoMetadata(
        key=key,
        sha256=sha256,
        mime="image/webp",
        tamanho_bytes=len(webp_bytes),
    )


# ============================================================================
# Storage simples (1 arquivo por entidade — avatar do paciente, logo da clínica)
# ============================================================================

def _safe_simple_path(base_dir: Path, clinica_id: str, entity_id: str) -> Path:
    """Path /base/{clinica_id}/{entity_id}.webp com regex de sanity."""
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", clinica_id) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", entity_id):
        raise FotoError("ID inválido")
    pasta = base_dir / clinica_id
    destino = (pasta / f"{entity_id}.webp").resolve()
    try:
        destino.relative_to(base_dir)
    except ValueError:
        raise FotoError("Path traversal bloqueado")
    return destino


def salvar_avatar(clinica_id: str, paciente_id: str, raw_bytes: bytes) -> FotoMetadata:
    """Processa + salva foto do paciente (1 por paciente, sobrescreve)."""
    webp, meta = processar_upload(raw_bytes, dimensao_max=MAX_DIMENSAO_AVATAR, quality=85)
    path = _safe_simple_path(AVATARS_BASE_DIR, clinica_id, paciente_id)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_suffix(".webp.tmp")
    tmp.write_bytes(webp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    # key armazenada no DB é só "{paciente_id}.webp" — base_dir é fixo
    return FotoMetadata(
        key=f"{paciente_id}.webp",
        sha256=meta.sha256,
        mime="image/webp",
        tamanho_bytes=meta.tamanho_bytes,
    )


def ler_avatar(clinica_id: str, paciente_id: str) -> bytes:
    path = _safe_simple_path(AVATARS_BASE_DIR, clinica_id, paciente_id)
    if not path.is_file():
        raise FotoError("Foto não encontrada")
    return path.read_bytes()


def deletar_avatar(clinica_id: str, paciente_id: str) -> bool:
    path = _safe_simple_path(AVATARS_BASE_DIR, clinica_id, paciente_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def salvar_logo(clinica_id: str, raw_bytes: bytes) -> FotoMetadata:
    """Logo da clínica — usado em cabeçalho de PDFs."""
    webp, meta = processar_upload(raw_bytes, dimensao_max=MAX_DIMENSAO_LOGO, quality=85)
    LOGOS_BASE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    destino = (LOGOS_BASE_DIR / f"{clinica_id}.webp").resolve()
    try:
        destino.relative_to(LOGOS_BASE_DIR)
    except ValueError:
        raise FotoError("Path traversal bloqueado")
    tmp = destino.with_suffix(".webp.tmp")
    tmp.write_bytes(webp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, destino)
    return FotoMetadata(
        key=f"{clinica_id}.webp",
        sha256=meta.sha256,
        mime="image/webp",
        tamanho_bytes=meta.tamanho_bytes,
    )


def ler_logo(clinica_id: str) -> bytes | None:
    destino = (LOGOS_BASE_DIR / f"{clinica_id}.webp").resolve()
    try:
        destino.relative_to(LOGOS_BASE_DIR)
    except ValueError:
        return None
    if not destino.is_file():
        return None
    return destino.read_bytes()


def deletar_logo(clinica_id: str) -> bool:
    destino = (LOGOS_BASE_DIR / f"{clinica_id}.webp").resolve()
    try:
        destino.relative_to(LOGOS_BASE_DIR)
        destino.unlink()
        return True
    except (ValueError, FileNotFoundError):
        return False


def salvar(clinica_id: str, prontuario_id: str, webp_bytes: bytes, meta: FotoMetadata) -> Path:
    """Persiste no FS. Cria diretórios com mode 0700."""
    pasta = _safe_path(clinica_id, prontuario_id)
    pasta.mkdir(parents=True, mode=0o700, exist_ok=True)
    destino = pasta / meta.key
    # write atômico: tmp + rename
    tmp = pasta / (meta.key + ".tmp")
    tmp.write_bytes(webp_bytes)
    os.chmod(tmp, 0o600)
    os.replace(tmp, destino)
    return destino


def ler(clinica_id: str, prontuario_id: str, key: str) -> bytes:
    """Lê bytes do FS. Path sanitizado por _safe_path."""
    path = _safe_path(clinica_id, prontuario_id, key)
    if not path.is_file():
        raise FotoError("Foto não encontrada")
    return path.read_bytes()


def deletar(clinica_id: str, prontuario_id: str, key: str) -> bool:
    """Remove arquivo. Retorna True se removeu, False se não existia."""
    path = _safe_path(clinica_id, prontuario_id, key)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def validar_integridade(clinica_id: str, prontuario_id: str, key: str, sha256_esperado: str) -> bool:
    """Confere SHA256 do arquivo no FS vs o registrado no JSON. Útil pra detectar tampering."""
    try:
        bytes_atuais = ler(clinica_id, prontuario_id, key)
    except FotoError:
        return False
    return hashlib.sha256(bytes_atuais).hexdigest() == sha256_esperado
