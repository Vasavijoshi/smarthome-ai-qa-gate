"""Chunking and retrieval: split manuals at headings, then rank chunks by cosine similarity."""

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a manual, with a stable id like 'vayu_f1_fan#timer'."""

    chunk_id: str
    text: str


@dataclass(frozen=True)
class Retrieved:
    """A chunk returned for a query, with its similarity score."""

    chunk_id: str
    score: float
    text: str


def slugify(heading: str) -> str:
    """Turn a heading like 'Error codes' into 'error_codes'."""
    return re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")


def section_of(chunk_id: str) -> str:
    """Return the 'document#section' part of an id, dropping a split suffix like '/2'."""
    return chunk_id.split("/")[0]


def split_words(words: list[str], max_words: int, overlap: int) -> list[list[str]]:
    """Split a word list into windows of at most max_words that overlap by `overlap` words."""
    if len(words) <= max_words:
        return [words]
    step = max_words - overlap
    windows = []
    for start in range(0, len(words), step):
        windows.append(words[start : start + max_words])
        if start + max_words >= len(words):
            break
    return windows


def chunk_document(doc_id: str, markdown: str, max_words: int, overlap: int) -> list[Chunk]:
    """Split one markdown document at its '## ' headings into chunks."""
    chunks = []
    # Text before the first '## ' heading (title and notice) is not indexed.
    for section in re.split(r"^## ", markdown, flags=re.MULTILINE)[1:]:
        heading, _, body = section.partition("\n")
        base_id = f"{doc_id}#{slugify(heading)}"
        parts = split_words(body.split(), max_words, overlap)
        for i, words in enumerate(parts, start=1):
            chunk_id = base_id if len(parts) == 1 else f"{base_id}/{i}"
            chunks.append(Chunk(chunk_id, f"{heading.strip()}: {' '.join(words)}"))
    return chunks


def load_chunks(kb_dir: Path, max_words: int, overlap: int) -> list[Chunk]:
    """Chunk every markdown file in the knowledge-base folder."""
    chunks = []
    for path in sorted(kb_dir.glob("*.md")):
        chunks += chunk_document(path.stem, path.read_text(encoding="utf-8"), max_words, overlap)
    return chunks


class Retriever:
    """Ranks chunks by cosine similarity between normalised vectors."""

    def __init__(self, chunks: list[Chunk], kind: str = "embedding") -> None:
        """Embed all chunks once, with sentence embeddings or TF-IDF (used in CI)."""
        self.chunks = chunks
        texts = [c.text for c in chunks]
        if kind == "embedding":
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer("all-MiniLM-L6-v2")
            self._embed = lambda items: model.encode(items, normalize_embeddings=True)
        elif kind == "tfidf":
            from sklearn.feature_extraction.text import TfidfVectorizer

            vectorizer = TfidfVectorizer(stop_words="english").fit(texts)
            self._embed = lambda items: vectorizer.transform(items).toarray()
        else:
            raise ValueError(f"Unknown retriever: {kind}")
        self.matrix = np.asarray(self._embed(texts))

    def search(self, query: str, k: int) -> list[Retrieved]:
        """Return the top-k chunks for a query, best first."""
        query_vec = np.asarray(self._embed([query]))[0]
        # Both sides are L2-normalised, so the dot product is the cosine similarity.
        scores = self.matrix @ query_vec
        best = np.argsort(-scores, kind="stable")[:k]
        return [
            Retrieved(self.chunks[i].chunk_id, round(float(scores[i]), 4), self.chunks[i].text) for i in best
        ]
