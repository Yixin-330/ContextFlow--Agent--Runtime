from __future__ import annotations

"""Case lifecycle interface for isolated natural-language A/B benchmarks."""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .adapters import BenchmarkRequest, OpenClawGatewayAdapter, RunResult
from .evaluators import Evaluation, FileDelta, PhaseCheckpoint, PhaseRecorder, answer_terms


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    scene: str
    attempt: int
    adapter_name: str
    order: str
    order_position: int
    session_key: str
    workspace: Path
    phases: PhaseRecorder

    def checkpoint(self, name: str) -> PhaseCheckpoint:
        """Record a named post-prepare workspace state for multi-turn verification."""

        return self.phases.checkpoint(name)


@dataclass
class CaseOutcome:
    """All turns and local artifacts produced by one adapter run."""

    turns: list[RunResult]
    artifacts: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.turns) and all(turn.success for turn in self.turns)

    @property
    def latency_ms(self) -> float:
        return round(sum(turn.latency_ms for turn in self.turns), 3)

    @property
    def final_answer(self) -> str:
        return self.turns[-1].final_answer if self.turns else ""


class ABCase(Protocol):
    """The only lifecycle interface the runner requires from a scenario."""

    case_id: str
    scene: str
    description: str

    def prepare(self, context: CaseContext) -> Any: ...

    def execute(self, adapter: OpenClawGatewayAdapter, context: CaseContext, fixture: Any) -> CaseOutcome: ...

    def verify(self, context: CaseContext, fixture: Any, outcome: CaseOutcome, file_delta: FileDelta) -> Evaluation: ...


@dataclass(frozen=True)
class FileSearchCase:
    """Small built-in case that proves the framework through the existing task."""

    case_id: str = "ab_file_search_v2"
    scene: str = "file_search"
    description: str = "Read-only filename search with deterministic file verification"
    expected_name: str = "meeting-notes.txt"
    query: str = "notes"

    def prepare(self, context: CaseContext) -> dict[str, Any]:
        target = context.workspace / self.expected_name
        target.write_text("Archive after the review meeting.\n", encoding="utf-8")
        (context.workspace / "unrelated.txt").write_text("Control file.\n", encoding="utf-8")
        return {"target": target}

    def execute(self, adapter: OpenClawGatewayAdapter, context: CaseContext, fixture: dict[str, Any]) -> CaseOutcome:
        prompt = (
            f'请在目录 "{context.workspace}" 中搜索文件名包含 "{self.query}" 的文件。'
            "只读搜索，不要修改任何文件；请返回命中的相对路径和数量。"
        )
        result = adapter.run(BenchmarkRequest(
            case_id=self.case_id,
            prompt=prompt,
            session_key=context.session_key,
            system_prompt="Complete only the requested local file search. Never modify files.",
        ))
        return CaseOutcome(turns=[result])

    def verify(self, context: CaseContext, fixture: dict[str, Any], outcome: CaseOutcome, file_delta: FileDelta) -> Evaluation:
        answer = answer_terms(outcome.final_answer, required=(self.expected_name,))
        failures = list(answer.failures)
        if not outcome.success:
            failures.append("gateway request failed")
        if file_delta.change_count:
            failures.append("read-only search changed the workspace")
        return Evaluation.from_failures(
            failures,
            answer_term_verified=answer.passed,
            workspace_unchanged=file_delta.change_count == 0,
        )


