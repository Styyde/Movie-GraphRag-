from moviegraphrag.ingestion.dataset import normalize
from moviegraphrag.schemas.models import Label, LinkResult, QuestionDraft

GENRE_ALIASES = {
    "science-fiction": "science fiction", "science fiction": "science fiction",
    "comedie": "comedy", "drame": "drama", "horreur": "horror", "aventure": "adventure",
    "fantastique": "fantasy", "documentaire": "documentary", "policier": "crime",
    "romantique": "romance", "guerre": "war", "historique": "history", "famille": "family",
    "musique": "music", "mystere": "mystery", "animation": "animation", "action": "action",
    "thriller": "thriller", "western": "western", "telefilm": "tv movie",
}


class EntityLinker:
    def __init__(self, store):
        self.store = store

    def link(self, plan: QuestionDraft, selections: dict[str, int] | None = None,
             allowed_selections: dict[str, list[int]] | None = None) -> list[LinkResult]:
        selections = selections or {}
        allowed_selections = allowed_selections or {}
        if set(selections) - set(allowed_selections):
            raise ValueError("Selections require a signed clarification for the same question")
        results = {}
        # Resolve movie anchors before person context. No combinatorial guessing.
        ordered = sorted(plan.mentions, key=lambda m: m.type == Label.PERSON)
        for mention in ordered:
            search = GENRE_ALIASES.get(normalize(mention.text), mention.text) if (
                mention.type == Label.GENRE) else mention.text
            candidates = self.store.find_entities(search, mention.type)
            original_candidates = list(candidates)
            context = []
            if mention.year is not None:
                candidates = [c for c in candidates if c.year == mention.year]
                context.append(f"release_year={mention.year}")
            if mention.role:
                candidates = [c for c in candidates if mention.role in c.roles]
                context.append(f"role={mention.role}")
            if mention.context_movie_ref:
                movie = results.get(mention.context_movie_ref)
                if not movie or movie.status != "resolved":
                    candidates = []
                    context.append("Identifying movie must be resolved first")
                else:
                    candidates = [c for c in candidates if self.store.context_matches(
                        c.id, movie.canonical_id, mention.role)]
                    context.append(f"explicit_movie={movie.canonical_id}")
            method = candidates[0].method if candidates else "none"
            chosen = None
            if mention.ref in selections:
                selected = selections[mention.ref]
                if selected not in allowed_selections[mention.ref]:
                    raise ValueError("Entity was not proposed in this clarification")
                chosen = next((c for c in candidates if c.id == selected), None)
                if not chosen:
                    raise ValueError("Selected entity no longer satisfies the explicit context")
                method = "user_confirmed"
            elif len(candidates) == 1 and method != "fuzzy":
                chosen = candidates[0]
                method = "contextual" if context else method
            status = "resolved" if chosen else ("ambiguous" if candidates else "not_found")
            alternatives = candidates if candidates else original_candidates
            results[mention.ref] = LinkResult(
                mention=mention, status=status, canonical_id=chosen.id if chosen else None,
                canonical_name=chosen.name if chosen else None, method=method,
                contextual_evidence=context, alternatives=alternatives[:10])
        return [results[m.ref] for m in plan.mentions]
