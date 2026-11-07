from dataclasses import dataclass

from moviegraphrag.schemas.models import Label, QuestionDraft

ID_PROPERTIES = {Label.MOVIE: "movie_id", Label.PERSON: "person_id", Label.GENRE: "genre_id"}


@dataclass
class CompiledQuery:
    match: str
    where: str
    parameters: dict
    target: str
    variables: dict[str, str]
    template_id: str

    @property
    def prefix(self):
        return self.match + (" WHERE " + self.where if self.where else "")


def route(plan: QuestionDraft) -> str:
    structural = bool(plan.mentions or plan.edges or plan.filters)
    if not plan.supported:
        raise ValueError("Unsupported plan")
    return "HYBRID_GRAPH_FIRST" if structural and plan.theme_request else (
        "GRAPH" if structural else "VECTOR"
    )


def compile_graph(plan: QuestionDraft, bindings: dict[str, int]) -> CompiledQuery:
    # Even programmatic callers must pass the same validation boundary.
    plan = QuestionDraft.model_validate(plan.model_dump())
    aliases = {v.ref: f"v{i}" for i, v in enumerate(plan.variables)}
    vs = {v.ref: v for v in plan.variables}
    patterns = []
    for i, e in enumerate(plan.edges):
        a, b = aliases[e.source], aliases[e.target]
        patterns.append(f"({a}:{vs[e.source].label.value})-[r{i}:{e.relation.value}]->"
                        f"({b}:{vs[e.target].label.value})")
    if not patterns:
        v = vs[plan.target]
        patterns = [f"({aliases[v.ref]}:{v.label.value})"]
    params, predicates = {}, []
    for v in plan.variables:
        if v.mention_ref:
            value = bindings.get(v.mention_ref)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Unresolved binding {v.mention_ref}")
            key = f"id_{aliases[v.ref]}"
            params[key] = value
            predicates.append(f"{aliases[v.ref]}.{ID_PROPERTIES[v.label]} = ${key}")
    ops = {"eq": "=", "gte": ">=", "lte": "<="}
    for i, f in enumerate(plan.filters):
        key = f"filter_{i}"
        params[key] = f.value
        predicates.append(f"{aliases[f.variable]}.{f.field} {ops[f.op]} ${key}")
    for a, b in plan.distinct_movies:
        predicates.append(f"{aliases[a]}.movie_id <> {aliases[b]}.movie_id")
    signature = "+".join(sorted(e.relation.value for e in plan.edges)) or "node"
    return CompiledQuery("MATCH " + ", ".join(patterns), " AND ".join(predicates), params,
                         aliases[plan.target], aliases, f"tree-v1:{signature}")
