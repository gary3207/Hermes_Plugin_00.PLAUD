---
name: plaud-kanban-manual-jobs
description: Use when operating PLAUD Kanban manual Import jobs.
version: 1.1.0
author: Gary Lee (Gary-Y-Lee), Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [plaud, kanban, manual-jobs, windows]
---

# PLAUD manual Kanban jobs

## When to Use
Use when reinstalling, configuring, operating, diagnosing or extending the existing PLAUD manual Import integration with native Hermes Kanban. Do not expand this task to other scheduled jobs.

## Scope and prerequisites
- Repository: `gary3207/Hermes_Plugin_00.PLAUD` (formerly `Gary-Y-Lee/Hermes_Plugin_PLAUD`). Maintained Import engine: `source/`; Kanban connector and tests: `source/kanban/`. Read root `README.md` and `PLAUD_Kanban_사용안내.md` for installation.
- Resolve the selected Profile's actual home with `get_hermes_home()` or `HERMES_HOME`. Engine: `plugins/hermes-plaud/`; connector: `plugins/hermes-plaud-kanban/`.
- Install both repository subdirectories at the same verified commit. Bootstrap the engine runtime with the Hermes-managed Python, supplying the vault and relative output folder, and explicitly enable both plugins. PLAUD login and Hermes model authentication are separate; never import private authentication, board DB, journals or meeting caches from Git.
- This Skill is registered by the connector and resolves as `hermes-plaud-kanban:plaud-kanban-manual-jobs`; it does not require copying into user-local skills.
- Before changing code, load available `hermes-agent`, `graphify-context` and `hermes-archive-records` guidance and inspect actual source/callers. A missing or stale graph is advisory, not a new execution gate.
- Do not modify Hermes Core/Desktop/Gateway, other Profiles or scheduled jobs without explicit authorization. A package update does not authorize a live Import.

## Procedure
1. Inspect status with `terminal(command="hermes plaud status")` and `terminal(command="hermes plaud-kanban status --board default")`; verify the selected Profile, destination and active execution before maintenance.
2. On a fresh board only, use `terminal(command="hermes plaud-kanban initialize --board default")`. Verify one Scheduled waiting card and no execution. Initialization is not an Import.
3. Start only the current waiting card by moving Scheduled → Ready. Do not drag directly to Running or restart a history card.
4. Verify terminal card status, counts and artifact checks. Exit zero is not enough: inspect failed counts, outcome and output verification. Keep unavailable Highlights distinct from failed records.
5. After execution actually terminates, verify exactly one next-run waiting card and preserved history. For cancellation, native Blocked/deletion is a request, not proof of process exit.

