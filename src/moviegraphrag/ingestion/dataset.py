import csv
import hashlib
import json
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def normalize(value: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                            if not unicodedata.combining(c)).split())


def positive_id(value) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"Invalid TMDB ID: {value!r}")
    return int(value)


def read_csv(path: Path, required: set[str], key: str) -> tuple[list[dict], str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f"Missing columns in {path.name}: {required - set(reader.fieldnames or [])}")
        rows = list(reader)
    ids = set()
    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"Malformed CSV row in {path.name}")
        movie_id = positive_id(row[key])
        if movie_id in ids:
            raise ValueError(f"Duplicate movie ID {movie_id}")
        ids.add(movie_id)
    return rows, hashlib.sha256(path.read_bytes()).hexdigest()


def json_array(raw: str, field: str, movie_id: int) -> list[dict]:
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {field} JSON for movie {movie_id}") from exc
    if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
        raise ValueError(f"{field} must be a list of objects for movie {movie_id}")
    return result


@dataclass
class Dataset:
    movies: list[dict]
    people: list[dict]
    genres: list[dict]
    acted_in: list[dict]
    directed: list[dict]
    in_genre: list[dict]
    manifest: dict
    report: dict


def load_dataset(movies_path: Path, credits_path: Path) -> Dataset:
    raw_movies, mh = read_csv(movies_path, {"id", "title", "original_title", "overview", "genres",
                                            "keywords", "release_date", "original_language",
                                            "vote_average", "vote_count"}, "id")
    raw_credits, ch = read_csv(credits_path, {"movie_id", "title", "cast", "crew"}, "movie_id")
    credits = {positive_id(c["movie_id"]): c for c in raw_credits}
    if {positive_id(m["id"]) for m in raw_movies} != set(credits):
        raise ValueError("Movie and credit ID sets must match exactly")
    snapshot_id = hashlib.sha256(f"{mh}:{ch}:canonical-v1".encode()).hexdigest()
    movies, people, genres, acts, dirs, gs = [], {}, {}, {}, {}, set()
    raw_cast_count = 0
    all_people = set()
    names = defaultdict(set)
    cast_counts, director_counts = [], []

    def person(item):
        pid = positive_id(item["id"])
        name = item["name"]
        if not name.strip():
            raise ValueError(f"Empty name for {pid}")
        if pid in people and people[pid]["name"] != name:
            raise ValueError(f"Conflicting names for person {pid}")
        people[pid] = {"person_id": pid, "name": name, "normalized_name": normalize(name),
                       "surname": normalize(name).split()[-1], "aliases": []}
        names[name].add(pid)
        return pid

    for raw in raw_movies:
        mid = positive_id(raw["id"])
        credit = credits[mid]
        if raw["title"] != credit["title"]:
            raise ValueError(f"Title mismatch for {mid}")
        genre_list = json_array(raw["genres"], "genres", mid)
        keywords = json_array(raw["keywords"], "keywords", mid)
        for item in genre_list + keywords:
            positive_id(item["id"])
            if not isinstance(item.get("name"), str):
                raise ValueError(f"Invalid named metadata for {mid}")
        unnamed_keywords = [positive_id(k["id"]) for k in keywords if not k["name"].strip()]
        keywords = [k for k in keywords if k["name"].strip()]
        release = date.fromisoformat(raw["release_date"]) if raw["release_date"] else None
        movie = {"movie_id": mid, "title": raw["title"], "original_title": raw["original_title"],
                 "normalized_title": normalize(raw["title"]),
                 "aliases": sorted({normalize(raw["title"]), normalize(raw["original_title"])}),
                 "overview": raw["overview"].strip(), "release_date": str(release) if release else None,
                 "release_year": release.year if release else None,
                 "original_language": raw["original_language"],
                 "vote_average": float(raw["vote_average"]), "vote_count": int(raw["vote_count"]),
                 "keywords": [k["name"] for k in keywords],
                 "keyword_ids": [positive_id(k["id"]) for k in keywords],
                 "unnamed_keyword_ids": unnamed_keywords,
                 "genre_names": [g["name"] for g in genre_list], "snapshot_id": snapshot_id,
                 "overview_missing": not bool(raw["overview"].strip())}
        movies.append(movie)
        for g in genre_list:
            gid = positive_id(g["id"])
            if gid in genres and genres[gid]["name"] != g["name"]:
                raise ValueError(f"Conflicting genre {gid}")
            genres[gid] = {"genre_id": gid, "name": g["name"],
                           "normalized_name": normalize(g["name"])}
            gs.add((mid, gid))
        cast = json_array(credit["cast"], "cast", mid)
        crew = json_array(credit["crew"], "crew", mid)
        actors, directors = set(), set()
        for item in cast + crew:
            all_people.add(positive_id(item["id"]))
        for item in cast:
            pid = person(item)
            actors.add(pid)
            raw_cast_count += 1
            if not isinstance(item.get("order"), int) or item["order"] < 0:
                raise ValueError(f"Invalid cast order for {mid}/{pid}")
            entry = acts.setdefault((pid, mid), {"person_id": pid, "movie_id": mid,
                                                "credits": [], "snapshot_id": snapshot_id})
            entry["credits"].append({k: item.get(k) for k in ("character", "order", "credit_id", "cast_id")})
        for item in crew:
            if item.get("job") == "Director":
                pid = person(item)
                directors.add(pid)
                entry = dirs.setdefault((pid, mid), {"person_id": pid, "movie_id": mid,
                                                    "credits": [], "snapshot_id": snapshot_id})
                entry["credits"].append({"credit_id": item.get("credit_id"), "job": "Director"})
        cast_counts.append(len(actors))
        director_counts.append(len(directors))
    for entry in acts.values():
        entry["characters"] = sorted({c["character"] for c in entry["credits"] if c["character"]})
        entry["min_order"] = min(c["order"] for c in entry["credits"])
        entry["credits_json"] = json.dumps(entry.pop("credits"), ensure_ascii=False, sort_keys=True)
    for entry in dirs.values():
        entry["credits_json"] = json.dumps(entry.pop("credits"), ensure_ascii=False, sort_keys=True)
    actor_ids = {a[0] for a in acts}
    director_ids = {d[0] for d in dirs}
    lengths = [len(m["overview"].split()) for m in movies if m["overview"]]
    normalized_names = defaultdict(set)
    for p in people.values():
        normalized_names[p["normalized_name"]].add(p["person_id"])
    report = {"movies": len(movies), "people": len(people), "genres": len(genres),
              "acted_in": len(acts), "directed": len(dirs), "in_genre": len(gs),
              "raw_cast_credits": raw_cast_count, "actors": len(actor_ids),
              "directors": len(director_ids), "both_roles": len(actor_ids & director_ids),
              "all_cast_crew_people": len(all_people),
              "ambiguous_names": sum(len(ids) > 1 for ids in names.values()),
              "ambiguous_normalized_names": sum(len(ids) > 1 for ids in normalized_names.values()),
              "no_actors": cast_counts.count(0), "no_director": director_counts.count(0),
              "multiple_directors": sum(n > 1 for n in director_counts),
              "mean_cast": statistics.mean(cast_counts),
              "empty_overview": sum(not m["overview"] for m in movies),
              "short_overview_under_20_words": sum(0 < n < 20 for n in lengths),
              "overview_words": {"mean": statistics.mean(lengths) if lengths else 0,
                                 "median": statistics.median(lengths) if lengths else 0,
                                 "max": max(lengths, default=0)},
              "no_keywords": sum(not m["keywords"] for m in movies),
              "unnamed_keyword_annotations": sum(len(m["unnamed_keyword_ids"]) for m in movies),
              "no_genres": sum(not m["genre_names"] for m in movies),
              "languages": dict(Counter(m["original_language"] for m in movies))}
    manifest = {"snapshot_id": snapshot_id, "movies_sha256": mh, "credits_sha256": ch,
                "canonical_version": "canonical-v1", "counts": {k: report[k] for k in
                    ("movies", "people", "genres", "acted_in", "directed", "in_genre")}}
    return Dataset(movies, list(people.values()), list(genres.values()), list(acts.values()),
                   list(dirs.values()), [{"movie_id": m, "genre_id": g} for m, g in sorted(gs)],
                   manifest, report)
