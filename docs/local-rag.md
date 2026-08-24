# LocalRAGAgent

## Scope

LocalRAGAgent is ContextFlow's first vertical Agent. It provides a session-isolated, offline knowledge space for project material and local notes. The MVP uses deterministic lexical retrieval so its evidence, latency, and failure behaviour are reproducible on an MTT AIBOOK without a cloud vector database.

## Tool interface

| Tool | Purpose | Safety boundary |
| --- | --- | --- |
| `rag_ingest` | Index inline text or a local `.txt` / `.md` document | Reads material and writes only the local SQLite index |
| `rag_search` | Return ranked evidence excerpts | Read-only |
| `rag_answer` | Return an evidence-grounded answer draft with citations | Returns "no local evidence" instead of inventing an answer |

All three tools require `session_key`. Documents, chunks, search results, and telemetry are isolated by that key.

## Evaluation evidence

Use execution events to calculate ingest latency, query latency, grounded-answer rate, citation coverage, and cross-session leakage. A valid demo should show one grounded answer with citations and one no-evidence answer.

## Upgrade seam

The public tools stay unchanged when lexical scoring is replaced by an on-device embedding index, reranker, PDF parser, or DOCX parser. Only the `LocalRAG` implementation changes.
