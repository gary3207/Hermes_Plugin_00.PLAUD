# Verification — v1.0.0

Validated on Windows using Hermes's managed Python 3.14 environment and the official `@plaud-ai/mcp@0.3.14` stdio runtime.

## Verified locally

- 48 offline unittest checks passed.
- Source preservation, full-translation rendering, reference-marker fallback, cache reuse, page traversal, numeric/date metadata boundaries and transport error handling.
- A single changed artifact updates while unchanged neighbors retain their bytes and modification times.
- A missing output is recreated from the render cache without another translation call.
- Existing unowned destinations and changes made during translation are protected.
- A matching installed plugin can be configured through `--runtime-only` without payload changes; mismatched/missing payloads are rejected.
- A fresh isolated Hermes home was created without copying account authentication or user configuration. Actual npm bootstrap, plugin enablement, new CLI discovery, configuration readback and real MCP initialization/tool discovery passed. No login was attempted in the isolated home.
- A live read-only repeat checked that all previously imported available artifacts were unchanged, with no model calls and no file byte or mtime changes. A new account record was imported separately, then the repeat check passed. Private account IDs, content and detailed reports are intentionally not published.
- A real artifact was replayed in a disposable private vault to verify byte-identical missing-output recovery; no user's note was deleted for this test.

## Boundaries

- Translation semantics are unreviewed; uncertain units carry `[원문참조]`. The originals remain available.
- Live PLAUD records were not edited to test changes. Changed-source behavior was exercised with explicitly synthetic offline fixtures.
- Linux/macOS and a completely new operating-system installation have not been tested. The isolated install uses the existing host's Hermes Python and Node/npm.
- A successful process exit alone is not a clean import result: inspect `failed` and `pending` in the actual execution report.
- Empty/unavailable Highlights are not fabricated.
- GitHub pinned-install verification is recorded separately after publication; this document does not claim it has already happened.
