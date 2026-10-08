

<!-- hermes-graphify:owned:start:v1 -->
## Graphify change guard
Load the graphify-context skill before code work. Use Graphify query/affected when available,
then verify impact against original source and callers. Missing, stale, failed or locked graphs
are advisory: continue with source searches and tests; do not require an approval MCP server
or create new permission gates. Work in an isolated Git worktree and run relevant tests.
After source changes, refresh only this project's graph with the existing Graphify operator.
Keep implementation code, tests and build configuration under source/. Keep final deliverables
at the project root. Never upload secrets, change unrelated files or force-push.
<!-- hermes-graphify:owned:end:v1 -->
