from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Label(StrEnum):
    PERSON = "Person"
    MOVIE = "Movie"
    GENRE = "Genre"


class Relation(StrEnum):
    ACTED_IN = "ACTED_IN"
    DIRECTED = "DIRECTED"
    IN_GENRE = "IN_GENRE"


class Mention(StrictModel):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    text: str = Field(min_length=1, max_length=200)
    type: Label
    role: Literal["actor", "director"] | None = None
    # Only a literal identifying phrase supplied in the question, never inferred from a nonempty answer.
    context_movie_ref: str | None = None
    year: int | None = Field(default=None, ge=1800, le=2200)


class Variable(StrictModel):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    label: Label
    mention_ref: str | None = None


class Edge(StrictModel):
    source: str
    relation: Relation
    target: str


class Filter(StrictModel):
    variable: str
    field: Literal["release_year", "vote_average", "vote_count", "original_language"]
    op: Literal["eq", "gte", "lte"]
    value: str | int | float


class ThemeRequest(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    themes: list[str] = Field(min_length=1, max_length=5)
    operator: Literal["ALL", "ANY"] = "ALL"
    requirement: Literal["association", "central"] = "association"


class QuestionDraft(StrictModel):
    supported: bool = True
    unsupported_reason: str | None = None
    answer_type: Literal["movie_list", "person_list"] = "movie_list"
    mentions: list[Mention] = Field(default_factory=list, max_length=8)
    variables: list[Variable] = Field(default_factory=list, max_length=6,
        description="Declare every edge endpoint and the target, including every entity mention binding. Never empty for a supported plan.")
    edges: list[Edge] = Field(default_factory=list, max_length=4)
    target: str = "result"
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    theme_request: ThemeRequest | None = None
    distinct_movies: list[tuple[str, str]] = Field(default_factory=list, max_length=2)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_graph(self):
        if not self.supported:
            if not self.unsupported_reason:
                raise ValueError("Unsupported questions need a reason")
            return self
        vs = {v.ref: v for v in self.variables}
        ms = {m.ref: m for m in self.mentions}
        if len(vs) != len(self.variables) or len(ms) != len(self.mentions):
            raise ValueError("Duplicate references")
        expected = Label.MOVIE if self.answer_type == "movie_list" else Label.PERSON
        if self.target not in vs or vs[self.target].label != expected:
            raise ValueError("Target has wrong label or is missing")
        used_mentions = set()
        for v in self.variables:
            if v.mention_ref:
                if v.mention_ref not in ms or ms[v.mention_ref].type != v.label:
                    raise ValueError("Invalid mention binding")
                used_mentions.add(v.mention_ref)
        for m in self.mentions:
            if m.role is not None and m.type != Label.PERSON:
                raise ValueError("Only people have actor/director roles")
            if m.year is not None and m.type != Label.MOVIE:
                raise ValueError("Release years identify movies only")
            if m.context_movie_ref:
                if m.type != Label.PERSON:
                    raise ValueError("Explicit film context identifies people only")
                if m.context_movie_ref not in ms or ms[m.context_movie_ref].type != Label.MOVIE:
                    raise ValueError("Context must refer to a movie mention")
                used_mentions.add(m.context_movie_ref)
        if used_mentions != set(ms):
            raise ValueError("Unbound mentions are not supported")
        pairs = set()
        adjacency = {ref: set() for ref in vs}
        for e in self.edges:
            if e.source not in vs or e.target not in vs or e.source == e.target:
                raise ValueError("Invalid edge references")
            labels = (vs[e.source].label, vs[e.target].label)
            allowed = (Label.MOVIE, Label.GENRE) if e.relation == Relation.IN_GENRE else (
                Label.PERSON, Label.MOVIE
            )
            if labels != allowed:
                raise ValueError("Invalid relationship direction")
            pair = tuple(sorted((e.source, e.target)))
            if pair in pairs:
                raise ValueError("Parallel edges are outside the MVP patterns")
            pairs.add(pair)
            adjacency[e.source].add(e.target)
            adjacency[e.target].add(e.source)
        reached, pending = set(), [self.target]
        while pending:
            ref = pending.pop()
            if ref not in reached:
                reached.add(ref)
                pending.extend(adjacency[ref] - reached)
        if reached != set(vs) or len(self.edges) != len(vs) - 1:
            raise ValueError("Only connected acyclic patterns are supported")
        for f in self.filters:
            if f.variable not in vs or vs[f.variable].label != Label.MOVIE:
                raise ValueError("Filters require a Movie variable")
            if f.field == "original_language":
                if f.op != "eq" or not isinstance(f.value, str) or len(f.value) > 10:
                    raise ValueError("Invalid language filter")
            elif isinstance(f.value, bool) or not isinstance(f.value, (int, float)):
                raise ValueError("Numeric filter required")
            elif f.field == "release_year" and not 1800 <= f.value <= 2200:
                raise ValueError("Invalid year")
            elif f.field == "vote_average" and not 0 <= f.value <= 10:
                raise ValueError("Invalid rating")
            elif f.field == "vote_count" and f.value < 0:
                raise ValueError("Invalid vote count")
        for a, b in self.distinct_movies:
            if a == b or a not in vs or b not in vs or any(
                vs[x].label != Label.MOVIE for x in (a, b)
            ):
                raise ValueError("Invalid distinct-movie constraint")
        structural = bool(self.mentions or self.edges or self.filters)
        if structural and not (self.mentions or self.filters):
            raise ValueError("Graph patterns need an anchor")
        if not structural and not self.theme_request:
            raise ValueError("Empty question plan")
        if self.theme_request and expected != Label.MOVIE:
            raise ValueError("Semantic person retrieval is outside the MVP")
        if self.theme_request and len(set(self.theme_request.themes)) != len(self.theme_request.themes):
            raise ValueError("Duplicate requested themes")
        for variable in vs:
            for field in ("release_year", "vote_average", "vote_count"):
                filters = [f for f in self.filters if f.variable == variable and f.field == field]
                lower = max((f.value for f in filters if f.op in {"gte", "eq"}), default=-float("inf"))
                upper = min((f.value for f in filters if f.op in {"lte", "eq"}), default=float("inf"))
                if lower > upper:
                    raise ValueError("Contradictory filters")
        return self


class EntityCandidate(StrictModel):
    id: int = Field(gt=0)
    name: str
    type: Label
    method: str
    roles: list[str] = Field(default_factory=list)
    films: list[str] = Field(default_factory=list)
    year: int | None = None


class LinkResult(StrictModel):
    mention: Mention
    status: Literal["resolved", "ambiguous", "not_found"]
    canonical_id: int | None = None
    canonical_name: str | None = None
    method: str
    contextual_evidence: list[str] = Field(default_factory=list)
    alternatives: list[EntityCandidate] = Field(default_factory=list)


class QuestionRequest(StrictModel):
    question: str = Field(min_length=3, max_length=3000)
    language: Literal["fr", "en"] = "fr"
    limit: int = Field(default=5, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=10000)
    selections: dict[str, int] = Field(default_factory=dict)
    clarification_token: str | None = None


class ThemeJudgment(StrictModel):
    theme: str
    source: Literal["overview", "keyword", "both", "none"]
    centrality: Literal["central", "peripheral", "unknown"]
    overview_quote: str | None = None
    keyword_ids: list[int] = Field(default_factory=list)


class FilmAssessment(StrictModel):
    movie_id: int
    judgments: list[ThemeJudgment]


class AssessmentBatch(StrictModel):
    films: list[FilmAssessment]


class CitationStatement(StrictModel):
    text: str
    evidence_ids: list[str] = Field(min_length=1)


class GeneratedAnswer(StrictModel):
    statements: list[CitationStatement]


class Evidence(StrictModel):
    id: str
    kind: Literal["relation", "overview", "keyword"]
    entity_id: int
    data: dict


class Coverage(StrictModel):
    total_candidates: int = 0
    scored: int = 0
    examined: int = 0
    returned: int = 0
    without_embedding: int = 0
    semantic_coverage: Literal["not_applicable", "complete", "partial"] = "not_applicable"
    approximate: bool = False
    offset: int = 0
    has_more: bool = False


class AnswerResponse(StrictModel):
    status: Literal["ok", "needs_clarification", "entity_not_found", "no_match_in_dataset",
                    "insufficient_evidence", "unsupported", "dependency_error"]
    strategy: Literal["GRAPH", "VECTOR", "HYBRID_GRAPH_FIRST"] | None = None
    resolved_entities: list[LinkResult] = Field(default_factory=list)
    clarification_token: str | None = None
    results: list[dict] = Field(default_factory=list)
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    warnings: list[str] = Field(default_factory=list)
    trace_id: str
    trace: dict = Field(default_factory=dict)
