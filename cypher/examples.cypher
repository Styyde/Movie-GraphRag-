// Q1: use actual IDs resolved from the graph.
MATCH (:Person {person_id:$director_id})-[:DIRECTED]->(m:Movie)
MATCH (:Person {person_id:$actor_id})-[:ACTED_IN]->(m)
RETURN DISTINCT m.movie_id, m.title;

// Exact semantic scoring over ALL structurally admissible IDs, no global top-k intersection.
MATCH (m:Movie)
WHERE m.movie_id IN $candidate_ids AND m.embedding IS NOT NULL
RETURN m.movie_id, vector.similarity.cosine(m.embedding,$query_vector) AS score
ORDER BY score DESC;
