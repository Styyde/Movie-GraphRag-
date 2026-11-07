from threading import Lock


class LocalEmbedder:
    """Lazy local model: constructing the API never triggers a network download."""

    def __init__(self, model_name: str, revision: str):
        self.model_name, self.revision = model_name, revision
        self.model = None
        self.lock = Lock()

    def load(self):
        with self.lock:
            if self.model is None:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name, revision=self.revision, device="cpu")
                self.revision = getattr(self.model[0].auto_model.config, "_commit_hash", None) or self.revision
        return self.model

    @property
    def is_e5(self):
        return self.model_name.startswith("intfloat/")

    def metadata(self):
        model = self.load()
        return {"model": self.model_name, "revision": self.revision,
                "dimensions": model.get_sentence_embedding_dimension(),
                "max_tokens": model.max_seq_length,
                "prefix": "passage: " if self.is_e5 else ""}

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        prefix = "passage: " if self.is_e5 else ""
        return self.load().encode([prefix + t for t in texts], normalize_embeddings=True,
                                  batch_size=32, show_progress_bar=False).tolist()

    def encode_query(self, text: str) -> list[float]:
        prefix = "query: " if self.is_e5 else ""
        model = self.load()
        if len(model.tokenizer.encode(prefix + text, truncation=False)) > model.max_seq_length:
            raise ValueError("Semantic query exceeds embedding token window")
        return model.encode(prefix + text, normalize_embeddings=True).tolist()
