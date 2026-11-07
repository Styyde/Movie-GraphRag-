import hashlib
from dataclasses import dataclass

DOCUMENT_VERSION = "movie-document-v1"


@dataclass
class Document:
    movie_id: int
    text: str
    coverage: str
    text_hash: str
    token_count: int
    keywords_removed: int
    document_version: str = DOCUMENT_VERSION


def build_document(movie: dict, tokenizer, max_tokens: int = 512,
                   variant: str = "enriched", prefix: str = "passage: ") -> Document | None:
    overview = movie["overview"]
    keywords = list(movie.get("keywords", []))
    if not overview and (not keywords or variant != "enriched"):
        return None
    coverage = "overview" if overview else "metadata_only"
    def render(ks):
        if variant == "overview":
            return overview
        text = f"Title: {movie['title']}\nOverview: {overview}"
        if variant == "enriched":
            text += "\nGenres: " + ", ".join(movie.get("genre_names", []))
            text += "\nKeywords: " + ", ".join(ks)
        elif variant != "title_overview":
            raise ValueError("Unknown document variant")
        return text
    removed = 0
    while True:
        text = render(keywords)
        tokens = len(tokenizer.encode(prefix + text, add_special_tokens=True, truncation=False))
        if tokens <= max_tokens:
            return Document(movie["movie_id"], text, coverage,
                            hashlib.sha256(text.encode()).hexdigest(), tokens, removed,
                            f"{DOCUMENT_VERSION}:{variant}")
        if not keywords:
            raise ValueError(f"Document {movie['movie_id']} exceeds {max_tokens} tokens without keywords")
        keywords.pop()
        removed += 1
