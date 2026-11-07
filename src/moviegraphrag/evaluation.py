"""Independent CSV truth, retrieval metrics, and three reproducible baseline entrypoints."""
import csv
import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path


JUDGED_STATUSES = {"csv_verified", "human_reviewed"}
JUDGMENT_STATUSES = JUDGED_STATUSES | {"pending_human_review"}
ANSWER_STATUSES = {"ok", "needs_clarification", "entity_not_found", "no_match_in_dataset",
                   "insufficient_evidence", "unsupported", "dependency_error"}


def validate_cases(cases, snapshot_id=None):
    """Reject stale gold and split leakage before any paid inference begins."""
    seen, families = set(), {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Evaluation cases must be JSON objects")
        case_id, family = case.get("id"), case.get("family")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"Missing or duplicate case ID: {case_id}")
        seen.add(case_id)
        if not isinstance(family, str) or not family or case.get("split") not in {"dev", "test"}:
            raise ValueError(f"Invalid family or split for {case_id}")
        if (case.get("category") not in {"graph", "vector", "hybrid", "robustness"}
                or case.get("answer_type") not in {"movie_list", "person_list"}
                or not isinstance(case.get("question"), str) or len(case["question"]) < 3):
            raise ValueError(f"Invalid case metadata for {case_id}")
        if family in families and families[family] != case["split"]:
            raise ValueError(f"Question family crosses dev/test splits: {family}")
        families[family] = case["split"]
        status = case.get("judgment_status", "pending_human_review")
        if status not in JUDGMENT_STATUSES:
            raise ValueError(f"Unknown judgment_status for {case_id}: {status}")
        relevance = case.get("relevance", {})
        if not isinstance(relevance, dict):
            raise ValueError(f"Invalid relevance mapping for {case_id}")
        for entity_id, grade in relevance.items():
            if (not str(entity_id).isdigit() or int(entity_id) <= 0
                    or str(int(entity_id)) != str(entity_id)
                    or isinstance(grade, bool) or not isinstance(grade, (int, float))
                    or not math.isfinite(grade) or not 0 <= grade <= 3):
                raise ValueError(f"Invalid relevance judgment for {case_id}")
        if relevance and status not in JUDGED_STATUSES:
            raise ValueError(f"Unreviewed relevance judgments for {case_id}")
        if not isinstance(case.get("exhaustive", False), bool):
            raise ValueError(f"Invalid exhaustive flag for {case_id}")
        if status in JUDGED_STATUSES:
            if not case.get("snapshot_id"):
                raise ValueError(f"Labeled case requires snapshot_id: {case_id}")
        if case.get("snapshot_id") and snapshot_id is not None and case["snapshot_id"] != snapshot_id:
            raise ValueError(f"Gold snapshot differs from corpus for {case_id}")
        if case.get("expected_status") is not None and case["expected_status"] not in ANSWER_STATUSES:
            raise ValueError(f"Invalid expected_status for {case_id}")
    return cases


def preserve_annotations(generated, previous):
    """Merge by stable ID without silently deleting or reassigning human judgments."""
    validate_cases(previous)
    old = {case["id"]: case for case in previous}
    if set(old) - {case["id"] for case in generated}:
        raise ValueError("Generation would remove existing case IDs; migrate annotations explicitly")
    result = []
    generated_fields = {"id", "question", "category", "answer_type", "judgment_status", "relevance",
                        "exhaustive", "snapshot_id", "expected_status", "expected_status_source"}
    annotation_fields = {"judgment_status", "relevance", "exhaustive", "snapshot_id",
                         "expected_status", "expected_status_source"}
    for case in generated:
        case = dict(case)
        prior = old.get(case["id"])
        if prior:
            if prior["question"] != case["question"]:
                raise ValueError(f"Question changed under a stable case ID: {case['id']}")
            # Family/split assignments and all annotation extension fields remain intact.
            case.update({key: value for key, value in prior.items() if key not in generated_fields})
            if prior.get("judgment_status") == "human_reviewed" or (
                    prior.get("judgment_status") != "csv_verified" and prior.get("relevance")):
                case.update({key: prior[key] for key in annotation_fields if key in prior})
        result.append(case)
    return result


