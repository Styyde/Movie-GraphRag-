"""Independent CSV truth, retrieval metrics, and three reproducible baseline entrypoints."""
import hashlib
import json
import math
import statistics
import time
from collections import defaultdict


def graph_reference(dataset):
    cast, directed = defaultdict(set), defaultdict(set)
    for row in dataset.acted_in:
        cast[row["person_id"]].add(row["movie_id"])
    for row in dataset.directed:
        directed[row["person_id"]].add(row["movie_id"])
    names = defaultdict(list)
    for p in dataset.people:
        names[p["name"]].append(p["person_id"])
    def unique(name):
        ids = names[name]
        if len(ids) != 1:
            raise ValueError(f"Reference anchor is ambiguous: {name}")
        return ids[0]
    nolan, murphy, leo, damon = map(unique, ["Christopher Nolan", "Cillian Murphy", "Leonardo DiCaprio", "Matt Damon"])
    def collaborators(actor):
        return {d for d, movies in directed.items() if movies & cast[actor]}
    lc, mc, dc = collaborators(leo), collaborators(murphy), collaborators(damon)
    science_fiction = {m["movie_id"] for m in dataset.movies if "Science Fiction" in m["genre_names"]}
    return {"snapshot_id": dataset.manifest["snapshot_id"],
            "Q1": sorted(directed[nolan] & cast[murphy]), "Q2": sorted(lc),
            "Q3": sorted(a for a, movies in cast.items() if movies & directed[nolan]),
            "Q4": sorted(lc & mc), "Q8": sorted(directed[nolan] & cast[leo]),
            "Q9": sorted(science_fiction & set().union(*(directed[d] for d in lc))),
            "Q10": sorted(set().union(*(directed[d] for d in dc)))}


def metrics(ids, relevance, k=5, exhaustive=False):
    ranked = list(dict.fromkeys(ids))[:k]
    grades = [relevance.get(str(i), relevance.get(i, 0)) for i in ranked]
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    relevant = sum(g > 0 for g in relevance.values())
    return {"precision_at_5": sum(g > 0 for g in grades) / k,
            "ndcg_at_5": dcg / idcg if idcg else None,
            "recall_at_5": sum(g > 0 for g in grades) / relevant if exhaustive and relevant else None}


def evaluate(args, settings):
    from moviegraphrag.embeddings import LocalEmbedder
    from moviegraphrag.ingestion.dataset import load_dataset
    from moviegraphrag.llm.mistral import MistralLLM
    from moviegraphrag.orchestration import MovieGraphRAG
    from moviegraphrag.retrieval.neo4j_store import Neo4jStore
    from moviegraphrag.schemas.models import QuestionRequest
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [c for c in cases if c["split"] == args.split]
    store, llm = Neo4jStore(settings), MistralLLM(settings)
    embedder = LocalEmbedder(settings.embedding_model, settings.embedding_revision)
    service = MovieGraphRAG(store, embedder, llm, settings)
    manifest = store.manifest()
    documents, vectors = [], []
    try:
        if args.system != "graph":
            from moviegraphrag.ingestion.documents import build_document
            data = load_dataset(args.movies, args.credits)
            if not manifest or manifest["snapshot_id"] != data.manifest["snapshot_id"]:
                raise ValueError("Baseline and graph snapshots must match")
            model = embedder.load()
            credits = defaultdict(list)
            people = {p["person_id"]: p["name"] for p in data.people}
            if args.system == "flat":
                for rel, rows in (("actor", data.acted_in), ("director", data.directed)):
                    for row in rows:
                        credits[row["movie_id"]].append(f"{rel}: {people[row['person_id']]}")
            for movie in data.movies:
                document = build_document(movie, model.tokenizer, model.max_seq_length,
                                          prefix="passage: " if embedder.is_e5 else "")
                if document:
                    text = document.text
                    if args.system == "flat":
                        # Chunk the baseline's long credit list rather than silently dropping cast facts.
                        for item in credits[movie["movie_id"]]:
                            candidate = text + "\n" + item
                            if len(model.tokenizer.encode("passage: " + candidate)) > model.max_seq_length:
                                documents.append((movie["movie_id"], text))
                                text = f"Title: {movie['title']}\n{item}"
                            else:
                                text = candidate
                    documents.append((movie["movie_id"], text))
            vectors = embedder.encode_passages([text for _, text in documents])
        rows = []
        for case in cases:
            started = time.perf_counter()
            if args.system == "graph":
                response = service.ask(QuestionRequest(question=case["question"]))
                ids, status, trace = [r["id"] for r in response.results], response.status, response.trace
                errors = [w for w in response.warnings]
            else:
                query = embedder.encode_query(case["question"])
                scores = defaultdict(lambda: -float("inf"))
                for (mid, _), vector in zip(documents, vectors, strict=True):
                    scores[mid] = max(scores[mid], sum(a * b for a, b in zip(query, vector, strict=True)))
                ids = sorted(scores, key=lambda mid: (-scores[mid], mid))[:5]
                status, trace, errors = "ok", {}, []
            relevance = case.get("relevance", {})
            comparable = args.system == "graph" or case.get("answer_type") != "person_list"
            score = metrics(ids, relevance, exhaustive=case.get("exhaustive", False)) if relevance and comparable else None
            rows.append({"case_id": case["id"], "status": status, "ids": ids, "metrics": score,
                         "judgment_status": case.get("judgment_status", "pending_human_review"),
                         "latency_ms": (time.perf_counter() - started) * 1000,
                         "trace": trace, "warnings": errors,
                         "metrics_comparable": comparable})
        times = sorted(r["latency_ms"] for r in rows)
        return {"system": args.system, "scope": "retrieval; generation quality requires human review",
                "snapshot": manifest, "embedding": embedder.metadata() if args.system != "graph" else None,
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "split": args.split, "results": rows,
                "latency_p50_ms": statistics.median(times) if times else None,
                "latency_p95_ms": times[max(0, math.ceil(len(times) * .95) - 1)] if times else None}
    finally:
        llm.close()
        store.close()
