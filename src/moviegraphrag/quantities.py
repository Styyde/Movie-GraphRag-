"""Recognize explicit result quantities, never infer database cardinality."""
import re

from moviegraphrag.ingestion.dataset import normalize


NUMBER_WORDS = {
    "un": 1, "une": 1, "one": 1, "a": 1, "an": 1,
    "deux": 2, "two": 2, "trois": 3, "three": 3, "quatre": 4, "four": 4,
    "cinq": 5, "five": 5, "six": 6, "sept": 7, "seven": 7, "huit": 8,
    "eight": 8, "neuf": 9, "nine": 9, "dix": 10, "ten": 10,
    "onze": 11, "eleven": 11, "douze": 12, "twelve": 12, "treize": 13,
    "thirteen": 13, "quatorze": 14, "fourteen": 14, "quinze": 15, "fifteen": 15,
    "seize": 16, "sixteen": 16, "dix-sept": 17, "seventeen": 17,
    "dix-huit": 18, "eighteen": 18, "dix-neuf": 19, "nineteen": 19,
    "vingt": 20, "twenty": 20, "trente": 30, "thirty": 30,
    "cinquante": 50, "fifty": 50, "cent": 100, "hundred": 100,
}
NUMBER = r"(?:\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")"
PREFIX = (r"(?:recommande\w*|propose\w*|trouve\w*|donne\w*|affiche\w*|liste\w*|"
          r"montre\w*|cherche\w*|voudrais|souhaite\w*|recommend\w*|give|show|find|list|"
          r"quels? sont|quelles? sont|what are)")
FILLER = r"(?:(?:moi|nous|me|us|les|des|the|exactement|seulement|juste|only|exactly)\s+)*"
NOUN = r"(?:films?|movies?|acteurs?|actors?|realisateurs?|directors?|personnes?|people|titres?)\b"
PATTERN = re.compile(r"\b" + PREFIX + r"[\s-]+" + FILLER + r"(?P<n>" + NUMBER + r")\s+"
                     r"(?:(?:meilleurs?|meilleures?|premiers?|premieres?|best|first)\s+)*" + NOUN)
TOP = re.compile(r"\btop\s+(?P<n>" + NUMBER + r")\b")


def explicit_quantity(question: str) -> int | None:
    text = normalize(question)
    match = TOP.search(text) or PATTERN.search(text)
    if match:
        raw = match.group("n")
        value = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
        return value if value > 0 else None
    return None


def validate_quantity(question: str, requested: int | None) -> int | None:
    explicit = explicit_quantity(question)
    if explicit is not None:
        return explicit
    if requested is not None:
        # Other phrasings remain possible, but the quantity must have a literal basis.
        text = normalize(question)
        tokens = {str(requested)} | {word for word, n in NUMBER_WORDS.items() if n == requested}
        if not any(re.search(r"\b" + re.escape(token) + r"\b", text) for token in tokens):
            raise ValueError("Requested limit must be explicitly present in the question")
    return requested