@dataclass(frozen=True)
class LocalRagCase:
    """Question set shared by native and ContextFlow A/B configurations."""

    question_id: str
    case_id: str = "ab_local_rag_v1"
    scene: str = "local_rag"
    description: str = "Grounded local-document question with source verification"

    FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"

    def prepare(self, context: CaseContext) -> dict[str, Any]:
        source = self.FIXTURE_ROOT / "rag" / "contextflow_reference.md"
        questions_path = self.FIXTURE_ROOT / "ground_truth" / "rag_questions.json"
        questions = json.loads(questions_path.read_text(encoding="utf-8"))
        question = next((item for item in questions if item.get("id") == self.question_id), None)
        if not isinstance(question, dict):
            raise ValueError(f"unknown RAG question: {self.question_id}")
        target = context.workspace / source.name
        shutil.copyfile(source, target)
        return {
            "reference": target,
            # Function Router runs outside the benchmark's temporary-directory
            # security boundary on Windows.  Ingest the immutable, repository
            # fixture so the Router can read it; the native case still receives
            # the isolated copy used for workspace mutation checks.
            "ingest_reference": source.resolve(),
            "question": question,
        }

    def execute(self, adapter: OpenClawGatewayAdapter, context: CaseContext, fixture: dict[str, Any]) -> CaseOutcome:
        question = fixture["question"]
        turns: list[RunResult] = []
        # ContextFlow RAG is deliberately session-isolated: copying a document
        # into a directory is not equivalent to making it searchable. Prepare
        # its local index through the same Gateway/Router path before timing the
        # grounded question. Native OpenClaw reads the same file directly.
        if context.adapter_name == "contextflow_openclaw":
            ingest_reference = fixture.get("ingest_reference", fixture["reference"])
            ingestion = adapter.run(BenchmarkRequest(
                case_id=f"{self.case_id}:{self.question_id}:ingest",
                prompt=(
                    f'请将本地文件 "{ingest_reference}" 导入当前会话本地知识库；'
                    "文档 ID 使用 ab-rag-reference。导入完成后只简要确认。"
                ),
                session_key=context.session_key,
                system_prompt="Prepare the current session local knowledge only. Do not use the network or modify the source file.",
            ))
            turns.append(ingestion)
        gateway_reference = fixture.get("ingest_reference", fixture["reference"])
        prompt = (
            f'请只使用本地文件 "{gateway_reference}" 回答：{question["question"]}\n'
            "先读取该文件，不要使用网络或修改文件。回答必须包含依据，并以 [Source: 文件名] 标出来源；"
            "若文件没有足够证据，请明确说明证据不足，不要补充外部知识。"
        )
        result = adapter.run(BenchmarkRequest(
            case_id=f"{self.case_id}:{self.question_id}",
            prompt=prompt,
            session_key=context.session_key,
            system_prompt="You are running a benchmark. Ground every claim in the supplied local file only.",
        ))
        turns.append(result)
        return CaseOutcome(turns=turns, details={
            "knowledge_prepared": context.adapter_name == "contextflow_openclaw",
            "preparation_latency_ms": sum(turn.latency_ms for turn in turns[:-1]),
            "question_latency_ms": result.latency_ms,
        })

    def verify(self, context: CaseContext, fixture: dict[str, Any], outcome: CaseOutcome, file_delta: FileDelta) -> Evaluation:
        question = fixture["question"]
        answer = answer_terms(
            outcome.final_answer,
            required=(*question["required_claims"], question["required_source"]),
        )
        failures = list(answer.failures)
        lowered = outcome.final_answer.casefold()
        alternative_groups = question.get("required_any_of", [])
        if not isinstance(alternative_groups, list):
            alternative_groups = []
        missing_groups = [
            group for group in alternative_groups
            if isinstance(group, list) and not any(isinstance(term, str) and term.casefold() in lowered for term in group)
        ]
        failures.extend(f"missing required claim group: {' / '.join(group)}" for group in missing_groups)
        if not outcome.success:
            failures.append("gateway request failed")
        if file_delta.change_count:
            failures.append("RAG question changed the reference workspace")
        return Evaluation.from_failures(
            failures,
            question_id=question["id"],
            required_claim_count=len(question["required_claims"]),
            required_claim_group_count=len(alternative_groups),
            missing_claim_group_count=len(missing_groups),
            answer_term_verified=answer.passed,
            workspace_unchanged=file_delta.change_count == 0,
        )
