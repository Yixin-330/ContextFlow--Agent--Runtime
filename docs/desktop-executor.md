# DesktopExecutorAgent

`DesktopExecutorAgent` is ContextFlow's safe local-file execution vertical. It demonstrates a complete closed loop rather than direct desktop automation: find files, create a deterministic archive plan, obtain a user decision, execute the approved move, and undo that recorded move if needed.

## Safety contract

- `desktop_search_files` is read-only. It searches only inside the caller's explicit `root_path` and returns relative paths.
- `desktop_plan_archive` accepts only those relative paths under the same root. It creates a plan in `awaiting_confirmation`; no file changes at this stage.
- `desktop_confirm_archive` persists an explicit `approved` or `rejected` decision. Rejected plans cannot be executed.
- `desktop_execute_archive` accepts only approved plans. It moves regular files into one local archive folder, never deletes them, and creates an operation log.
- `desktop_undo_archive` restores every moved file to its original path. It stops if an original path is now occupied, avoiding silent overwrite.
- Traversal outside `root_path`, absolute archive folders, duplicate sources, missing files, and archive-folder re-entry are rejected before a move is attempted. If a multi-file move fails, files already moved in that attempt are restored.

The plan and operation log are session-isolated in ContextFlow SQLite. This is deliberately a local, review-first MVP; it does not operate system settings, run shell commands, delete files, or upload material.

## Demonstration sequence

```powershell
# 1) Read-only discovery.
'{"session_key":"desktop-demo","root_path":".\\ContextFlow\\demo_desktop","query":"notes"}' |
  python .\ContextFlow\service\contextflow_cli.py desktop_search_files

# 2) Return the exact relative path from the search results and make a plan.
'{"session_key":"desktop-demo","root_path":".\\ContextFlow\\demo_desktop","relative_paths":["meeting-notes.txt"]}' |
  python .\ContextFlow\service\contextflow_cli.py desktop_plan_archive

# 3) After showing the proposed source and destination to the user, approve it.
'{"session_key":"desktop-demo","plan_id":"<plan_id>","decision":"approved"}' |
  python .\ContextFlow\service\contextflow_cli.py desktop_confirm_archive

# 4) Execute only that approved plan, then retain the operation_id for reversal.
'{"session_key":"desktop-demo","plan_id":"<plan_id>"}' |
  python .\ContextFlow\service\contextflow_cli.py desktop_execute_archive

# 5) Demonstrate reversibility.
'{"session_key":"desktop-demo","operation_id":"<operation_id>"}' |
  python .\ContextFlow\service\contextflow_cli.py desktop_undo_archive
```
