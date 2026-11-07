FROM python:3.12.10-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY cypher ./cypher
RUN pip install --no-cache-dir torch==2.7.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir '.[dev,embeddings]' \
    && pip freeze > /app/requirements.lock
ENV HF_HOME=/models PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "moviegraphrag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
