from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS context_states (
                  session_key TEXT PRIMARY KEY,
                  state TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  evidence_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_drafts (
                  draft_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  workflow_type TEXT NOT NULL,
                  plan_version INTEGER NOT NULL DEFAULT 1,
                  status TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_key TEXT NOT NULL,
                  scenario TEXT NOT NULL,
                  context_state TEXT NOT NULL,
                  selected_agent TEXT,
                  selected_tool TEXT NOT NULL,
                  latency_ms REAL NOT NULL,
                  success INTEGER NOT NULL,
                  fallback_used INTEGER NOT NULL,
                  user_confirmation TEXT,
                  error_reason TEXT,
                  result_json TEXT,
                  recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_documents (
                  session_key TEXT NOT NULL,
                  document_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  source_name TEXT NOT NULL,
                  source_path TEXT,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(session_key, document_id)
                );
                CREATE TABLE IF NOT EXISTS rag_chunks (
                  chunk_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  document_id TEXT NOT NULL,
                  chunk_index INTEGER NOT NULL,
                  content TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_session
                  ON rag_chunks(session_key, document_id, chunk_index);
                CREATE TABLE IF NOT EXISTS data_datasets (
                  session_key TEXT NOT NULL,
                  dataset_id TEXT NOT NULL,
                  source_name TEXT NOT NULL,
                  source_path TEXT NOT NULL,
                  profile_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(session_key, dataset_id)
                );
                CREATE TABLE IF NOT EXISTS desktop_archive_plans (
                  plan_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  root_path TEXT NOT NULL,
                  archive_folder TEXT NOT NULL,
                  status TEXT NOT NULL,
                  entries_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  confirmed_at TEXT,
                  executed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS desktop_archive_operations (
                  operation_id TEXT PRIMARY KEY,
                  plan_id TEXT NOT NULL,
                  session_key TEXT NOT NULL,
                  status TEXT NOT NULL,
                  entries_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  undone_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_desktop_archive_plans_session
                  ON desktop_archive_plans(session_key, created_at);
                CREATE INDEX IF NOT EXISTS idx_desktop_archive_operations_session
                  ON desktop_archive_operations(session_key, created_at);
                CREATE TABLE IF NOT EXISTS sensed_events (
                  event_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  source TEXT NOT NULL,
                  occurred_at TEXT NOT NULL,
                  attributes_json TEXT NOT NULL,
                  received_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sensed_events_session_time
                  ON sensed_events(session_key, occurred_at DESC);
                CREATE TABLE IF NOT EXISTS state_transitions (
                  transition_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  from_state TEXT,
                  to_state TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  evidence_json TEXT NOT NULL,
                  trigger_event_id TEXT NOT NULL,
                  recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_state_transitions_session_time
                  ON state_transitions(session_key, recorded_at DESC);
                CREATE TABLE IF NOT EXISTS watcher_heartbeats (
                  watcher_id TEXT PRIMARY KEY,
                  base_session_key TEXT NOT NULL,
                  status TEXT NOT NULL,
                  meetings_path TEXT NOT NULL,
                  data_path TEXT NOT NULL,
                  interval_seconds REAL NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_watcher_heartbeats_session
                  ON watcher_heartbeats(base_session_key, updated_at DESC);
                CREATE TABLE IF NOT EXISTS demo_runs (
                  demo_id TEXT PRIMARY KEY,
                  base_session_key TEXT NOT NULL,
                  meeting_session_key TEXT NOT NULL,
                  file_path TEXT NOT NULL,
                  content_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_demo_runs_session
                  ON demo_runs(base_session_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS calendar_events (
                  provider TEXT NOT NULL,
                  account_key TEXT NOT NULL,
                  provider_event_id TEXT NOT NULL,
                  version TEXT NOT NULL,
                  title TEXT NOT NULL,
                  start_at TEXT NOT NULL,
                  end_at TEXT NOT NULL,
                  response_status TEXT NOT NULL,
                  cancelled INTEGER NOT NULL,
                  online_meeting INTEGER NOT NULL,
                  phase TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  PRIMARY KEY(provider,account_key,provider_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_calendar_events_window
                  ON calendar_events(provider,account_key,start_at,end_at);
                CREATE TABLE IF NOT EXISTS calendar_sync_state (
                  provider TEXT NOT NULL,
                  account_key TEXT NOT NULL,
                  delta_link TEXT,
                  window_start TEXT NOT NULL,
                  window_end TEXT NOT NULL,
                  baseline_complete INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  error_message TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(provider,account_key)
                );
                CREATE TABLE IF NOT EXISTS runtime_tasks (
                  task_id TEXT PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  goal TEXT NOT NULL,
                  workflow_version TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_tasks_session
                  ON runtime_tasks(session_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS runtime_executions (
                  execution_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  current_node TEXT,
                  budget_json TEXT NOT NULL,
                  checkpoint_version INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(task_id) REFERENCES runtime_tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_executions_task
                  ON runtime_executions(task_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS runtime_checkpoints (
                  checkpoint_id TEXT PRIMARY KEY,
                  execution_id TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  current_node TEXT,
                  completed_nodes_json TEXT NOT NULL,
                  pending_nodes_json TEXT NOT NULL,
                  context_snapshot_json TEXT NOT NULL,
                  tool_results_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(execution_id, version),
                  FOREIGN KEY(execution_id) REFERENCES runtime_executions(execution_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_operations (
                  operation_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  execution_id TEXT NOT NULL,
                  tool_name TEXT NOT NULL,
                  idempotency_key TEXT NOT NULL,
                  status TEXT NOT NULL,
                  request_json TEXT NOT NULL,
                  result_json TEXT,
                  error_json TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(task_id, idempotency_key),
                  FOREIGN KEY(task_id) REFERENCES runtime_tasks(task_id),
                  FOREIGN KEY(execution_id) REFERENCES runtime_executions(execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_operations_execution
                  ON runtime_operations(execution_id, created_at);
                CREATE TABLE IF NOT EXISTS runtime_approvals (
                  approval_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  execution_id TEXT NOT NULL,
                  plan_version INTEGER NOT NULL,
                  operation_ids_json TEXT NOT NULL,
                  plan_reference TEXT,
                  decision TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  decided_at TEXT,
                  FOREIGN KEY(task_id) REFERENCES runtime_tasks(task_id),
                  FOREIGN KEY(execution_id) REFERENCES runtime_executions(execution_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_traces (
                  trace_id TEXT PRIMARY KEY,
                  execution_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  recorded_at TEXT NOT NULL,
                  FOREIGN KEY(execution_id) REFERENCES runtime_executions(execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_traces_execution
                  ON runtime_traces(execution_id, recorded_at);
                """
            )
            self._ensure_column(connection, "workflow_drafts", "plan_version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "runtime_approvals", "plan_reference", "TEXT")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_state(self, state: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO context_states(session_key,state,confidence,evidence_json,updated_at)
                VALUES(:session_key,:state,:confidence,:evidence_json,:updated_at)
                ON CONFLICT(session_key) DO UPDATE SET state=excluded.state,
                  confidence=excluded.confidence,evidence_json=excluded.evidence_json,
                  updated_at=excluded.updated_at""",
                {**state, "evidence_json": json.dumps(state["evidence"], ensure_ascii=False)},
            )
            connection.commit()
        finally:
            connection.close()

    def get_state(self, session_key: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM context_states WHERE session_key=?", (session_key,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    def append_sensed_event(self, event: dict[str, Any]) -> bool:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO sensed_events(
                  event_id,session_key,event_type,source,occurred_at,attributes_json,received_at
                ) VALUES(:event_id,:session_key,:event_type,:source,:occurred_at,:attributes_json,:received_at)""",
                {**event, "attributes_json": json.dumps(event["attributes"], ensure_ascii=False)},
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def list_sensed_events(self, session_key: str, *, since: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            if since is None:
                rows = connection.execute(
                    "SELECT * FROM sensed_events WHERE session_key=? ORDER BY occurred_at DESC LIMIT ?",
                    (session_key, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM sensed_events WHERE session_key=? AND occurred_at>=?
                    ORDER BY occurred_at DESC LIMIT ?""",
                    (session_key, since, limit),
                ).fetchall()
        finally:
            connection.close()
        events = []
        for row in rows:
            event = dict(row)
            event["attributes"] = json.loads(event.pop("attributes_json"))
            events.append(event)
        return events

    def record_state_transition(self, transition: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO state_transitions(
                  transition_id,session_key,from_state,to_state,confidence,evidence_json,trigger_event_id,recorded_at
                ) VALUES(:transition_id,:session_key,:from_state,:to_state,:confidence,:evidence_json,:trigger_event_id,:recorded_at)""",
                {**transition, "evidence_json": json.dumps(transition["evidence"], ensure_ascii=False)},
            )
            connection.commit()
        finally:
            connection.close()

    def list_state_transitions(self, session_key: str, limit: int = 50) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM state_transitions WHERE session_key=? ORDER BY recorded_at DESC LIMIT ?",
                (session_key, limit),
            ).fetchall()
        finally:
            connection.close()
        transitions = []
        for row in rows:
            transition = dict(row)
            transition["evidence"] = json.loads(transition.pop("evidence_json"))
            transitions.append(transition)
        return transitions

    def save_draft(self, draft: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO workflow_drafts(draft_id,session_key,workflow_type,plan_version,status,payload_json,created_at,updated_at)
                VALUES(:draft_id,:session_key,:workflow_type,:plan_version,:status,:payload_json,:created_at,:updated_at)""",
                {**draft, "plan_version": draft.get("plan_version", 1), "payload_json": json.dumps(draft["payload"], ensure_ascii=False)},
            )
            connection.commit()
        finally:
            connection.close()

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM workflow_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def find_latest_draft(self, session_key: str, workflow_type: str) -> dict[str, Any] | None:
        """Return the latest draft of one workflow type in an isolated session."""
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM workflow_drafts
                WHERE session_key=? AND workflow_type=?
                ORDER BY created_at DESC LIMIT 1""",
                (session_key, workflow_type),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def update_draft_status(self, draft_id: str, status: str, updated_at: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE workflow_drafts SET status=?, updated_at=? WHERE draft_id=?",
                (status, updated_at, draft_id),
            )
            row = connection.execute("SELECT * FROM workflow_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def record_execution(self, event: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO execution_events(
                  session_key,scenario,context_state,selected_agent,selected_tool,latency_ms,
                  success,fallback_used,user_confirmation,error_reason,result_json,recorded_at
                ) VALUES(
                  :session_key,:scenario,:context_state,:selected_agent,:selected_tool,:latency_ms,
                  :success,:fallback_used,:user_confirmation,:error_reason,:result_json,:recorded_at
                )""",
                {
                    **event,
                    "success": int(event["success"]),
                    "fallback_used": int(event["fallback_used"]),
                    "result_json": json.dumps(event.get("result"), ensure_ascii=False),
                },
            )
            connection.commit()
        finally:
            connection.close()

    def list_execution_events(self, session_key: str, limit: int = 50) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM execution_events WHERE session_key=?
                ORDER BY event_id DESC LIMIT ?""",
                (session_key, limit),
            ).fetchall()
        finally:
            connection.close()
        events = []
        for row in rows:
            event = dict(row)
            event["success"] = bool(event["success"])
            event["fallback_used"] = bool(event["fallback_used"])
            event["result"] = json.loads(event.pop("result_json")) if event["result_json"] else None
            events.append(event)
        return events

    def latest_session_key(self) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT session_key FROM execution_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        return str(row["session_key"]) if row is not None else None

    def list_drafts(self, session_key: str, limit: int = 20) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM workflow_drafts WHERE session_key=?
                ORDER BY created_at DESC LIMIT ?""",
                (session_key, limit),
            ).fetchall()
        finally:
            connection.close()
        drafts = []
        for row in rows:
            draft = dict(row)
            draft["payload"] = json.loads(draft.pop("payload_json"))
            drafts.append(draft)
        return drafts

    def record_watcher_heartbeat(self, heartbeat: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO watcher_heartbeats(
                  watcher_id,base_session_key,status,meetings_path,data_path,interval_seconds,updated_at
                ) VALUES(:watcher_id,:base_session_key,:status,:meetings_path,:data_path,:interval_seconds,:updated_at)
                ON CONFLICT(watcher_id) DO UPDATE SET
                  base_session_key=excluded.base_session_key,status=excluded.status,
                  meetings_path=excluded.meetings_path,data_path=excluded.data_path,
                  interval_seconds=excluded.interval_seconds,updated_at=excluded.updated_at""",
                heartbeat,
            )
            connection.commit()
        finally:
            connection.close()

    def latest_watcher_heartbeat(self, base_session_key: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM watcher_heartbeats WHERE base_session_key=?
                ORDER BY updated_at DESC LIMIT 1""",
                (base_session_key,),
            ).fetchone()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    def save_demo_run(self, run: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO demo_runs(
                  demo_id,base_session_key,meeting_session_key,file_path,content_sha256,status,created_at,updated_at
                ) VALUES(:demo_id,:base_session_key,:meeting_session_key,:file_path,:content_sha256,:status,:created_at,:updated_at)""",
                run,
            )
            connection.commit()
        finally:
            connection.close()

    def latest_demo_run(self, base_session_key: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM demo_runs WHERE base_session_key=? AND status!='reset'
                ORDER BY created_at DESC LIMIT 1""",
                (base_session_key,),
            ).fetchone()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    def list_demo_runs(self, base_session_key: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM demo_runs WHERE base_session_key=? ORDER BY created_at DESC",
                (base_session_key,),
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def update_demo_run_status(self, demo_id: str, status: str, updated_at: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE demo_runs SET status=?,updated_at=? WHERE demo_id=?",
                (status, updated_at, demo_id),
            )
            connection.commit()
        finally:
            connection.close()

    def save_runtime_task(self, task: dict[str, Any]) -> None:
        self._save_runtime_record(
            """INSERT INTO runtime_tasks(task_id,session_key,goal,workflow_version,status,created_at,updated_at)
            VALUES(:task_id,:session_key,:goal,:workflow_version,:status,:created_at,:updated_at)""",
            task,
        )

    def get_runtime_task(self, task_id: str) -> dict[str, Any] | None:
        return self._runtime_row("SELECT * FROM runtime_tasks WHERE task_id=?", (task_id,))

    def update_runtime_task_status(self, task_id: str, *, status: str, updated_at: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE runtime_tasks SET status=?,updated_at=? WHERE task_id=?",
                (status, updated_at, task_id),
            )
            row = connection.execute("SELECT * FROM runtime_tasks WHERE task_id=?", (task_id,)).fetchone()
            connection.commit()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    def save_runtime_execution(self, execution: dict[str, Any]) -> None:
        self._save_runtime_record(
            """INSERT INTO runtime_executions(
              execution_id,task_id,status,current_node,budget_json,checkpoint_version,created_at,updated_at
            ) VALUES(
              :execution_id,:task_id,:status,:current_node,:budget_json,:checkpoint_version,:created_at,:updated_at
            )""",
            {**execution, "budget_json": json.dumps(execution["budget"], ensure_ascii=False)},
        )

    def get_runtime_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = self._runtime_row("SELECT * FROM runtime_executions WHERE execution_id=?", (execution_id,))
        if row is not None:
            row["budget"] = json.loads(row.pop("budget_json"))
        return row

    def update_runtime_execution(self, execution_id: str, *, status: str, current_node: str | None, checkpoint_version: int, updated_at: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute(
                """UPDATE runtime_executions SET status=?,current_node=?,checkpoint_version=?,updated_at=?
                WHERE execution_id=?""",
                (status, current_node, checkpoint_version, updated_at, execution_id),
            )
            row = connection.execute("SELECT * FROM runtime_executions WHERE execution_id=?", (execution_id,)).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["budget"] = json.loads(result.pop("budget_json"))
        return result

    def save_runtime_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        self._save_runtime_record(
            """INSERT INTO runtime_checkpoints(
              checkpoint_id,execution_id,version,current_node,completed_nodes_json,pending_nodes_json,
              context_snapshot_json,tool_results_json,created_at
            ) VALUES(
              :checkpoint_id,:execution_id,:version,:current_node,:completed_nodes_json,:pending_nodes_json,
              :context_snapshot_json,:tool_results_json,:created_at
            )""",
            {
                **checkpoint,
                "completed_nodes_json": json.dumps(checkpoint["completed_nodes"], ensure_ascii=False),
                "pending_nodes_json": json.dumps(checkpoint["pending_nodes"], ensure_ascii=False),
                "context_snapshot_json": json.dumps(checkpoint["context_snapshot"], ensure_ascii=False),
                "tool_results_json": json.dumps(checkpoint["tool_results"], ensure_ascii=False),
            },
        )

    def latest_runtime_checkpoint(self, execution_id: str) -> dict[str, Any] | None:
        row = self._runtime_row(
            "SELECT * FROM runtime_checkpoints WHERE execution_id=? ORDER BY version DESC LIMIT 1",
            (execution_id,),
        )
        if row is not None:
            for field in ("completed_nodes", "pending_nodes", "context_snapshot", "tool_results"):
                row[field] = json.loads(row.pop(f"{field}_json"))
        return row

    def begin_runtime_operation(self, operation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create an operation once per task/idempotency key and return whether it was new."""
        connection = self._connect()
        try:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO runtime_operations(
                  operation_id,task_id,execution_id,tool_name,idempotency_key,status,request_json,
                  result_json,error_json,created_at,updated_at
                ) VALUES(
                  :operation_id,:task_id,:execution_id,:tool_name,:idempotency_key,:status,:request_json,
                  NULL,NULL,:created_at,:updated_at
                )""",
                {**operation, "request_json": json.dumps(operation["request"], ensure_ascii=False)},
            )
            row = connection.execute(
                "SELECT * FROM runtime_operations WHERE task_id=? AND idempotency_key=?",
                (operation["task_id"], operation["idempotency_key"]),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        assert row is not None
        return self._decode_runtime_operation(dict(row)), cursor.rowcount == 1

    def prepare_runtime_operation(self, operation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist an immutable requested operation before it is submitted for approval."""
        connection = self._connect()
        try:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO runtime_operations(
                  operation_id,task_id,execution_id,tool_name,idempotency_key,status,request_json,
                  result_json,error_json,created_at,updated_at
                ) VALUES(
                  :operation_id,:task_id,:execution_id,:tool_name,:idempotency_key,'awaiting_approval',:request_json,
                  NULL,NULL,:created_at,:updated_at
                )""",
                {**operation, "request_json": json.dumps(operation["request"], ensure_ascii=False)},
            )
            row = connection.execute(
                "SELECT * FROM runtime_operations WHERE task_id=? AND idempotency_key=?",
                (operation["task_id"], operation["idempotency_key"]),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        assert row is not None
        return self._decode_runtime_operation(dict(row)), cursor.rowcount == 1

    def get_runtime_operation(self, operation_id: str) -> dict[str, Any] | None:
        row = self._runtime_row("SELECT * FROM runtime_operations WHERE operation_id=?", (operation_id,))
        return self._decode_runtime_operation(row) if row is not None else None

    def claim_prepared_runtime_operation(self, operation_id: str, *, updated_at: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """UPDATE runtime_operations SET status='running',updated_at=?
                WHERE operation_id=? AND status='awaiting_approval'""",
                (updated_at, operation_id),
            )
            row = connection.execute("SELECT * FROM runtime_operations WHERE operation_id=?", (operation_id,)).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is None or cursor.rowcount != 1:
            return None
        return self._decode_runtime_operation(dict(row))

    def finish_runtime_operation(self, operation_id: str, *, status: str, result: dict[str, Any] | None, error: dict[str, Any] | None, updated_at: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute(
                """UPDATE runtime_operations SET status=?,result_json=?,error_json=?,updated_at=?
                WHERE operation_id=?""",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    updated_at,
                    operation_id,
                ),
            )
            row = connection.execute("SELECT * FROM runtime_operations WHERE operation_id=?", (operation_id,)).fetchone()
            connection.commit()
        finally:
            connection.close()
        return self._decode_runtime_operation(dict(row)) if row is not None else None

    def save_runtime_approval(self, approval: dict[str, Any]) -> None:
        self._save_runtime_record(
            """INSERT INTO runtime_approvals(
              approval_id,task_id,execution_id,plan_version,operation_ids_json,plan_reference,decision,created_at,decided_at
            ) VALUES(
              :approval_id,:task_id,:execution_id,:plan_version,:operation_ids_json,:plan_reference,:decision,:created_at,:decided_at
            )""",
            {**approval, "plan_reference": approval.get("plan_reference"), "operation_ids_json": json.dumps(approval["operation_ids"], ensure_ascii=False)},
        )

    def get_runtime_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._runtime_row("SELECT * FROM runtime_approvals WHERE approval_id=?", (approval_id,))
        if row is not None:
            row["operation_ids"] = json.loads(row.pop("operation_ids_json"))
        return row

    def decide_runtime_approval(self, approval_id: str, *, decision: str, decided_at: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """UPDATE runtime_approvals SET decision=?,decided_at=?
                WHERE approval_id=? AND decision='pending'""",
                (decision, decided_at, approval_id),
            )
            row = connection.execute("SELECT * FROM runtime_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is None or cursor.rowcount != 1:
            return None
        result = dict(row)
        result["operation_ids"] = json.loads(result.pop("operation_ids_json"))
        return result

    def append_runtime_trace(self, trace: dict[str, Any]) -> None:
        self._save_runtime_record(
            """INSERT INTO runtime_traces(trace_id,execution_id,event_type,payload_json,recorded_at)
            VALUES(:trace_id,:execution_id,:event_type,:payload_json,:recorded_at)""",
            {**trace, "payload_json": json.dumps(trace["payload"], ensure_ascii=False)},
        )

    def _save_runtime_record(self, statement: str, values: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(statement, values)
            connection.commit()
        finally:
            connection.close()

    def _runtime_row(self, statement: str, values: tuple[Any, ...]) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(statement, values).fetchone()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    @staticmethod
    def _decode_runtime_operation(operation: dict[str, Any]) -> dict[str, Any]:
        operation["request"] = json.loads(operation.pop("request_json"))
        operation["result"] = json.loads(operation.pop("result_json")) if operation["result_json"] else None
        operation["error"] = json.loads(operation.pop("error_json")) if operation["error_json"] else None
        return operation

    def clear_session(self, session_key: str) -> dict[str, int]:
        """Remove one isolated session while leaving every other session untouched."""
        tables = {
            "context_states": "session_key",
            "workflow_drafts": "session_key",
            "execution_events": "session_key",
            "sensed_events": "session_key",
            "state_transitions": "session_key",
            "rag_chunks": "session_key",
            "rag_documents": "session_key",
            "data_datasets": "session_key",
            "desktop_archive_plans": "session_key",
            "desktop_archive_operations": "session_key",
        }
        connection = self._connect()
        counts: dict[str, int] = {}
        try:
            execution_rows = connection.execute(
                """SELECT execution_id FROM runtime_executions
                WHERE task_id IN (SELECT task_id FROM runtime_tasks WHERE session_key=?)""",
                (session_key,),
            ).fetchall()
            execution_ids = [row["execution_id"] for row in execution_rows]
            if execution_ids:
                placeholders = ",".join("?" for _ in execution_ids)
                for table in ("runtime_traces", "runtime_checkpoints", "runtime_approvals", "runtime_operations"):
                    cursor = connection.execute(
                        f"DELETE FROM {table} WHERE execution_id IN ({placeholders})",
                        execution_ids,
                    )
                    counts[table] = cursor.rowcount
            else:
                for table in ("runtime_traces", "runtime_checkpoints", "runtime_approvals", "runtime_operations"):
                    counts[table] = 0
            cursor = connection.execute(
                """DELETE FROM runtime_executions
                WHERE task_id IN (SELECT task_id FROM runtime_tasks WHERE session_key=?)""",
                (session_key,),
            )
            counts["runtime_executions"] = cursor.rowcount
            cursor = connection.execute("DELETE FROM runtime_tasks WHERE session_key=?", (session_key,))
            counts["runtime_tasks"] = cursor.rowcount
            for table, column in tables.items():
                cursor = connection.execute(f"DELETE FROM {table} WHERE {column}=?", (session_key,))
                counts[table] = cursor.rowcount
            connection.commit()
        finally:
            connection.close()
        return counts

    def get_calendar_event(self, provider: str, account_key: str, provider_event_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM calendar_events
                WHERE provider=? AND account_key=? AND provider_event_id=?""",
                (provider, account_key, provider_event_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["cancelled"] = bool(result["cancelled"])
        result["online_meeting"] = bool(result["online_meeting"])
        return result

    def save_calendar_event(self, event: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO calendar_events(
                  provider,account_key,provider_event_id,version,title,start_at,end_at,
                  response_status,cancelled,online_meeting,phase,last_seen_at
                ) VALUES(
                  :provider,:account_key,:provider_event_id,:version,:title,:start_at,:end_at,
                  :response_status,:cancelled,:online_meeting,:phase,:last_seen_at
                ) ON CONFLICT(provider,account_key,provider_event_id) DO UPDATE SET
                  version=excluded.version,title=excluded.title,start_at=excluded.start_at,
                  end_at=excluded.end_at,response_status=excluded.response_status,
                  cancelled=excluded.cancelled,online_meeting=excluded.online_meeting,
                  phase=excluded.phase,last_seen_at=excluded.last_seen_at""",
                {**event, "cancelled": int(event["cancelled"]), "online_meeting": int(event["online_meeting"])},
            )
            connection.commit()
        finally:
            connection.close()

    def list_calendar_events(self, provider: str, account_key: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM calendar_events WHERE provider=? AND account_key=?
                ORDER BY start_at""",
                (provider, account_key),
            ).fetchall()
        finally:
            connection.close()
        results = []
        for row in rows:
            item = dict(row)
            item["cancelled"] = bool(item["cancelled"])
            item["online_meeting"] = bool(item["online_meeting"])
            results.append(item)
        return results

    def get_calendar_sync_state(self, provider: str, account_key: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM calendar_sync_state WHERE provider=? AND account_key=?",
                (provider, account_key),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        result = dict(row)
        result["baseline_complete"] = bool(result["baseline_complete"])
        return result

    def save_calendar_sync_state(self, state: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO calendar_sync_state(
                  provider,account_key,delta_link,window_start,window_end,baseline_complete,
                  status,error_message,updated_at
                ) VALUES(
                  :provider,:account_key,:delta_link,:window_start,:window_end,:baseline_complete,
                  :status,:error_message,:updated_at
                ) ON CONFLICT(provider,account_key) DO UPDATE SET
                  delta_link=excluded.delta_link,window_start=excluded.window_start,
                  window_end=excluded.window_end,baseline_complete=excluded.baseline_complete,
                  status=excluded.status,error_message=excluded.error_message,updated_at=excluded.updated_at""",
                {**state, "baseline_complete": int(state["baseline_complete"])},
            )
            connection.commit()
        finally:
            connection.close()

    def evaluation_summary(self, session_key: str | None = None) -> dict[str, Any]:
        connection = self._connect()
        try:
            where = "WHERE session_key=?" if session_key else ""
            parameters = (session_key,) if session_key else ()
            row = connection.execute(
                f"""SELECT COUNT(*) AS total, COALESCE(AVG(latency_ms), 0) AS average_latency_ms,
                COALESCE(SUM(success), 0) AS successes, COALESCE(SUM(fallback_used), 0) AS fallbacks
                FROM execution_events {where}""",
                parameters,
            ).fetchone()
        finally:
            connection.close()
        total = int(row["total"])
        return {
            "total_events": total,
            "average_latency_ms": round(float(row["average_latency_ms"]), 3),
            "success_rate": round(int(row["successes"]) / total, 4) if total else 0.0,
            "fallback_rate": round(int(row["fallbacks"]) / total, 4) if total else 0.0,
        }

    def save_rag_document(self, session_key: str, document: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO rag_documents(session_key,document_id,title,source_name,source_path,created_at)
                VALUES(:session_key,:document_id,:title,:source_name,:source_path,:created_at)
                ON CONFLICT(session_key,document_id) DO UPDATE SET title=excluded.title,
                  source_name=excluded.source_name, source_path=excluded.source_path,
                  created_at=excluded.created_at""",
                {"session_key": session_key, **document},
            )
            connection.execute(
                "DELETE FROM rag_chunks WHERE session_key=? AND document_id=?",
                (session_key, document["document_id"]),
            )
            connection.executemany(
                """INSERT INTO rag_chunks(chunk_id,session_key,document_id,chunk_index,content,created_at)
                VALUES(:chunk_id,:session_key,:document_id,:chunk_index,:content,:created_at)""",
                [
                    {"session_key": session_key, "document_id": document["document_id"], **chunk}
                    for chunk in chunks
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def list_rag_chunks(self, session_key: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT c.chunk_id,c.document_id,c.chunk_index,c.content,d.title,d.source_name,d.source_path
                FROM rag_chunks c JOIN rag_documents d
                  ON c.session_key=d.session_key AND c.document_id=d.document_id
                WHERE c.session_key=? ORDER BY c.document_id,c.chunk_index""",
                (session_key,),
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def save_dataset(self, session_key: str, dataset: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO data_datasets(session_key,dataset_id,source_name,source_path,profile_json,created_at)
                VALUES(:session_key,:dataset_id,:source_name,:source_path,:profile_json,:created_at)
                ON CONFLICT(session_key,dataset_id) DO UPDATE SET source_name=excluded.source_name,
                  source_path=excluded.source_path, profile_json=excluded.profile_json,
                  created_at=excluded.created_at""",
                {"session_key": session_key, **dataset, "profile_json": json.dumps(dataset["profile"], ensure_ascii=False)},
            )
            connection.commit()
        finally:
            connection.close()

    def get_dataset(self, session_key: str, dataset_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM data_datasets WHERE session_key=? AND dataset_id=?",
                (session_key, dataset_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        dataset = dict(row)
        dataset["profile"] = json.loads(dataset.pop("profile_json"))
        return dataset

    def save_desktop_archive_plan(self, plan: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO desktop_archive_plans(
                  plan_id,session_key,root_path,archive_folder,status,entries_json,created_at,confirmed_at,executed_at
                ) VALUES(
                  :plan_id,:session_key,:root_path,:archive_folder,:status,:entries_json,:created_at,:confirmed_at,:executed_at
                )""",
                {**plan, "entries_json": json.dumps(plan["entries"], ensure_ascii=False)},
            )
            connection.commit()
        finally:
            connection.close()

    def get_desktop_archive_plan(self, session_key: str, plan_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM desktop_archive_plans WHERE session_key=? AND plan_id=?",
                (session_key, plan_id),
            ).fetchone()
        finally:
            connection.close()
        return self._decode_desktop_plan(row)

    def update_desktop_archive_plan(self, session_key: str, plan_id: str, *, status: str, confirmed_at: str | None = None, executed_at: str | None = None) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute(
                """UPDATE desktop_archive_plans SET status=?,
                  confirmed_at=COALESCE(?, confirmed_at), executed_at=COALESCE(?, executed_at)
                  WHERE session_key=? AND plan_id=?""",
                (status, confirmed_at, executed_at, session_key, plan_id),
            )
            row = connection.execute(
                "SELECT * FROM desktop_archive_plans WHERE session_key=? AND plan_id=?",
                (session_key, plan_id),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        return self._decode_desktop_plan(row)

    def save_desktop_archive_operation(self, operation: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO desktop_archive_operations(
                  operation_id,plan_id,session_key,status,entries_json,created_at,undone_at
                ) VALUES(
                  :operation_id,:plan_id,:session_key,:status,:entries_json,:created_at,:undone_at
                )""",
                {**operation, "entries_json": json.dumps(operation["entries"], ensure_ascii=False)},
            )
            connection.commit()
        finally:
            connection.close()

    def get_desktop_archive_operation(self, session_key: str, operation_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM desktop_archive_operations WHERE session_key=? AND operation_id=?",
                (session_key, operation_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        operation = dict(row)
        operation["entries"] = json.loads(operation.pop("entries_json"))
        return operation

    def update_desktop_archive_operation(self, session_key: str, operation_id: str, *, status: str, undone_at: str | None = None) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute(
                """UPDATE desktop_archive_operations SET status=?, undone_at=COALESCE(?, undone_at)
                  WHERE session_key=? AND operation_id=?""",
                (status, undone_at, session_key, operation_id),
            )
            row = connection.execute(
                "SELECT * FROM desktop_archive_operations WHERE session_key=? AND operation_id=?",
                (session_key, operation_id),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is None:
            return None
        operation = dict(row)
        operation["entries"] = json.loads(operation.pop("entries_json"))
        return operation

    @staticmethod
    def _decode_desktop_plan(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        plan = dict(row)
        plan["entries"] = json.loads(plan.pop("entries_json"))
        return plan
