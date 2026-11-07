import importlib.metadata
import json
import logging
import platform
from dataclasses import asdict
from datetime import datetime, timezone

from moviegraphrag.ingestion.documents import build_document

logger = logging.getLogger(__name__)


def chunks(rows, size=500):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def ingest(dataset, store, embedder):
    store.setup()
    current = store.manifest()
    if current and current["snapshot_id"] != dataset.manifest["snapshot_id"]:
        raise ValueError("Different snapshot: use a fresh dedicated Neo4j volume; implicit deletion is disabled")
    if not current and store.read("MATCH (n:Movie) RETURN count(n) AS n")[0]["n"]:
        raise ValueError("Unmanaged Movie nodes exist: use a fresh dedicated database")
    manifest = {**dataset.manifest, "status": "loading",
                "created_at": datetime.now(timezone.utc).isoformat(), "document_variant": "enriched",
                "python": platform.python_version(),
                "dependencies": {n: importlib.metadata.version(n) for n in (
                    "neo4j", "pydantic", "fastapi", "sentence-transformers", "transformers", "torch")}}
    def save():
        store.write("MERGE (c:Corpus {name:'tmdb'}) SET c.manifest=$manifest",
                    {"manifest": json.dumps(manifest)})
    save()
    for label, rows, key in (("Movie", dataset.movies, "movie_id"),
                             ("Person", dataset.people, "person_id"),
                             ("Genre", dataset.genres, "genre_id")):
        for batch in chunks(rows):
            store.write(f"UNWIND $rows AS row MERGE (n:{label} {{{key}:row.{key}}}) SET n += row",
                        {"rows": batch})
    for relation, rows, start_label, start_key, end_label, end_key in (
        ("ACTED_IN", dataset.acted_in, "Person", "person_id", "Movie", "movie_id"),
        ("DIRECTED", dataset.directed, "Person", "person_id", "Movie", "movie_id"),
        ("IN_GENRE", dataset.in_genre, "Movie", "movie_id", "Genre", "genre_id"),
    ):
        for batch in chunks(rows):
            store.write(f"UNWIND $rows AS row MATCH (a:{start_label} {{{start_key}:row.{start_key}}}) "
                        f"MATCH (b:{end_label} {{{end_key}:row.{end_key}}}) "
                        f"MERGE (a)-[r:{relation}]->(b) SET r = row", {"rows": batch})
    metadata = embedder.metadata()
    if metadata["dimensions"] != 384:
        raise ValueError("This schema requires 384-dimensional embeddings")
    manifest["embedding"] = metadata
    documents = []
    for movie in dataset.movies:
        document = build_document(movie, embedder.load().tokenizer,
                                  max_tokens=metadata["max_tokens"], prefix=metadata["prefix"])
        if document:
            documents.append(document)
    existing = {r["id"]: r for r in store.read(
        "MATCH (m:Movie) RETURN m.movie_id AS id, m.document_hash AS hash, "
        "m.embedding_model AS model, m.embedding_revision AS revision, "
        "m.document_version AS version, size(m.embedding) AS dimensions")}
    pending = [d for d in documents if not (
        (old := existing.get(d.movie_id)) and old["hash"] == d.text_hash
        and old["model"] == metadata["model"] and old["revision"] == metadata["revision"]
        and old["version"] == d.document_version and old["dimensions"] == metadata["dimensions"])]
    logger.info("embedding documents=%s reused=%s pending=%s", len(documents),
                len(documents) - len(pending), len(pending))
    for i, batch in enumerate(chunks(pending, 32)):
        vectors = embedder.encode_passages([d.text for d in batch])
        rows = [{**asdict(d), "embedding": v} for d, v in zip(batch, vectors, strict=True)]
        store.write("UNWIND $rows AS row MATCH (m:Movie {movie_id:row.movie_id}) "
                    "SET m.embedding=row.embedding,m.embedding_model=$model,m.embedding_revision=$revision,"
                    "m.document_text=row.text,m.document_hash=row.text_hash,"
                    "m.document_version=row.document_version,m.text_coverage=row.coverage,"
                    "m.token_count=row.token_count,m.keywords_removed=row.keywords_removed",
                    {"rows": rows, "model": metadata["model"], "revision": metadata["revision"]})
        if i % 8 == 0:
            logger.info("encoded %s/%s pending documents", min((i + 1) * 32, len(pending)), len(pending))
    indexed_ids = {d.movie_id for d in documents}
    store.write("MATCH (m:Movie) WHERE NOT m.movie_id IN $ids REMOVE m.embedding, m.document_text, "
                "m.document_hash, m.document_version, m.embedding_model, m.embedding_revision "
                "SET m.text_coverage='none'", {"ids": sorted(indexed_ids)})
    store.write("CALL db.awaitIndexes(300)")
    actual = {}
    for label, key in (("Movie", "movies"), ("Person", "people"), ("Genre", "genres")):
        actual[key] = store.read(f"MATCH (n:{label}) RETURN count(n) AS n")[0]["n"]
    for rel, key in (("ACTED_IN", "acted_in"), ("DIRECTED", "directed"), ("IN_GENRE", "in_genre")):
        actual[key] = store.read(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n")[0]["n"]
    if actual != dataset.manifest["counts"]:
        raise ValueError(f"Snapshot count mismatch: {actual}")
    actual_embeddings = store.read("MATCH (m:Movie) WHERE m.embedding IS NOT NULL RETURN count(m) AS n")[0]["n"]
    if actual_embeddings != len(documents):
        raise ValueError("Embedding coverage mismatch")
    manifest.update(status="ready", embedded_documents=len(documents),
                    reused_embeddings=len(documents) - len(pending),
                    metadata_only=sum(d.coverage == "metadata_only" for d in documents),
                    trimmed_documents=sum(d.keywords_removed > 0 for d in documents))
    save()
    return manifest
