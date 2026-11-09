"""Check source integrity without asking a model to judge thematic relevance."""
import hashlib

from moviegraphrag.schemas.models import Evidence


def document_evidence(movie: dict | None, movie_id: int, manifest: dict) -> Evidence | None:
    if not movie or movie.get("movie_id") != movie_id:
        raise RuntimeError("Ranked movie is missing or has a different identity")
    if movie.get("snapshot_id") != manifest["snapshot_id"]:
        raise RuntimeError("Document belongs to another snapshot")
    indexed = manifest["embedding"]
    if (movie.get("embedding_model") != indexed["model"]
            or movie.get("embedding_revision") != indexed["revision"]):
        raise RuntimeError("Document embedding version differs from the snapshot")
    text = movie.get("document_text")
    if not text or hashlib.sha256(text.encode()).hexdigest() != movie.get("document_hash"):
        raise RuntimeError("Document text or hash is invalid")
    overview = movie.get("overview", "")
    names, ids = movie.get("keywords", []), movie.get("keyword_ids", [])
    if len(names) != len(ids):
        raise RuntimeError("Keyword source IDs do not match the names")
    if not overview.strip() and not names:
        return None
    return Evidence(id=f"d{movie_id}", kind="document", entity_id=movie_id, data={
        "movie_id": movie_id, "title": movie["title"], "overview": overview,
        "keywords": [{"id": key, "name": name} for key, name in zip(ids, names, strict=True)],
        "genre_names": movie.get("genre_names", []), "text_coverage": movie.get("text_coverage"),
        "document_hash": movie["document_hash"], "document_version": movie.get("document_version"),
        "snapshot_id": movie["snapshot_id"], "thematic_verification": "not_performed",
    })