## Operating contract
- The engine's registered `plaud_translation` task inherits the main model and defaults its substantive-output no-progress window to 120s (request timeout 180s). Preserve the operator's task overrides and existing 12000-character chunk/cache identity. On transient translation transport failure it re-enters the host facade once; do not add nested job reruns or alter global/Core timeouts.
- Exhausted transient recovery preserves source and available translated chunks, marks missing translations `[원문참조]`, and writes `plaud_translation: "incomplete-transport"`. The JOB remains failed/partial. These notes bypass unchanged on the next manual run and must not enter successful translation/render caches. Safe attempt metadata is in private `hermes-plaud/translation-events.jsonl`; do not log meeting content or arbitrary errors.
- One Scheduled `[실행 대기] PLAUD Import` card without a wake-up date or timer. Manual Ready movement starts execution. `plaud-job` is a non-profile assignee lane, not a new Agent Profile.
- One immutable card per execution. Success → Done; failure, partial failure and cancellation → Blocked. Preserve successfully written notes; never roll them back to make a batch atomic.
- Running → Blocked/deletion requests owned process-tree cancellation. Regenerate only after owned-process exit confirmation.
- Publish card body only on phase/error/cancellation/final transitions. Heartbeats prove liveness, not progress. Do not add periodic public-body updates.
- Put full stage tables, counts, paths and verification in Description; keep Result and native run Summary concise and explicitly separate. Display elapsed durations in minutes/seconds. Show all four stages and measured 3.x record-operation totals; label historical unmeasured work `미보고 / 기록 없음`, never infer durations. Child totals are included in the parent and are not extra sequential stages.
- Show the actual plan in order: preparation and existing-note index → complete inventory → per-record source fetch → unchanged/incomplete/integrity comparison → skip unchanged or inspect render cache and translate only necessary units → safe save → final reindex and artifact verification. This is a conditional per-record plan, not proof that every child executed. Preserve measured fetch/translation totals and label all unmeasured children `미보고 / 기록 없음`; do not invent child durations.
- Match Scheduled Job date display in titles and start/end fields (`2026년 10월 22일 오전 9시 1분 53초` in the host's local timezone); retain raw ISO timestamps and measured durations in private evidence. Missing Highlights are normal optional-source absence: show the unique-record count as `정상 생략·실패 아님`, separately from real errors and other unavailable source types. Do not reclassify translation timeouts, failed source requests or artifact corruption as normal omissions. When older reports have only an aggregate pending count, do not assume its source types; enrich only from a matching retained engine report, otherwise label them unrecorded. Historical display refresh changes title/body only under existing ownership/run fences with a durable private backup; never replay completion or Import.
- Preserve original Transcript speaker paragraphs, Summary and Highlights. Add full Korean translations, not re-summaries. Uncertain translation units use exactly `[원문참조]`; preserve available wording and original source. Structural IDs, ownership and destination checks remain strict.

## Verified implementation lessons
- Preserve record IDs literally. The verified account returned both `of_` and `f_` followed by exactly 32 hexadecimal characters; historical unprefixed 32-hex IDs remain supported. Use the engine's shared RECORD_ID_PATTERN across provider, transport, storage and existing-note discovery, not prefix replacement, trimming, case conversion or skipping records. Validate new prefixes against actual source/SDK evidence rather than accepting arbitrary strings. Test real provider/transport through synthetic save and no-change repeat, legacy filename recognition, malformed rejection and safe local validation diagnostics. Authentication/list access alone does not prove full provider inventory acceptance.
- Native create_task only accepts running/blocked initial_status. Create sticky Blocked then schedule_task; preserve pending idempotency identity across both. Omit reason for an unexecuted wait to avoid a synthetic run. Re-park only the journal-owned waiting role with ownership, Blocked status and no current_run_id fenced in the same transaction; never rewrite Ready/Running/history.
- Fence ownership and current_run_id in the same DB write transaction as progress publication. Separate check/edit transactions can overwrite later executions.
- Recover claim-before-journal, terminal-before-history and clear-before-next-wait crash windows through the normal dispatch hook, including missing/deleted waiting cards.
- Saved success is provisional until native transition evidence agrees. Operator cancellation must not become Done from a pre-written success file; recovered completion must publish title/body/history/retention.
- Reject nonzero worker exit even with a success result file. Validate engine failures and output hashes.
- Pass an explicit short summary and result to complete_task; its default summary mirrors result. Do not replay completion to clean historical display fields.
- Preserve persisted counts and active-phase elapsed time on cancellation, even without a final worker report. Copy phase snapshots before later mutation.
- Windows atomic os.replace can transiently raise PermissionError while another reader holds the target. Use bounded retries and retain the original failure on exhaustion; never fall back to unsafe in-place JSON overwrite.

## Change and deployment verification
- Work in an isolated Git worktree. Run `terminal` with the managed Hermes Python and `PYTHONPATH=source` for `-B -m unittest discover -s source/tests -v`; run `PYTHONPATH=source/kanban` with `-B -m unittest discover -s source/kanban -p "test_*.py" -v` from the repository root. Fixtures must use disposable homes/boards under `source/kanban/test-sandboxes`, never the actual account.
- Native plugin manifests use lists for provides_tools/provides_hooks; declare on_kanban_dispatch_tick. Validate/doctor the explicit staged payload; do not bypass the native scanner or scan unrelated worktrees as if they were the payload.
- Connector production payload is plugin.yaml, __init__.py, plaud_kanban.py and the bundled Skill. Keep tests/fixtures in maintained source, not a manual production deployment.
- Before replacing an enabled live connector, acquire existing controller/job locks and verify no owned execution is active. Never cancel a user-started job to deploy. If the user cancels, confirm controller/worker PID-plus-birth exit; journal active=None alone is insufficient.
- Enable through native plugins CLI. Read back enablement, exact payload bytes, native qualified Skill resolution and the target board/card. Verify fresh Gateway hook loading separately; enable/reload output is not live-hook proof.
- For reinstall verification use `source/verify_install.py --repo gary3207/Hermes_Plugin_00.PLAUD/source --revision <exact-commit> --kanban` under the managed Python with `PYTHONPATH=source`. It uses disposable homes and no copied authentication. Same-host isolation is not a fresh OS test.
- Leave actual PLAUD login/Import unexecuted unless authorized. Distinguish offline tests, MCP handshake, waiting-card readback and real account/UI validation.
- Refresh only this project's Graphify graph after source changes; report advisory errors separately. Record actual checks in authored project notes, never credentials or meeting/translation bodies.

## Pitfalls
For `PLAUD_AUTH_REQUIRED`, help the user log in rather than rerunning Import. Run `hermes -p <profile> plaud login --aside` as a background terminal process with completion notification. It emits an `awaiting_user` JSON event containing an authorization URL and waits up to 10 minutes. Open that exact URL with `desktop_preview(action='open', url=..., label='PLAUD 재로그인')`; use the user's own login/Authorize actions. Do not type or collect passwords/codes, copy tokens, or edit the official package. The helper reuses the pinned official SDK's PKCE, loopback callback on port 8199, exchange and existing isolated auth store; an unsupported package version/layout fails closed. Do not start a second pending login; on timeout, explain and start a new attempt when the user is ready. Verify `authenticated` output AND a read-only `list_files` call before reporting recovery. Login is not permission to run Import, rewrite failed history, or move the next waiting card.

Read `events.jsonl` cause `detail` and result/summary `errors` for safe code, tool, message, HTTP status and authentication/retry hints. Known pinned MCP error formats map to constant explanations; unknown raw error bodies stay private and are not logged. Do not claim token expiry/deletion from a generic authentication failure. Official MCP documentation covers login and optional `list_files` filters; keep the complete unfiltered Import inventory and existing credential/translation boundaries. Package latest-version lookup failure is not a reason to disable TLS or upgrade blindly.

On npm `UNABLE_TO_VERIFY_LEAF_SIGNATURE`, diagnose certificate trust without disabling TLS validation. A command-scoped `npm_config_offline=true` bootstrap can use an already complete cached fixed package/dependency set; label that as cached same-host installation, not fresh-machine online download. A timed-out bootstrap can leave its npm child behind: identify and stop only verification-owned processes before retrying, never unrelated or live business jobs.

`tick` and `reconcile` may start Ready work or perform recovery; use `status` for read-only inspection. Windows connector behavior is tested; Linux/macOS execution is not supported as verified. Git restores functionality and instructions, not private history, caches, notes or authentication. Consult current test output rather than treating historic counts or card IDs as permanent guarantees.
