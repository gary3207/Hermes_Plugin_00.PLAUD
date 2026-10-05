

<!-- hermes-graphify:owned:start:v1 -->
## Graphify change guard
Before modifying code use mcp__graphify_guard__prepare with the session injected by the hook.
Obtain one-shot file grants before each write. For terminal/execute_code, call mcp__graphify_guard__authorize_execution
with the exact execution arguments; generic approvals do not authorize commands.
Use Graphify affected/query for impact context, work in an isolated Git worktree, run tests,
then call mcp__graphify_guard__finalize to record verification and refresh the graph. Never upload secrets or force-push.
<!-- hermes-graphify:owned:end:v1 -->