def graph_suite_reference(movies_path: Path, credits_path: Path):
    """Raw-CSV oracle, intentionally independent of the ingestion normalizer and Cypher."""
    def rows(path, key):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
        result = {int(row[key]): row for row in records}
        if len(records) != len(result):
            raise ValueError(f"Duplicate IDs in reference source {path.name}")
        return result

    movies, credits = rows(movies_path, "id"), rows(credits_path, "movie_id")
    if movies.keys() != credits.keys():
        raise ValueError("Reference source film IDs do not match")
    mh = hashlib.sha256(movies_path.read_bytes()).hexdigest()
    ch = hashlib.sha256(credits_path.read_bytes()).hexdigest()
    snapshot_id = hashlib.sha256(f"{mh}:{ch}:canonical-v1".encode()).hexdigest()
    cast, directed, names = defaultdict(set), defaultdict(set), defaultdict(set)
    genres = defaultdict(set)
    for mid, movie in movies.items():
        for genre in json.loads(movie["genres"]):
            genres[genre["name"]].add(mid)
        credit = credits[mid]
        for person in json.loads(credit["cast"]):
            pid = int(person["id"])
            cast[pid].add(mid)
            names[person["name"]].add(pid)
        for person in json.loads(credit["crew"]):
            if person.get("job") == "Director":
                pid = int(person["id"])
                directed[pid].add(mid)
                names[person["name"]].add(pid)

    class UnresolvedAnchor(Exception):
        def __init__(self, status):
            self.status = status

    def anchor(name, role):
        candidates = names[name] & set(cast if role == "actor" else directed)
        if len(candidates) != 1:
            raise UnresolvedAnchor("needs_clarification" if candidates else "entity_not_found")
        return next(iter(candidates))

    def acting(name):
        return cast[anchor(name, "actor")]

    def directing(name):
        return directed[anchor(name, "director")]

    def collaborators(name):
        films = acting(name)
        return {pid for pid, works in directed.items() if works & films}

    def actors(name):
        films = directing(name)
        return {pid for pid, works in cast.items() if works & films}

    queries = [
        lambda: directing("Christopher Nolan") & acting("Cillian Murphy"),
        lambda: collaborators("Leonardo DiCaprio"),
        lambda: actors("Christopher Nolan"),
        lambda: collaborators("Leonardo DiCaprio") & collaborators("Cillian Murphy"),
        lambda: directing("Steven Spielberg") & acting("Tom Hanks"),
        lambda: directing("Ridley Scott") & acting("Matt Damon"),
        lambda: collaborators("Kate Winslet"),
        lambda: actors("James Cameron"),
        lambda: acting("Brad Pitt") & acting("George Clooney"),
        lambda: acting("Matt Damon") & acting("Ben Affleck"),
        lambda: {mid for mid in directing("Martin Scorsese")
                 if movies[mid]["release_date"] and int(movies[mid]["release_date"][:4]) > 2000},
        lambda: {mid for mid in directing("Quentin Tarantino")
                 if movies[mid]["release_date"] and int(movies[mid]["release_date"][:4]) < 2000},
        lambda: genres["Science Fiction"] & acting("Will Smith"),
        lambda: genres["Animation"] & acting("Tom Hanks"),
        lambda: collaborators("Morgan Freeman"),
        lambda: directing("Tim Burton") & acting("Johnny Depp"),
        lambda: directing("Peter Jackson") & acting("Ian McKellen"),
        lambda: directing("David Fincher") & acting("Brad Pitt"),
        lambda: genres["Comedy"] & acting("Jim Carrey"),
        lambda: directing("Robert Zemeckis") & acting("Tom Hanks"),
    ]
    result = {}
    for i, query in enumerate(queries, 1):
        case_id = f"graph-{i:02}"
        try:
            ids = query()
        except UnresolvedAnchor as exc:
            result[case_id] = {"relevance": {}, "exhaustive": False,
                               "judgment_status": "pending_human_review", "snapshot_id": snapshot_id,
                               "expected_status": exc.status, "expected_status_source": "raw_csv_anchor_lookup"}
        else:
            result[case_id] = {"relevance": {str(pid): 1 for pid in sorted(ids)}, "exhaustive": True,
                               "judgment_status": "csv_verified", "snapshot_id": snapshot_id,
                               "expected_status": "ok" if ids else "no_match_in_dataset",
                               "expected_status_source": "raw_csv_set_oracle"}
    return result


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


def graph_set_metrics(store, response, case):
    """Check the complete real retriever without paying for another analysis per page.

    This is a retrieval-set metric, not a claim that every HTTP pagination page was tested.
    The public first page is separately checked against that complete set.
    """
    from moviegraphrag.schemas.models import QuestionDraft

    if not (case["category"] == "graph" and case.get("exhaustive")
            and case.get("judgment_status") in JUDGED_STATUSES
            and response.strategy == "GRAPH"
            and response.status in {"ok", "no_match_in_dataset"}):
        return None
    plan = QuestionDraft.model_validate(response.trace["plan"])
    bindings = response.trace["bindings"]
    started = time.perf_counter()
    rows, _ = store.graph_candidates(plan, bindings)
    retrieval_ms = (time.perf_counter() - started) * 1000
    actual = {row["id"] for row in rows}
    expected = {int(entity_id) for entity_id, grade in case.get("relevance", {}).items() if grade > 0}
    page_consistent = ([r["id"] for r in response.results]
                       == [r["id"] for r in rows[:plan.limit]])
    page_consistent = page_consistent and response.coverage.total_candidates == len(rows)
    return {"metric_scope": "complete_graph_retrieval", "exact_set_match": actual == expected,
            "precision": len(actual & expected) / len(actual) if actual else float(not expected),
            "recall": len(actual & expected) / len(expected) if expected else float(not actual),
            "expected_count": len(expected), "actual_count": len(actual),
            "missing_ids": sorted(expected - actual), "unexpected_ids": sorted(actual - expected),
            "first_page_consistent": page_consistent, "retrieval_extra_latency_ms": retrieval_ms}


