"""Tests are deliberately restricted to Docker and real dependencies."""
import os
from pathlib import Path

import httpx
import pytest

from moviegraphrag.config import Settings
from moviegraphrag.embeddings import LocalEmbedder
from moviegraphrag.ingestion.dataset import load_dataset
from moviegraphrag.retrieval.neo4j_store import Neo4jStore


def pytest_sessionstart(session):
    if not Path("/.dockerenv").exists():
        pytest.exit("Tests must run inside Docker: docker compose --profile test run --rm tests", returncode=4)


@pytest.fixture(scope="session")
def settings():
    return Settings()


@pytest.fixture(scope="session")
def store(settings):
    result = Neo4jStore(settings)
    if not result.readiness()["ready"]:
        pytest.fail("Real Neo4j snapshot is not ready. Run docker compose exec api moviegraphrag ingest")
    yield result
    result.close()


@pytest.fixture(scope="session")
def dataset():
    return load_dataset(Path("data/raw/tmdb_5000_movies.csv"), Path("data/raw/tmdb_5000_credits.csv"))


@pytest.fixture(scope="session")
def embedder(settings):
    result = LocalEmbedder(settings.embedding_model, settings.embedding_revision)
    result.load()
    return result


@pytest.fixture(scope="session")
def api():
    with httpx.Client(base_url=os.environ.get("API_BASE_URL", "http://api:8000"), timeout=300) as client:
        assert client.get("/health").status_code == 200, "Real API container must be running"
        yield client


@pytest.fixture
def groq_configured(settings):
    if not settings.groq_api_key.get_secret_value():
        pytest.fail("GROQ_API_KEY is missing. No simulated provider is permitted.")
