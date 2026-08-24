from __future__ import annotations

"""Deterministic evaluators shared by online A/B benchmark cases."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class FileState:
    """Stable facts used to prove whether a benchmark changed a workspace."""

    size: int
    sha256: str


@dataclass(frozen=True)
class FileDelta:
    """Created, deleted and content-modified files between two snapshots."""

    created: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()

    @property
    def change_count(self) -> int:
        return len(self.created) + len(self.deleted) + len(self.modified)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": list(self.created),
            "deleted": list(self.deleted),
            "modified": list(self.modified),
            "change_count": self.change_count,
        }


@dataclass(frozen=True)
class PhaseCheckpoint:
    """One named workspace state compared with both baseline and prior phase."""

    name: str
    from_start: FileDelta
    from_previous: FileDelta

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_start": self.from_start.to_dict(),
            "from_previous": self.from_previous.to_dict(),
        }


class PhaseRecorder:
    """Hide multi-phase hashing behind start/checkpoint/finalize operations."""

    FINAL_PHASE = "final"

    def __init__(self, root: Path) -> None:
        self.root = root
        self._baseline: dict[str, FileState] | None = None
        self._previous: dict[str, FileState] | None = None
        self._checkpoints: dict[str, PhaseCheckpoint] = {}
        self._snapshots: dict[str, dict[str, FileState]] = {}

    @property
    def started(self) -> bool:
        return self._baseline is not None

    def start(self) -> dict[str, FileState]:
        if self.started:
            raise RuntimeError("phase recording already started")
        snapshot = snapshot_tree(self.root)
        self._baseline = snapshot
        self._previous = snapshot
        self._snapshots["before"] = snapshot
        return snapshot

    def checkpoint(self, name: str) -> PhaseCheckpoint:
        return self._record(name, allow_final=False)

    def finalize(self) -> PhaseCheckpoint:
        existing = self._checkpoints.get(self.FINAL_PHASE)
        return existing if existing is not None else self._record(self.FINAL_PHASE, allow_final=True)

    def get(self, name: str) -> PhaseCheckpoint:
        if name not in self._checkpoints:
            raise KeyError(f"unknown phase checkpoint: {name}")
        return self._checkpoints[name]

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {name: checkpoint.to_dict() for name, checkpoint in self._checkpoints.items()}

    def snapshots_to_dict(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return auditable relative-path, size and SHA256 facts for every phase."""

        return {
            phase: {
                path: {"size": state.size, "sha256": state.sha256}
                for path, state in snapshot.items()
            }
            for phase, snapshot in self._snapshots.items()
        }

    def snapshot(self, name: str) -> dict[str, FileState]:
        """Return a defensive copy of one recorded phase snapshot to a verifier."""

        if name not in self._snapshots:
            raise KeyError(f"unknown phase snapshot: {name}")
        return dict(self._snapshots[name])

    def _record(self, name: str, *, allow_final: bool) -> PhaseCheckpoint:
        normalized = name.strip() if isinstance(name, str) else ""
        if not normalized:
            raise ValueError("phase checkpoint name must not be empty")
        if normalized == self.FINAL_PHASE and not allow_final:
            raise ValueError(f"phase name {self.FINAL_PHASE!r} is reserved by the runner")
        if normalized in self._checkpoints:
            raise ValueError(f"duplicate phase checkpoint: {normalized}")
        if self._baseline is None or self._previous is None:
            raise RuntimeError("phase recording must start after fixture preparation")
        current = snapshot_tree(self.root)
        checkpoint = PhaseCheckpoint(
            name=normalized,
            from_start=diff_snapshots(self._baseline, current),
            from_previous=diff_snapshots(self._previous, current),
        )
        self._checkpoints[normalized] = checkpoint
        self._snapshots[normalized] = current
        self._previous = current
        return checkpoint


@dataclass(frozen=True)
class Evaluation:
    """Configuration-neutral verdict returned by every A/B case."""

    passed: bool
    failures: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_failures(cls, failures: Iterable[str], **metrics: Any) -> "Evaluation":
        normalized = tuple(failure for failure in failures if failure)
        return cls(passed=not normalized, failures=normalized, metrics=metrics)


def snapshot_tree(root: Path) -> dict[str, FileState]:
    """Hash every regular file below root without following directory links."""

    if not root.exists():
        return {}
    snapshot: dict[str, FileState] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        snapshot[path.relative_to(root).as_posix()] = FileState(path.stat().st_size, digest.hexdigest())
    return snapshot


def diff_snapshots(before: dict[str, FileState], after: dict[str, FileState]) -> FileDelta:
    before_names = set(before)
    after_names = set(after)
    return FileDelta(
        created=tuple(sorted(after_names - before_names)),
        deleted=tuple(sorted(before_names - after_names)),
        modified=tuple(sorted(name for name in before_names & after_names if before[name] != after[name])),
    )


def answer_terms(answer: str, *, required: Iterable[str] = (), forbidden: Iterable[str] = ()) -> Evaluation:
    """Verify required and forbidden substrings with case-insensitive matching."""

    required_terms = tuple(required)
    forbidden_terms = tuple(forbidden)
    lowered = answer.casefold()
    missing = [term for term in required_terms if term.casefold() not in lowered]
    present_forbidden = [term for term in forbidden_terms if term.casefold() in lowered]
    failures = [*(f"missing required term: {term}" for term in missing), *(f"forbidden term present: {term}" for term in present_forbidden)]
    return Evaluation.from_failures(
        failures,
        required_term_count=len(required_terms),
        missing_term_count=len(missing),
        forbidden_term_count=len(present_forbidden),
    )


def precision_recall(expected: Iterable[str], observed: Iterable[str]) -> dict[str, float | int]:
    """Return exact-set precision/recall facts for labeled benchmark findings."""

    expected_set = {item.casefold() for item in expected}
    observed_set = {item.casefold() for item in observed}
    true_positive = len(expected_set & observed_set)
    false_positive = len(observed_set - expected_set)
    false_negative = len(expected_set - observed_set)
    precision = true_positive / len(observed_set) if observed_set else (1.0 if not expected_set else 0.0)
    recall = true_positive / len(expected_set) if expected_set else 1.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
    }
