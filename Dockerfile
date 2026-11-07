FROM python:3.12.10-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY cypher ./cypher
RUN pip install --no-cache-dir '.[embeddings]'
ENV HF_HOME=/models
EXPOSE 8000
CMD ["uvicorn", "moviegraphrag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
