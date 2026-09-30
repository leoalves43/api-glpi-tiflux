FROM python:3.13-slim

# tzdata: log() usa datetime.now(); sem ele o TZ do compose é ignorado.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY glpi_tiflux.py ./
COPY sync/ sync/
COPY tests/ tests/
COPY docker/loop_sincronizacao.sh /usr/local/bin/loop_sincronizacao.sh
RUN chmod +x /usr/local/bin/loop_sincronizacao.sh \
    && useradd --create-home sincronizador
USER sincronizador

# .env NÃO entra na imagem — o compose monta em /app/.env (somente leitura).
CMD ["loop_sincronizacao.sh"]
