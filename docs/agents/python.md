<!-- GENERATED FILE — DO NOT EDIT.
     Source: ~/.config/agent-instructions/layers/python.md
     Rebuild: ~/.config/agent-instructions/build.sh sync
     Edits made here are silently lost on the next sync. -->

# Python house rules

Rules and known failure modes for Python projects (pytest, unittest.mock, ruff).
The project's own instructions pin the specifics — the exact test command, patch
targets, fixtures/factories. If a rule here conflicts with the project's
instructions, the project wins.

## Running tests (pytest)

- Run pytest through the project's environment manager as pinned by the project
  (e.g. `pipenv run python -m pytest`, `poetry run pytest`, `.venv/bin/pytest`).
  Bare `pytest` may not be on PATH, and `python -m pytest` is what puts the repo
  root on `sys.path` — a bare entrypoint can make first-party imports fail.
- Wrap every run in `timeout` and write output to a file, as a single statement:
  `timeout 180 <runner> -m pytest tests/ -q > /tmp/test.log 2>&1`
  A timeout-killed run exits 124; read the log file, not the (possibly lost)
  pipe output.

## Mocking (unittest.mock)

- Patch where the name is **looked up**, not where it is defined. If
  `service.py` does `from store import Db`, patch `"service.Db"`, not
  `"store.Db"` — patching the source module leaves the service's own reference
  untouched and the mock silently never engages.
- Mis-targeting a patch on something with **side effects** does not merely fail
  the assertion — it escapes the test process. A suite patching the wrong
  module's `media_root` wrote real uploaded files into the repository checkout.
  Where the patched thing touches the filesystem, the network, or a real
  service, confirm the patch actually engages before trusting the test.
- `MagicMock().name` is a Mock, not a string — `Mock(name=...)` sets the mock's
  repr, not the attribute. Set `.name` (and `.id`, `.position`, etc.) explicitly
  after construction when code reads them as data.
- The same applies to **collection** attributes the code tests for truthiness.
  An unset `mock.message.attachments` auto-vivifies to a truthy Mock, so the
  "nothing attached" path silently takes the "something attached" branch and
  then fails somewhere unrelated. Set `[]` explicitly.
- Anything awaited must be an `AsyncMock`; sync attributes stay `MagicMock`.
  Assert with `assert_awaited_once()`, `assert_awaited_once_with(...)`,
  `assert_not_awaited()`.

## Linting (ruff)

- NEVER blind-apply `ruff check --fix` for F401 (unused import) on production
  code. Imports that look unused locally can be load-bearing: package
  re-exports consumed by other modules, and test patch-targets
  (`patch("pkg.module.Name")` needs `Name` importable at that site). Review each
  F401 fix on production modules; `# noqa: F401` with a reason is the correct
  outcome for a deliberate re-export.
