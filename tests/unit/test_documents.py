import pytest

from moviegraphrag.ingestion.documents import build_document


class Tokenizer:
    def encode(self, text, **kwargs):
        return text.split()


def test_metadata_only_and_graph_only():
    movie = {"movie_id": 1, "title": "movie", "overview": "", "keywords": [], "genre_names": []}
    assert build_document(movie, Tokenizer()) is None
    movie["keywords"] = ["space"]
    assert build_document(movie, Tokenizer()).coverage == "metadata_only"


def test_remove_keywords_before_rejecting_long_document():
    movie = {"movie_id": 1, "title": "movie", "overview": "short synopsis",
             "keywords": ["one two three four", "five six seven eight"], "genre_names": []}
    doc = build_document(movie, Tokenizer(), max_tokens=10)
    assert doc.keywords_removed > 0
    assert "short synopsis" in doc.text
    with pytest.raises(ValueError):
        build_document(movie, Tokenizer(), max_tokens=2)
