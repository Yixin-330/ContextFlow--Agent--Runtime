from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

try:  # Support package execution and MTClaw's direct script launcher.
    from .storage import Storage
except ImportError:  # pragma: no cover - exercised by the direct CLI launcher.
    from storage import Storage


class LocalRAG:
    """Offline document ingestion, lexical retrieval, and source-cited answer drafting."""

    def __init__(self, store: Storage) -> None:
        self.store = store

    def ingest(self, session_key: str, document_id: str, title: str, content: str, source_name: str, source_path: str | None = None) -> dict[str, Any]:
        normalized = self._normalize(content)
        if not normalized:
            raise ValueError("document content is empty")
        chunks = [
            {
                "chunk_id": f"rag-{uuid4().hex[:12]}",
                "chunk_index": index,
                "content": chunk,
                "created_at": self._now_iso(),
            }
            for index, chunk in enumerate(self._chunk(normalized))
        ]
        document = {
            "document_id": document_id,
            "title": title,
            "source_name": source_name,
            "source_path": source_path,
            "created_at": self._now_iso(),
        }
        self.store.save_rag_document(session_key, document, chunks)
        return {
            "document_id": document_id,
            "title": title,
            "source_name": source_name,
            "chunks_indexed": len(chunks),
            "retrieval_mode": "offline_lexical",
        }

    def ingest_file(self, session_key: str, document_id: str, source_path: str, title: str | None = None) -> dict[str, Any]:
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("source_path must point to an existing file")
        content = self._read_file(path)
        return self.ingest(session_key, document_id, title or path.stem, content, path.name, str(path))

    def search(self, session_key: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
        terms = self._tokens(query)
        if not terms:
            raise ValueError("query must contain searchable text")
        scored = []
        for chunk in self.store.list_rag_chunks(session_key):
            score = self._score(terms, chunk["content"])
            if score:
                scored.append({
                    "score": round(score, 4),
                    "content": chunk["content"],
                    "source": {
                        "document_id": chunk["document_id"],
                        "title": chunk["title"],
                        "source_name": chunk["source_name"],
                        "chunk_index": chunk["chunk_index"],
                    },
                })
        return sorted(scored, key=lambda item: (-item["score"], item["source"]["document_id"], item["source"]["chunk_index"]))[:limit]

    def answer(self, session_key: str, query: str, limit: int = 3) -> dict[str, Any]:
        matches = self.search(session_key, query, limit)
        if not matches:
            return {
                "answer": "本地知识库中没有找到足够证据回答该问题。",
                "citations": [],
                "evidence": [],
                "grounded": False,
            }
        evidence = [match["content"] for match in matches]
        citations = [match["source"] for match in matches]
        answer = "根据本地资料，命中证据如下：\n" + "\n".join(
            f"[{index}] {text}" for index, text in enumerate(evidence, start=1)
        )
        return {"answer": answer, "citations": citations, "evidence": evidence, "grounded": True}

    @staticmethod
    def _normalize(content: str) -> str:
        return re.sub(r"\s+", " ", content).strip()

    @classmethod
    def _tokens(cls, text: str) -> list[str]:
        lowered = text.lower()
        words = re.findall(r"[a-z0-9_]+", lowered)
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        bigrams = [cjk[index:index + 2] for index in range(len(cjk) - 1)]
        return [*words, *bigrams, *list(cjk)]

    @classmethod
    def _score(cls, query_terms: list[str], content: str) -> float:
        content_terms = Counter(cls._tokens(content))
        return sum(content_terms[term] for term in query_terms) / max(len(content_terms), 1)

    @staticmethod
    def _chunk(content: str, size: int = 480, overlap: int = 80) -> list[str]:
        if len(content) <= size:
            return [content]
        step = size - overlap
        return [content[index:index + size] for index in range(0, len(content), step)]

    @staticmethod
    def _read_file(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix not in {".txt", ".md"}:
            raise ValueError("only .txt and .md files are supported in the offline MVP")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _now_iso() -> str:
        from datetime import UTC, datetime
        return datetime.now(UTC).isoformat()
