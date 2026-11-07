from pathlib import Path

import pytest

from moviegraphrag.config import Settings
from moviegraphrag.ingestion.dataset import load_dataset


@pytest.fixture
def settings():
    return Settings(_env_file=None, synthesis_enabled=False)


@pytest.fixture(scope="session")
def dataset():
    root = Path("data/raw")
    movies, credits = root / "tmdb_5000_movies.csv", root / "tmdb_5000_credits.csv"
    if not movies.exists() or not credits.exists():
        pytest.skip("Place the two locally acquired CSVs in data/raw")
    return load_dataset(movies, credits)
