import json

from neo4j import GraphDatabase, unit_of_work

from moviegraphrag.ingestion.dataset import normalize
from moviegraphrag.retrieval.compiler import ID_PROPERTIES, compile_graph
from moviegraphrag.schemas.models import EntityCandidate, Label, QuestionDraft

SCHEMA = [
    "CREATE CONSTRAINT movie_id IF NOT EXISTS FOR (n:Movie) REQUIRE n.movie_id IS UNIQUE",
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (n:Person) REQUIRE n.person_id IS UNIQUE",
    "CREATE CONSTRAINT genre_id IF NOT EXISTS FOR (n:Genre) REQUIRE n.genre_id IS UNIQUE",
    "CREATE CONSTRAINT corpus_id IF NOT EXISTS FOR (n:Corpus) REQUIRE n.name IS UNIQUE",
    "CREATE INDEX person_name IF NOT EXISTS FOR (n:Person) ON (n.normalized_name)",
    "CREATE INDEX person_surname IF NOT EXISTS FOR (n:Person) ON (n.surname)",
    "CREATE INDEX movie_title IF NOT EXISTS FOR (n:Movie) ON (n.normalized_title)",
    "CREATE INDEX genre_name IF NOT EXISTS FOR (n:Genre) ON (n.normalized_name)",
    "CREATE FULLTEXT INDEX person_names IF NOT EXISTS FOR (n:Person) ON EACH [n.name, n.aliases] "
    "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}",
    "CREATE FULLTEXT INDEX movie_titles IF NOT EXISTS FOR (n:Movie) "
    "ON EACH [n.title, n.original_title] "
    "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}",
    "CREATE VECTOR INDEX movie_embeddings IF NOT EXISTS FOR (n:Movie) ON n.embedding "
    "OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}}",
]


