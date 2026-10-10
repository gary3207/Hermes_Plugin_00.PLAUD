# Verification

## 2026-10-10 repository synchronization: optional Kanban companion

- The Import engine's production payload remains identical to v1.0.1. The separate `source/kanban` production payload matches the currently installed manual Kanban connector; it does not fork the business engine.
- Reran 53 engine offline tests and 33 companion offline tests on this Windows host using Hermes's managed Python. All passed. Companion tests use the checked-in engine, not a personal installed-plugin path, and disposable native boards/vaults with synthetic workers.
- Companion Plugin Doctor passed manifest/import/registration with one dispatch hook and zero tools.
- The source ZIP builder includes the guide and explicitly allowlisted companion source/tests. ZIP extraction/content readback passed; authenticated data, board state, journals, test sandboxes, private evidence and credentials are excluded.
- This synchronization does not perform a live PLAUD Import, alter the existing board, change other scheduled jobs or replace the live plugin. Previous v1.0.0/v1.0.1 releases are preserved. A repository installation that includes the companion requires both subdirectory installs.
- For exact-commit reinstall verification use `source/verify_install.py --repo Gary-Y-Lee/Hermes_Plugin_PLAUD/source --revision <40-character SHA> --kanban` under Hermes's managed Python with `PYTHONPATH=source`. It installs both plugins in a disposable home, bootstraps the real npm/MCP runtime without login, verifies payload bytes and CLI discovery, and checks that an initialized Scheduled card does not start. It never copies the current account's authentication or private state.

## v1.0.1 readable filenames

- 53 offline tests pass, including readable date/time/title/kind naming, lossless legacy rename, unchanged repeat without translation, missing-note cache recovery, changed title rename without duplicates, forbidden characters/length limits, unrelated-note collision protection, duplicate ownership rejection and backed-up offline migration.
- Filename-only migration leaves original note bytes, translation markers and modification times intact. Full source titles/IDs remain in the note. No guessed timezone conversion is applied.
- Actual owned notes were migrated with a verified private ZIP backup, then read back byte-for-byte with unchanged modification times and translation markers. No PLAUD or model call was needed for the rename operation.
- The post-migration live repeat passed with no model calls, no content/mtime changes and no import errors. Missing-file recovery was replayed in a disposable private vault and remained byte-identical.
- Independent pre-publication review found no introduced blocking security or logic issues and reran all 53 offline tests successfully. The actual v1.0.1 ZIP was extracted and its isolated bootstrap, enablement, fresh CLI discovery, configuration and real MCP handshake passed without copying authentication or modifying the existing Profile.

## v1.0.0 baseline verification

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
- Independent pre-publication review found no blocking security or logic issues and reran all 48 offline tests successfully.
- The actual delivery ZIP was extracted and its local bootstrap was exercised successfully in a fresh isolated Hermes home.
- GitHub pinned install from `Gary-Y-Lee/Hermes_Plugin_PLAUD/source` at `d7322ca12a0bed1b35af6242cc0c8a7db26fc692` passed in a fresh isolated Hermes home: downloaded payload readback (7 files), runtime-only bootstrap, plugin enablement, fresh CLI discovery, configuration and real MCP initialization/tool discovery. No account login was attempted in that isolated home.

## Boundaries

- Translation semantics are unreviewed; uncertain units carry `[원문참조]`. The originals remain available.
- Live PLAUD records were not edited to test changes. Changed-source behavior was exercised with explicitly synthetic offline fixtures.
- Linux/macOS and a completely new operating-system installation have not been tested. The isolated install uses the existing host's Hermes Python and Node/npm.
- A successful process exit alone is not a clean import result: inspect `failed` and `pending` in the actual execution report.
- Empty/unavailable Highlights are not fabricated.
- GitHub verification proves installation of the published plugin on the existing Windows host, not a fresh OS install or transfer of PLAUD/model authentication. New installations still need their own credentials.
