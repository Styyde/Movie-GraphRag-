import argparse
import importlib.metadata
import json
from pathlib import Path

from moviegraphrag.config import Settings
from moviegraphrag.embeddings import LocalEmbedder
from moviegraphrag.ingestion.dataset import load_dataset
from moviegraphrag.ingestion.loader import ingest
from moviegraphrag.retrieval.neo4j_store import Neo4jStore


def main():
    parser = argparse.ArgumentParser(prog="moviegraphrag")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("profile", "ingest", "reference"):
        p = sub.add_parser(command)
        p.add_argument("--movies", type=Path, default=Path("data/raw/tmdb_5000_movies.csv"))
        p.add_argument("--credits", type=Path, default=Path("data/raw/tmdb_5000_credits.csv"))
        p.add_argument("--output", type=Path)
    sub.add_parser("warmup")
    sub.add_parser("versions")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--cases", type=Path, default=Path("evaluation/cases.jsonl"))
    ev.add_argument("--system", choices=["graph", "vector", "flat"], default="graph")
    ev.add_argument("--split", choices=["dev", "test"], default="dev")
    ev.add_argument("--output", type=Path, default=Path("evaluation/reports/run.json"))
    ev.add_argument("--movies", type=Path, default=Path("data/raw/tmdb_5000_movies.csv"))
    ev.add_argument("--credits", type=Path, default=Path("data/raw/tmdb_5000_credits.csv"))
    args = parser.parse_args()
    settings = Settings()
    if args.command == "versions":
        print(json.dumps({d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}, indent=2))
        return
    if args.command == "warmup":
        embedder = LocalEmbedder(settings.embedding_model, settings.embedding_revision)
        print(json.dumps(embedder.metadata(), indent=2))
        return
    if args.command == "evaluate":
        from moviegraphrag.evaluation import evaluate
        result = evaluate(args, settings)
    else:
        dataset = load_dataset(args.movies, args.credits)
        if args.command == "profile":
            result = {"manifest": dataset.manifest, "report": dataset.report}
        elif args.command == "reference":
            from moviegraphrag.evaluation import graph_reference
            result = graph_reference(dataset)
        else:
            store = Neo4jStore(settings)
            try:
                embedder = LocalEmbedder(settings.embedding_model, settings.embedding_revision)
                result = ingest(dataset, store, embedder)
            finally:
                store.close()
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
