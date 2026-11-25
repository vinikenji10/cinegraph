# Backend do CineGraph para o Cloud Run.
# O banco SQLite (somente leitura) e o modelo de embeddings vão dentro da imagem,
# então a única dependência externa em tempo de execução é o Firestore e a API do Claude.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    CINEGRAPH_HOME=/app \
    FASTEMBED_CACHE_PATH=/app/models \
    CINEGRAPH_VECTOR_BACKEND=firestore \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

# requirements.txt é gerado do uv.lock por scripts/deploy.sh (versões travadas).
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src ./src
COPY data/processed/cinegraph.db data/processed/embeddings.npy data/processed/embedding_ids.npy data/processed/index_meta.json ./data/processed/

# Baixa o modelo de embeddings durante o build (evita download a cada partida a frio).
RUN python -c "from cinegraph.retrieval.embedder import embed_query; print(embed_query('aquecimento').shape)"

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn cinegraph.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers"]
