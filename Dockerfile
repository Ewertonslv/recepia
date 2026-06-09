FROM python:3.12-slim

WORKDIR /app

# Dependências de sistema:
# - psycopg2: libpq-dev + gcc
# - WeasyPrint (PDF): pango, cairo, gdk-pixbuf, fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        fonts-dejavu-core \
        fonts-liberation \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Python deps primeiro (cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY . .

# Usuário não-root + diretório de fotos do prontuário (Sprint 1 D4).
# .keep garante que /data/fotos tem conteúdo na imagem, então quando Docker monta
# o named volume vazio pela primeira vez, ele copia o owner/perms da imagem.
RUN useradd --create-home --shell /bin/bash recepia \
    && chown -R recepia:recepia /app \
    && mkdir -p /data/fotos /data/fotos_paciente /data/logos \
    && touch /data/fotos/.keep /data/fotos_paciente/.keep /data/logos/.keep \
    && chown -R recepia:recepia /data
USER recepia

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