class Neo4jStore:
    def __init__(self, settings):
        self.settings = settings
        self.driver = GraphDatabase.driver(settings.neo4j_uri,
                                            auth=(settings.neo4j_user,
                                                  settings.neo4j_password.get_secret_value()),
                                            connection_timeout=5, max_transaction_retry_time=2)

    def close(self):
        self.driver.close()

    def read(self, query: str, parameters: dict | None = None) -> list[dict]:
        with self.driver.session(database=self.settings.neo4j_database) as session:
            @unit_of_work(timeout=self.settings.query_timeout)
            def work(tx):
                return [r.data() for r in tx.run(query, parameters or {})]
            return session.execute_read(work)

    def write(self, query: str, parameters: dict | None = None):
        with self.driver.session(database=self.settings.neo4j_database) as session:
            return session.execute_write(lambda tx: tx.run(query, parameters or {}).consume())

    def setup(self):
        for statement in SCHEMA:
            self.write(statement)

    def manifest(self) -> dict | None:
        rows = self.read("MATCH (c:Corpus {name:'tmdb'}) RETURN c.manifest AS manifest")
        return json.loads(rows[0]["manifest"]) if rows else None

    def readiness(self):
        self.driver.verify_connectivity()
        manifest = self.manifest()
        indexes = self.read("SHOW INDEXES YIELD name, state RETURN name, state")
        required = {"person_names", "movie_titles", "movie_embeddings", "movie_id", "person_id", "genre_id"}
        online = {r["name"] for r in indexes if r["state"] == "ONLINE"}
        return {"ready": bool(manifest and manifest.get("status") == "ready" and required <= online),
                "manifest": manifest, "indexes": indexes}

    def movie(self, movie_id: int):
        rows = self.read("MATCH (m:Movie {movie_id:$id}) RETURN properties(m) AS movie", {"id": movie_id})
        if not rows:
            return None
        result = rows[0]["movie"]
        result.pop("embedding", None)
        return result

    def find_entities(self, text: str, label: Label) -> list[EntityCandidate]:
        label = Label(label)
        key = normalize(text)
        prop = "normalized_title" if label == Label.MOVIE else "normalized_name"
        extra = " OR $key IN coalesce(n.aliases, [])" if label != Label.GENRE else ""
        rows = self.read(f"MATCH (n:{label.value}) WHERE n.{prop} = $key{extra} "
                         "RETURN properties(n) AS node", {"key": key})
        method = "exact"
        if not rows and label == Label.PERSON and len(key.split()) == 1:
            rows = self.read("MATCH (n:Person {surname:$key}) RETURN properties(n) AS node", {"key": key})
            method = "surname"
        if not rows and label != Label.GENRE:
            # Keep only alphanumeric tokens: the user cannot submit Lucene syntax.
            tokens = ["".join(c for c in t if c.isalnum()) for t in key.split()]
            query = " AND ".join(t + "~1" for t in tokens if len(t) >= 3)
            if query:
                index = "person_names" if label == Label.PERSON else "movie_titles"
                rows = self.read("CALL db.index.fulltext.queryNodes($index,$query) YIELD node,score "
                                 "RETURN properties(node) AS node ORDER BY score DESC LIMIT 10",
                                 {"index": index, "query": query})
                method = "fuzzy"
        result = []
        for row in rows:
            n = row["node"]
            candidate = EntityCandidate(id=n[ID_PROPERTIES[label]], type=label, method=method,
                                        name=n.get("name", n.get("title", "")),
                                        year=n.get("release_year"))
            if label == Label.PERSON:
                role_rows = self.read("MATCH (p:Person {person_id:$id})-[r:ACTED_IN|DIRECTED]->(m:Movie) "
                                      "RETURN type(r) AS role, m.title AS title ORDER BY title",
                                      {"id": candidate.id})
                candidate.roles = sorted({"actor" if r["role"] == "ACTED_IN" else "director"
                                          for r in role_rows})
                candidate.films = sorted({r["title"] for r in role_rows})[:5]
            result.append(candidate)
        return result

    def context_matches(self, person_id: int, movie_id: int, role: str | None):
        rel = {"actor": "ACTED_IN", "director": "DIRECTED", None: "ACTED_IN|DIRECTED"}[role]
        return bool(self.read(f"MATCH (:Person {{person_id:$p}})-[:{rel}]->"
                              "(:Movie {movie_id:$m}) RETURN 1 AS matched LIMIT 1",
                              {"p": person_id, "m": movie_id}))

    def graph_candidates(self, plan: QuestionDraft, bindings: dict[str, int]) -> tuple[list[dict], str]:
        cq = compile_graph(plan, bindings)
        prop = ID_PROPERTIES[next(v.label for v in plan.variables if v.ref == plan.target)]
        rows = self.read(cq.prefix + f" RETURN DISTINCT {cq.target}.{prop} AS id, "
                         f"coalesce({cq.target}.title,{cq.target}.name) AS name ORDER BY id", cq.parameters)
        return rows, cq.template_id

    def witnesses(self, plan: QuestionDraft, bindings: dict[str, int], entity_id: int):
        cq = compile_graph(plan, bindings)
        prop = ID_PROPERTIES[next(v.label for v in plan.variables if v.ref == plan.target)]
        where = cq.where + (" AND " if cq.where else "") + f"{cq.target}.{prop} = $result_id"
        nodes = ",".join(f"{alias} {{.*, embedding:null}}" for alias in cq.variables.values())
        edges = ",".join(f"{{type:type(r{i}),source:properties(startNode(r{i})),"
                         f"target:properties(endNode(r{i})),properties:properties(r{i})}}"
                         for i in range(len(plan.edges)))
        # Map projections strip large vectors from witness nodes and endpoints below.
        rows = self.read(cq.match + " WHERE " + where + f" RETURN [{nodes}] AS nodes, "
                         f"[{edges}] AS edges LIMIT 1", {**cq.parameters, "result_id": entity_id})
        if not rows:
            raise ValueError("Selected result no longer satisfies structural constraints")
        row = rows[0]
        identity_keys = {"person_id", "movie_id", "genre_id", "name", "title", "release_year", "snapshot_id"}
        for edge in row["edges"]:
            for endpoint in ("source", "target"):
                edge[endpoint] = {k: v for k, v in edge[endpoint].items() if k in identity_keys}
        row["nodes"] = [{k: v for k, v in node.items() if k in identity_keys} for node in row["nodes"]]
        return row

    def exact_scores(self, ids: list[int], vector: list[float]):
        return self.read("MATCH (m:Movie) WHERE m.movie_id IN $ids AND m.embedding IS NOT NULL "
                         "RETURN m.movie_id AS id, m.title AS name, "
                         "vector.similarity.cosine(m.embedding,$vector) AS score "
                         "ORDER BY score DESC, id", {"ids": ids, "vector": vector})

    def vector_candidates(self, vector: list[float], k: int):
        return self.read("CALL db.index.vector.queryNodes('movie_embeddings',$k,$vector) "
                         "YIELD node,score RETURN node.movie_id AS id,node.title AS name,score "
                         "ORDER BY score DESC,id", {"k": k, "vector": vector})
