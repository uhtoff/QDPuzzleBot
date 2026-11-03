# Staging code assessment — 2026-10-31

Review-only assessment of `staging` at commit
`e6b27bd82e5bea302ba2690ebb15072567f939bf` (`fix(invites): final-review fixes —
second pass, real causes, chunking, docs`). No production services or remote hosts were
accessed.

## Overall assessment

The codebase is in good health and substantially above typical hobby-project quality.
No critical security or data-loss issue was found. One medium-severity concurrency defect
in the new team-invite flow should be fixed before promoting that feature to live.

The strongest qualities are the extensive backend test suite, real-MySQL integration tests,
explicit lock-ordering discipline, shared service layer, web/bang-command parity, and careful
OAuth, CSRF, role-gating, and object-scoping defenses.

The principal maintainability risk is concentrated complexity: `bot/cogs/puzzles.py` is
4,523 lines, `bot/core/puzzle_service.py` is 4,095 lines, and `bot/web/actions.py` is
2,953 lines. The service alone contains 128 methods. This is architectural debt rather
than an immediate correctness finding.

## Findings

### 1. Medium — a late role assignment can send two invite DMs

`send_invites()` enables the role-change listener by calling
`HuntJsonDb.mark_invites_sent()` and then runs a second sweep:

- `bot/core/team_invites.py:91-95`

The role-change listener independently checks the invite ledger and delivers an invite:

- `bot/core/team_invites.py:223-232`

If a user receives the hub role during that window, the listener and second sweep can both:

1. observe that the user has not been invited;
2. create and DM separate single-use invites;
3. record both invites afterward.

`hunt_invites` intentionally has a non-unique `(hunt_id, user_id)` index so moderators can
reissue invites:

- `migrations/048_team_invites.sql:23-34`

The existing second-pass and listener tests exercise the paths sequentially, not concurrently:

- `tests/test_team_invites.py:183-195`
- `tests/test_team_invites_events.py:166-172`

**Recommended direction:** share an async lock keyed by `(hunt_id, user_id)` across the
second sweep and listener, and re-check `invited_user_ids()` inside the lock immediately
before delivery. This closes the in-flight race without preventing an explicit later
`!invite_player` reissue.

### 2. Medium — CI does not test or build the frontend

The GitHub Actions workflow applies migrations and runs Ruff, basedpyright, and pytest, but
has no Node installation, frontend tests, TypeScript check, or production build:

- `.github/workflows/ci.yml:68-89`

The frontend already provides the relevant scripts:

- `frontend/package.json:6-11`

Deployment deliberately treats a frontend build failure as non-fatal and serves the previous
build if one exists:

- `start.sh:41-52`

That availability choice becomes risky without a CI frontend gate: a broken or API-incompatible
SPA can merge, deployment can report success, and the server can continue serving a stale UI
against the new backend.

**Recommended direction:** add a CI job running `npm ci`, `npm test`, and `npm run build`
from `frontend/`.

### 3. Medium-low — the tested Python version conflicts with the declared runtime

The project manifest declares Python 3.12:

- `Pipfile:36-37`

CI installs and tests on Python 3.13:

- `.github/workflows/ci.yml:43-51`

The reviewed local environment was also Python 3.13.5. Consequently, the current gates do
not prove that staging remains compatible with the declared Python 3.12 production runtime;
3.13-only syntax or APIs could pass unnoticed.

**Recommended direction:** either run CI on 3.12, add a small 3.12/3.13 matrix, or deliberately
raise the declared and deployed runtime together.

### 4. Medium-low — CI bypasses the production migration path

Deployment uses the repository migration runner:

- `deploy.sh:93-98`
- `migrate.py:110-153`

CI instead pipes each SQL file directly into the MySQL CLI:

- `.github/workflows/ci.yml:68-77`

Therefore migration discovery, SQL splitting, ledger recording, and the actual deployment
entry point can regress while CI remains green. No tests directly cover `migrate.py`.

The reviewed `puzzlebot_test` ledger had no pending migrations through 048. It reports 043
and 044 as applied files missing from staging; this is the previously investigated and accepted
`matrix-spike` artifact documented in `docs/reviews/2026-08-30-comprehensive-review.md`.

**Recommended direction:** test the pure discovery/splitting behavior and exercise
`python migrate.py` against CI's disposable MySQL database, rather than maintaining a separate
raw-SQL application path.

### 5. Low — local type-check tooling is not fully reproducible

Basedpyright is installed and pinned ad hoc in CI:

- `.github/workflows/ci.yml:82-86`

It is absent from the Pipenv development dependencies:

- `Pipfile:12-17`

The source itself is type-clean, but a fresh `pipenv sync --dev` does not provide the documented
`pipenv run basedpyright bot/` command without an ambient installation.

**Recommended direction:** add the CI-pinned basedpyright version to `[dev-packages]` and install
CI tooling from the same manifest.

## Verification performed

| Check | Result |
|---|---|
| `timeout 180 pipenv run python -m pytest tests/ -q` | 2,388 passed, 31 warnings in 20.03s |
| `pipenv run ruff check` | All checks passed |
| `pipenv run basedpyright bot/` | 0 errors, 0 warnings, 0 notes |
| `npm test` | 55 passed across 10 files |
| `timeout 120 npm run build` | TypeScript and Vite production build passed |
| `pipenv run python migrate.py status` | No pending migrations through 048; known 043/044 orphan ledger entries |

The 31 backend warnings are the existing `datetime.datetime.utcnow()` deprecation family.

## Limitations and checks not performed

- The outstanding Discord manual acceptance sequence in `.ai/active/session-summary.md` was
  not run.
- No live Discord or Google Sheets API behavior was exercised.
- No browser or DOM-level component tests were available; the frontend tests cover pure helpers.
- Python 3.12 was not available in the reviewed environment.
- The shared test database carries the accepted 043/044 schema artifacts and was not rebuilt
  from an empty schema.

## Recommended order

1. Fix the team-invite delivery race before promoting slice 4 to live.
2. Add the frontend CI job.
3. Align the declared and tested Python runtime.
4. Make CI exercise `migrate.py` and declare basedpyright in the development manifest.
