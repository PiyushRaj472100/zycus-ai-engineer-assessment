
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Chunk:
 

    source_file: str          # relative path, e.g. "products/databridge-pro.md"
    heading: str              # nearest heading above this chunk (or file title)
    content: str              # raw Markdown text of the chunk
    tokens: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self.tokens = _tokenise(self.content + " " + self.heading)



# Internal helpers


_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "this", "that",
    "these", "those", "it", "its", "not", "as", "if", "then", "than",
    "so", "we", "our", "you", "your", "they", "their", "i", "my", "me",
}


def _tokenise(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_\-]+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 1}


def _extract_heading(text: str) -> str:
    """Return the first Markdown heading found in *text*, else empty string."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _split_into_chunks(content: str, source_file: str) -> List[Chunk]:
    """Split a Markdown document on `---` boundaries and return Chunk list."""
    # Track the most recent heading seen as we walk through the document.
    current_heading = _extract_heading(content) or Path(source_file).stem

    sections = content.split("\n---\n")
    chunks: List[Chunk] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        heading = _extract_heading(section) or current_heading
        # Update running heading tracker.
        if heading:
            current_heading = heading
        chunks.append(Chunk(source_file=source_file, heading=heading, content=section))
    return chunks



# Public API


class KnowledgeBase:
    """Loaded, chunked KB with keyword-based retrieval."""

    def __init__(self, kb_path: Path) -> None:
        self._chunks: List[Chunk] = []
        self._idf: dict[str, float] = {}
        self._load(kb_path)
        self._build_idf()

    def _load(self, kb_path: Path) -> None:
        for md_file in sorted(kb_path.rglob("*.md")):
            relative = md_file.relative_to(kb_path).as_posix()
            content = md_file.read_text(encoding="utf-8")
            self._chunks.extend(_split_into_chunks(content, relative))

    def _build_idf(self) -> None:
        """Pre-compute IDF scores so retrieval is fast at query time."""
        n = len(self._chunks)
        if n == 0:
            return
        df: dict[str, int] = {}
        for chunk in self._chunks:
            for token in chunk.tokens:
                df[token] = df.get(token, 0) + 1
        self._idf = {token: math.log(n / count) for token, count in df.items()}

    def _score(self, chunk: Chunk, query_tokens: set[str]) -> float:
        """Simple TF-IDF-like dot product between query and chunk."""
        return sum(
            self._idf.get(t, 0.0)
            for t in query_tokens & chunk.tokens
        )

    def retrieve(self, query: str, top_k: int = 4) -> List[Chunk]:
        """
        Return up to *top_k* chunks most relevant to *query*.
        Returns an empty list when the KB is empty or no tokens match.
        """
        if not self._chunks:
            return []
        query_tokens = _tokenise(query)
        if not query_tokens:
            return []

        scored = [(self._score(c, query_tokens), c) for c in self._chunks]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Only return chunks with a positive score (at least one matching token).
        return [c for score, c in scored[:top_k] if score > 0.0]

    @property
    def total_chunks(self) -> int:
        return len(self._chunks)


# Module-level singleton — loaded once at import time.
# The path is relative to the working directory (project root).
def load_kb(kb_path: Path) -> KnowledgeBase:
    return KnowledgeBase(kb_path)
