import logging
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from moviegraphrag import __version__
from moviegraphrag.config import Settings
from moviegraphrag.embeddings import LocalEmbedder
from moviegraphrag.llm.groq import GroqLLM
from moviegraphrag.orchestration import MovieGraphRAG
from moviegraphrag.retrieval.neo4j_store import Neo4jStore
from moviegraphrag.schemas.models import AnswerResponse, Label, QuestionRequest


def create_app(service=None):
    @asynccontextmanager
    async def lifespan(app):
        if service is None:
            settings = Settings()
            store = Neo4jStore(settings)
            llm = GroqLLM(settings)
            embedder = LocalEmbedder(settings.embedding_model, settings.embedding_revision)
            app.state.service = MovieGraphRAG(store, embedder, llm, settings)
            app.state.model_error = None
            stop = Event()
            def warm_when_ready():
                while not stop.is_set():
                    try:
                        manifest = store.manifest()
                        if manifest and manifest.get("status") == "ready":
                            metadata = embedder.metadata()
                            indexed = manifest.get("embedding", {})
                            if any(metadata.get(k) != indexed.get(k) for k in ("model", "revision", "dimensions")):
                                raise RuntimeError("Embedding configuration differs from the indexed snapshot")
                            return
                    except Exception as exc:
                        logging.getLogger(__name__).warning("Model warmup pending: %s", type(exc).__name__)
                        if embedder.model is not None:
                            app.state.model_error = "Embedding configuration mismatch"
                            return
                    stop.wait(5)
            warmup = Thread(target=warm_when_ready, name="embedding-warmup", daemon=True)
            warmup.start()
            try:
                yield
            finally:
                stop.set()
                warmup.join(timeout=5)
                llm.close()
                store.close()
        else:
            app.state.service = service
            yield

    app = FastAPI(title="MovieGraphRAG", version=__version__, lifespan=lifespan,
                  description="Controlled graph and semantic retrieval with inspectable evidence.")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    def ready():
        svc = app.state.service
        try:
            state = svc.store.readiness()
            key = bool(svc.settings.groq_api_key.get_secret_value())
            # The background startup worker warms the model after ingestion; this endpoint never downloads.
            model_loaded = svc.embedder.model is not None
            state.update(llm_configured=key, embedding_loaded=model_loaded)
            model_error = getattr(app.state, "model_error", None)
            state["ready"] = state["ready"] and key and model_loaded and not model_error
            if model_error:
                state["reason"] = model_error
            return JSONResponse(state, status_code=200 if state["ready"] else 503)
        except Exception:
            return JSONResponse({"ready": False, "reason": "Neo4j unavailable"}, status_code=503)

    @app.post("/questions", response_model=AnswerResponse)
    def questions(request: QuestionRequest):
        try:
            return app.state.service.ask(request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/entities/search")
    def entities(q: str = Query(min_length=1, max_length=200), type: Label = Label.PERSON):
        try:
            candidates = app.state.service.store.find_entities(q, type)
            return {"candidates": [c.model_dump() for c in candidates[:10]],
                    "total_candidates": len(candidates), "truncated": len(candidates) > 10}
        except Exception as exc:
            raise HTTPException(503, "Entity store unavailable") from exc

    @app.get("/movies/{movie_id}")
    def movie(movie_id: int):
        if movie_id <= 0:
            raise HTTPException(422, "Positive TMDB ID required")
        try:
            result = app.state.service.store.movie(movie_id)
        except Exception as exc:
            raise HTTPException(503, "Movie store unavailable") from exc
        if result is None:
            raise HTTPException(404, "Movie not found in dataset")
        return result

    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
