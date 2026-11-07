// Reference schema; the authoritative executable list is retrieval/neo4j_store.py.
CREATE CONSTRAINT movie_id IF NOT EXISTS FOR (n:Movie) REQUIRE n.movie_id IS UNIQUE;
CREATE CONSTRAINT person_id IF NOT EXISTS FOR (n:Person) REQUIRE n.person_id IS UNIQUE;
CREATE CONSTRAINT genre_id IF NOT EXISTS FOR (n:Genre) REQUIRE n.genre_id IS UNIQUE;
CREATE VECTOR INDEX movie_embeddings IF NOT EXISTS FOR (n:Movie) ON n.embedding
OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}};
