from moviegraphrag.schemas.models import Evidence


def validate_assessments(batch, movies, request):
    expected = {m["movie_id"]: m for m in movies}
    if len(batch.films) != len(expected) or {f.movie_id for f in batch.films} != set(expected):
        raise ValueError("Assessment must cover exactly the supplied movies")
    accepted = []
    for film in batch.films:
        movie = expected[film.movie_id]
        if len(film.judgments) != len(request.themes) or {
            j.theme for j in film.judgments
        } != set(request.themes):
            raise ValueError("Assessment must cover every requested theme exactly once")
        evidence, flags = [], []
        keyword_map = dict(zip(movie.get("keyword_ids", []), movie.get("keywords", []), strict=True))
        for i, j in enumerate(film.judgments):
            has_quote = j.source in {"overview", "both"}
            has_keywords = j.source in {"keyword", "both"}
            if has_quote:
                if not j.overview_quote or not j.overview_quote.strip() or j.overview_quote not in movie["overview"]:
                    raise ValueError("Overview quote is not an exact source excerpt")
                evidence.append(Evidence(id=f"m{film.movie_id}-t{i}-overview", kind="overview",
                                         entity_id=film.movie_id,
                                         data={"theme": j.theme, "quote": j.overview_quote,
                                               "centrality": j.centrality,
                                               "snapshot_id": movie.get("snapshot_id")}))
            elif j.overview_quote:
                raise ValueError("Unexpected overview quotation")
            if has_keywords:
                if not j.keyword_ids or any(k not in keyword_map for k in j.keyword_ids):
                    raise ValueError("Keyword citation not found")
                evidence.append(Evidence(id=f"m{film.movie_id}-t{i}-keyword", kind="keyword",
                                         entity_id=film.movie_id, data={"theme": j.theme,
                                         "keywords": [{"id": k, "name": keyword_map[k]} for k in j.keyword_ids],
                                         "centrality": "unknown", "snapshot_id": movie.get("snapshot_id")}))
            elif j.keyword_ids:
                raise ValueError("Unexpected keyword citations")
            if not has_quote and j.centrality != "unknown":
                raise ValueError("Keywords alone cannot establish centrality")
            valid = j.source != "none" and (request.requirement != "central" or j.centrality == "central")
            flags.append(valid)
        keep = all(flags) if request.operator == "ALL" else any(flags)
        if keep:
            accepted.append((film, evidence))
    return accepted