def evaluate(args, settings):
    from moviegraphrag.embeddings import LocalEmbedder
    from moviegraphrag.ingestion.dataset import load_dataset
    from moviegraphrag.llm.groq import GroqLLM
    from moviegraphrag.orchestration import MovieGraphRAG
    from moviegraphrag.retrieval.neo4j_store import Neo4jStore
    from moviegraphrag.schemas.models import QuestionRequest
    all_cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_cases(all_cases)
    store, llm = Neo4jStore(settings), GroqLLM(settings)
    embedder = LocalEmbedder(settings.embedding_model, settings.embedding_revision)
    service = MovieGraphRAG(store, embedder, llm, settings)
    documents, vectors = [], []
    try:
        manifest = store.manifest()
        if not manifest or manifest.get("status") != "ready":
            raise ValueError("A ready real corpus is required for evaluation")
        validate_cases(all_cases, manifest["snapshot_id"])
        cases = [c for c in all_cases if c["split"] == args.split]
        if not cases:
            raise ValueError(f"No evaluation cases for split {args.split}")
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
            complete_set = None
            if args.system == "graph":
                response = service.ask(QuestionRequest(question=case["question"]))
                ids, status, trace = [r["id"] for r in response.results], response.status, response.trace
                errors = [w for w in response.warnings]
                latency_ms = (time.perf_counter() - started) * 1000
                complete_set = graph_set_metrics(store, response, case)
                algorithm = {"GRAPH": "exact_graph_constraints", "VECTOR": "neo4j_approximate_vector_index",
                             "HYBRID_GRAPH_FIRST": "exact_graph_constraints_and_exact_candidate_cosine"}.get(
                                 response.strategy, "no_retrieval")
                coverage = response.coverage.model_dump()
            else:
                query = embedder.encode_query(case["question"])
                scores = defaultdict(lambda: -float("inf"))
                for (mid, _), vector in zip(documents, vectors, strict=True):
                    scores[mid] = max(scores[mid], sum(a * b for a, b in zip(query, vector, strict=True)))
                ids = sorted(scores, key=lambda mid: (-scores[mid], mid))[:5]
                status, trace, errors = "ok", {}, []
                latency_ms = (time.perf_counter() - started) * 1000
                algorithm = "exact_cosine_all_documents_max_pool_by_movie"
                coverage = {"documents": len(documents), "movies": len(scores), "approximate": False}
            relevance = case.get("relevance", {})
            comparable = args.system == "graph" or case.get("answer_type") != "person_list"
            judged = case.get("judgment_status") in JUDGED_STATUSES
            score = metrics(ids, relevance, exhaustive=case.get("exhaustive", False)) if (
                relevance and judged and comparable) else None
            rows.append({"case_id": case["id"], "status": status, "ids": ids, "metrics": score,
                         "judgment_status": case.get("judgment_status", "pending_human_review"),
                         "expected_status": case.get("expected_status"),
                         "expected_status_source": case.get("expected_status_source"),
                         "status_match": status == case["expected_status"] if (
                             args.system == "graph" and case.get("expected_status")) else None,
                         "latency_ms": latency_ms, "graph_set_metrics": complete_set,
                         "retrieval_algorithm": algorithm, "coverage": coverage,
                         "trace": trace, "warnings": errors,
                         "metrics_comparable": comparable})
        times = sorted(r["latency_ms"] for r in rows)
        return {"system": args.system,
                "scope": "end_to_end_pipeline" if args.system == "graph" else "retrieval_only",
                "latency_scope": "analysis_linking_retrieval_evidence_and_generation_first_page" if (
                    args.system == "graph") else "query_embedding_and_exact_retrieval_excluding_document_encoding",
                "latency_comparable_across_systems": False,
                "quality_note": "Generation quality and thematic implication require human review. "
                                "Top-5 metrics do not measure complete graph-set correctness.",
                "judged_cases": sum(r["metrics"] is not None for r in rows),
                "unjudged_cases": sum(c.get("judgment_status") not in JUDGED_STATUSES for c in cases),
                "snapshot": manifest, "embedding": manifest.get("embedding"),
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "split": args.split, "results": rows,
                "latency_p50_ms": statistics.median(times) if times else None,
                "latency_p95_ms": times[max(0, math.ceil(len(times) * .95) - 1)] if times else None}
    finally:
        llm.close()
        store.close()
