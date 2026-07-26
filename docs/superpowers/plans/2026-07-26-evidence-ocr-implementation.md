# Evidence OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit "Extract text" action, backed by `pytesseract`, to both the canvas
region editor and the upload/paste clue-logging form — it previews OCR'd text into the note
field before anything is submitted, saving nothing on its own.

**Architecture:** One new pure module `bot/core/ocr.py` (`extract_text(image_bytes) -> str`,
wraps pytesseract, no DAOs/Quart). Two new thin async routes, each calling it via
`run_in_executor`: a canvas one that reuses the existing `crop_bytes`/`rect_bounds` to compute an
on-the-fly (unsaved) crop, and a generic one that OCRs an uploaded file's raw bytes directly.
Two frontend hooks and two small UI wire-ups (a button + confirm-before-overwrite dialog) in
`RegionModal.tsx` and `EvidencePanel.tsx`.

**Tech Stack:** Python (Quart, pyvips), pytesseract + the system `tesseract` binary, TypeScript/
React (Mantine, TanStack Query).

**Spec:** `docs/superpowers/specs/2026-07-26-evidence-ocr-design.md` — read this first if anything
below is ambiguous; the spec is authoritative on *why*, this plan is authoritative on *how*.

**Prerequisite (already done, confirmed this session):** the system `tesseract` binary is
installed with English data on both the Plesk deploy host (`dnf install tesseract`, AlmaLinux —
do NOT also install `tesseract-langpack-eng`, it conflicts with files the base package already
ships) and the local dev machine (`apt install tesseract-ocr tesseract-ocr-eng`).

---

### Task 1: Add the `pytesseract` dependency

**Files:**
- Modify: `Pipfile`
- Modify: `Pipfile.lock` (regenerated, not hand-edited)

- [ ] **Step 1: Install and lock the dependency**

Run: `pipenv install pytesseract`

Expected: `Pipfile`'s `[packages]` section gains a `pytesseract = "*"` line (Pillow comes in
transitively — pytesseract depends on it). `Pipfile.lock` is regenerated.

- [ ] **Step 2: Verify the import works through the project's own environment**

Run: `pipenv run python -c "import pytesseract; print(pytesseract.get_tesseract_version())"`

Expected: prints a version like `5.5.0` (or whatever the local `tesseract` binary reports) with
no traceback. If this raises `TesseractNotFoundError`, the system `tesseract` binary described in
the Prerequisite above is missing — stop and fix that before continuing; do not paper over it in
code.

- [ ] **Step 3: Commit**

```bash
git add Pipfile Pipfile.lock
git commit -m "chore: add pytesseract dependency for evidence OCR"
```

---

### Task 2: `bot/core/ocr.py` — the pure OCR wrapper

**Files:**
- Create: `bot/core/ocr.py`
- Test: `tests/test_ocr.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ocr.py`:

```python
"""The pure OCR wrapper: image bytes in, text out. Real tesseract, no mocks --
mirrors test_image_tiler.py's convention of exercising the real engine rather
than a stand-in for it (see docs/agents/python.md on what makes a mock-heavy
test worthless here)."""
import pyvips

from bot.core import ocr


def _text_png(text: str) -> bytes:
    """A real PNG with real rendered text, via pyvips (already a project
    dependency) -- same generation technique this session's manual OCR spike
    used, and the same one test_image_tiler.py's _write_image() uses for
    build_pyramid's tests."""
    t = pyvips.Image.text(text, width=600, dpi=150)
    canvas = pyvips.Image.black(t.width + 80, t.height + 80, bands=1) + 255
    canvas = canvas.insert(t.ifthenelse(0, 255), 40, 40)
    return canvas.write_to_buffer(".png")


def test_extract_text_reads_clean_typed_text():
    png = _text_png("HYDROGEN")
    assert "HYDROGEN" in ocr.extract_text(png)


def test_extract_text_returns_empty_string_for_a_blank_image():
    blank = (pyvips.Image.black(200, 100, bands=1) + 255).write_to_buffer(".png")
    assert ocr.extract_text(blank) == ""


def test_extract_text_is_not_a_fixed_stub():
    """Two different images must produce different text -- proves this reads
    the actual pixels rather than returning some constant string regardless of
    input (the failure mode a lazily-mocked or hard-coded implementation would
    pass everything else here without catching)."""
    a = ocr.extract_text(_text_png("HYDROGEN"))
    b = ocr.extract_text(_text_png("OXYGEN"))
    assert a != b
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 60 pipenv run python -m pytest tests/test_ocr.py -q > /tmp/test_ocr_1.log 2>&1; echo "exit=$?"; cat /tmp/test_ocr_1.log`

Expected: `ModuleNotFoundError: No module named 'bot.core.ocr'` (or a collection error) — `bot/core/ocr.py` does not exist yet.

- [ ] **Step 3: Write the implementation**

Create `bot/core/ocr.py`:

```python
"""Text extraction for evidence images, via the system tesseract binary.

Pure: bytes in, text out. No DAOs, no Quart -- the async orchestration (running
this off the event loop) lives in the web layer, the same split image_tiler.py
and media_tiling.py already use.
"""
import io

import pytesseract
from PIL import Image


def extract_text(image_bytes: bytes) -> str:
    """OCR `image_bytes` (any format PIL can open) and return the text found.

    Raises on a corrupt/unreadable image or a tesseract failure -- callers are
    responsible for catching and reporting that, the same way image_tiler's
    build_pyramid raises rather than swallowing errors itself. Unlike the
    "never raises" convenience helpers elsewhere in this codebase
    (media_tiling.tile_media, bot/web/actions.py's _crop_region_media), THIS
    function's callers must NOT swallow the exception silently: the whole
    point of an OCR request is giving the user something back, so a silent
    failure here would just look broken. See the web-layer routes for where
    that reporting happens.
    """
    img = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(img).strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 60 pipenv run python -m pytest tests/test_ocr.py -q > /tmp/test_ocr_2.log 2>&1; echo "exit=$?"; cat /tmp/test_ocr_2.log`

Expected: `3 passed`

- [ ] **Step 5: Lint and typecheck**

Run: `pipenv run ruff check bot/core/ocr.py tests/test_ocr.py && pipenv run basedpyright bot/core/ocr.py`

Expected: `All checks passed!` and `0 errors, 0 warnings, 0 notes`

- [ ] **Step 6: Commit**

```bash
git add bot/core/ocr.py tests/test_ocr.py
git commit -m "feat: pure OCR text-extraction wrapper around pytesseract"
```

---

### Task 3: Canvas region OCR-preview route

**Files:**
- Modify: `bot/web/actions.py` (append after `create_puzzle_from_region`, the last function in
  the file — currently ends with `return jsonify(web_data.region_to_json(region)), 201`)
- Test: `tests/test_canvas_web.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canvas_web.py` (after the existing `TestCropRegionMedia` class, which is
currently the last thing in the file):

```python


class TestCanvasOcrPreview:
    """POST /api/canvases/<id>/ocr-preview -- extracts text from a rect without
    saving anything. Same permission bar as drawing a region (not organiser-
    gated), unlike the canvas create/delete routes."""

    async def test_extracts_text_from_the_crop(self, web):
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        with patch("bot.core.image_tiler.crop_bytes",
                   return_value=(b"PNGBYTES", 40, 30)), \
             patch("bot.core.ocr.extract_text", return_value="HYDROGEN") as extract:
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 200
        assert (await resp.get_json())["text"] == "HYDROGEN"
        extract.assert_called_once_with(b"PNGBYTES")

    async def test_malformed_shape_is_a_400(self, web):
        resp = await web.client.post(
            "/api/canvases/3/ocr-preview",
            json={"shape": {"type": "rect", "points": [[0.5, 0.1], [0.5, 0.9]]}})
        assert resp.status_code == 400

    async def test_extraction_failure_is_a_500_not_a_crash(self, web):
        """Unlike _crop_region_media, this route must NOT swallow the error --
        the whole point of the request is giving the user something back."""
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        with patch("bot.core.image_tiler.crop_bytes", side_effect=RuntimeError("boom")):
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 500
        assert (await resp.get_json())["error"] == "ocr_failed"

    async def test_nothing_is_persisted(self, web):
        web.mdb.get_by_id.return_value = SimpleNamespace(
            id=7, hunt_id=10, path="uploads/10/orig.png")
        with patch("bot.core.image_tiler.crop_bytes",
                   return_value=(b"PNGBYTES", 40, 30)), \
             patch("bot.core.ocr.extract_text", return_value="text"):
            await web.client.post("/api/canvases/3/ocr-preview", json={"shape": RECT})
        web.mdb.create.assert_not_called()

    async def test_an_archived_hunt_is_403(self, web):
        web.ctx["hunt"]["archived"] = True
        resp = await web.client.post("/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 403

    async def test_unauthenticated_is_401(self, web):
        with patch("bot.web.auth.session", {}):
            resp = await web.client.post(
                "/api/canvases/3/ocr-preview", json={"shape": RECT})
        assert resp.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 60 pipenv run python -m pytest tests/test_canvas_web.py -q -k TestCanvasOcrPreview > /tmp/test_canvas_ocr_1.log 2>&1; echo "exit=$?"; cat /tmp/test_canvas_ocr_1.log`

Expected: every test in the class fails with a 404 (route doesn't exist yet).

- [ ] **Step 3: Write the implementation**

In `bot/web/actions.py`, append after the final line of `create_puzzle_from_region`
(`return jsonify(web_data.region_to_json(region)), 201`):

```python


def _crop_and_extract_text(src, rect):
    """Compose crop + OCR in one executor hop.

    Two separate run_in_executor calls (crop, then OCR) would be two
    thread-pool round trips for one interactive click, for no benefit --
    both steps are CPU-bound and happen back to back regardless.
    """
    from bot.core.image_tiler import crop_bytes
    from bot.core.ocr import extract_text
    png, _, _ = crop_bytes(src, rect)
    return extract_text(png)


@actions_bp.route("/canvases/<int:canvas_id>/ocr-preview", methods=["POST"])
@login_required
async def ocr_canvas_region(canvas_id):
    """Extract text from a region's rect, without saving anything.

    Unlike _crop_region_media, failures here ARE surfaced to the caller: the
    whole point of this route is handing the user something back, so a silent
    failure would just look broken. Nothing is written to MediaJsonDb or
    EvidenceJsonDb here -- this is a preview, called before the region is ever
    submitted; the real (persisted) crop still only happens through the
    existing create_region/update_region/create_puzzle_from_region routes.
    """
    from bot.core.canvas_shape import ShapeRejected, rect_bounds
    from bot.core.message_media import media_root
    from bot.store import MediaJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    body = await request.get_json(silent=True) or {}
    try:
        rect = rect_bounds(body.get("shape"))
    except ShapeRejected as exc:
        return jsonify({"error": str(exc)}), 400
    media = MediaJsonDb.get_by_id(canvas_row.media_id)
    if media is None:
        return jsonify({"error": "canvas_missing_media"}), 400
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(
            None, _crop_and_extract_text, media_root() / media.path, rect)
    except Exception:
        logger.exception("OCR failed for canvas media %s", media.id)
        return jsonify({"error": "ocr_failed"}), 500
    return jsonify({"text": text})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 60 pipenv run python -m pytest tests/test_canvas_web.py -q > /tmp/test_canvas_ocr_2.log 2>&1; echo "exit=$?"; cat /tmp/test_canvas_ocr_2.log`

Expected: all tests in the file pass (the new class plus every pre-existing one — nothing here
should have disturbed the earlier crop-evidence work).

- [ ] **Step 5: Lint and typecheck**

Run: `pipenv run ruff check bot/web/actions.py tests/test_canvas_web.py && pipenv run basedpyright bot/web/actions.py`

Expected: `All checks passed!` and `0 errors, 0 warnings, 0 notes`

- [ ] **Step 6: Commit**

```bash
git add bot/web/actions.py tests/test_canvas_web.py
git commit -m "feat(web): OCR-preview a canvas region's crop before it's saved"
```

---

### Task 4: Generic (upload/paste) evidence OCR-preview route

**Files:**
- Modify: `bot/web/api.py` (insert after `upload_media`, before the `@api_bp.route("/hunts/<uid>/canvases")` route)
- Test: `tests/test_media_web.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media_web.py` (after the existing `TestMediaUpload` class, which is
currently the last thing in the file):

```python


class TestEvidenceOcrPreview:
    """POST /api/hunts/<uid>/evidence/ocr-preview -- extracts text from an
    about-to-be-logged clue image, without saving it anywhere. Reuses
    upload_media's own sniffing/size validation, on the same permission bar
    (any member who can see an unarchived hunt)."""

    @pytest.mark.asyncio
    async def test_extracts_text_from_the_upload(self, web):
        app, _, _ = web
        with patch("bot.core.ocr.extract_text", return_value="HYDROGEN") as extract:
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 200
        assert (await resp.get_json())["text"] == "HYDROGEN"
        extract.assert_called_once_with(b"\x89PNG\r\n\x1a\npayload")

    @pytest.mark.asyncio
    async def test_rejects_an_svg(self, web):
        """Same sniffing as the real upload route -- the declared content type
        is not trusted, only the magic bytes are."""
        app, _, _ = web
        resp = await app.test_client().post(
            "/api/hunts/U1/evidence/ocr-preview",
            files={"file": _file("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'>",
                                  "image/svg+xml")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_extraction_failure_is_a_500_not_a_crash(self, web):
        app, _, _ = web
        with patch("bot.core.ocr.extract_text", side_effect=RuntimeError("boom")):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        assert resp.status_code == 500
        assert (await resp.get_json())["error"] == "ocr_failed"

    @pytest.mark.asyncio
    async def test_nothing_is_persisted(self, web):
        app, mdb, _ = web
        with patch("bot.core.ocr.extract_text", return_value="text"):
            await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
            )
        mdb.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_an_archived_hunt(self, web):
        app, _, _ = web
        with patch("bot.web.api._load_visible_hunt",
                   AsyncMock(return_value=(_hunt(archived=True), {}))):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_file_is_400(self, web):
        app, _, _ = web
        resp = await app.test_client().post("/api/hunts/U1/evidence/ocr-preview")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unauthenticated_request_is_401(self, web):
        app, _, _ = web
        with patch("bot.web.auth.session", {}):
            resp = await app.test_client().post(
                "/api/hunts/U1/evidence/ocr-preview",
                files={"file": _file("crop.png", b"\x89PNG\r\n\x1a\np", "image/png")},
            )
        assert resp.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 60 pipenv run python -m pytest tests/test_media_web.py -q -k TestEvidenceOcrPreview > /tmp/test_evidence_ocr_1.log 2>&1; echo "exit=$?"; cat /tmp/test_evidence_ocr_1.log`

Expected: every test in the class fails with a 404 (route doesn't exist yet).

- [ ] **Step 3: Write the implementation**

In `bot/web/api.py`, insert after `upload_media`'s closing `}), 201` and before
`@api_bp.route("/hunts/<uid>/canvases")`:

```python


@api_bp.route("/hunts/<uid>/evidence/ocr-preview", methods=["POST"])
@login_required
async def ocr_evidence_preview(uid):
    """Extract text from an image about to be logged as evidence.

    Mirrors upload_media's sniffing/size validation, but never writes to disk
    or creates a `media` row -- this is a preview, called before "Log clue" is
    ever clicked. The real upload still only happens through upload_media,
    unchanged.
    """
    from bot.core.media_store import max_upload_bytes, sniff_image_type
    from bot.core.ocr import extract_text
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "hunt not found"}), guild_or_status
    if hunt["archived"]:
        return jsonify({"error": "hunt_archived"}), 403
    files = await request.files
    upload = files.get("file")
    if upload is None:
        return jsonify({"error": "file required"}), 400
    raw = upload.read(max_upload_bytes() + 1)
    if len(raw) > max_upload_bytes():
        return jsonify({"error": f"Image too large (limit {max_upload_bytes()} bytes)."}), 400
    if sniff_image_type(raw[:16]) is None:
        return jsonify({"error": "Not a supported image. Allowed: PNG, JPEG, GIF, WebP."}), 400
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, extract_text, raw)
    except Exception:
        logger.exception("OCR failed for an uploaded evidence preview")
        return jsonify({"error": "ocr_failed"}), 500
    return jsonify({"text": text})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 60 pipenv run python -m pytest tests/test_media_web.py -q > /tmp/test_evidence_ocr_2.log 2>&1; echo "exit=$?"; cat /tmp/test_evidence_ocr_2.log`

Expected: all tests in the file pass.

- [ ] **Step 5: Lint and typecheck**

Run: `pipenv run ruff check bot/web/api.py tests/test_media_web.py && pipenv run basedpyright bot/web/api.py`

Expected: `All checks passed!` and `0 errors, 0 warnings, 0 notes`

- [ ] **Step 6: Commit**

```bash
git add bot/web/api.py tests/test_media_web.py
git commit -m "feat(web): OCR-preview an evidence upload/paste before it's logged"
```

---

### Task 5: Frontend API hooks

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add the two mutation hooks**

In `frontend/src/api.ts`, add after `useCreatePuzzleFromRegion` (the last function in the region
hooks group):

```ts
export function useOcrCanvasRegion() {
  return useMutation({
    mutationFn: ({ canvasId, shape }: { canvasId: number; shape: RegionShape }) =>
      apiSend<{ text: string }>(`/api/canvases/${canvasId}/ocr-preview`, "POST", { shape }),
  });
}

export function useOcrEvidenceImage(huntUid: string) {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      // Not apiSend: multipart needs the browser's own boundary, same
      // reasoning as useUploadMedia above.
      const res = await fetch(`/api/hunts/${huntUid}/evidence/ocr-preview`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": csrfToken() },
        body: form,
      });
      if (res.status === 401) throw new AuthError();
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(friendlyError(detail, res.status));
      }
      return (await res.json()) as { text: string };
    },
  });
}
```

- [ ] **Step 2: Add a friendly error message for `ocr_failed`**

In `frontend/src/api.ts`'s `friendlyError` function, add a case (anywhere among the existing
`case` lines, e.g. right after the `"discord_error"` case):

```ts
    case "ocr_failed":
      return "Couldn't extract text from that image — try typing the note by hand.";
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`

Expected: no errors. (This project has no frontend test runner — typecheck is the only automated
gate for this file; see `docs/superpowers/specs/2026-07-26-evidence-ocr-design.md`'s Testing
section.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(web): add OCR-preview API hooks"
```

---

### Task 6: `RegionModal.tsx` — "Extract text" for canvas regions

**Files:**
- Modify: `frontend/src/components/RegionModal.tsx`

- [ ] **Step 1: Add state, the hook, and the confirm-before-overwrite flow**

In `frontend/src/components/RegionModal.tsx`, change the imports at the top:

```tsx
import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Divider,
  Group,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import {
  useCreatePuzzleFromRegion,
  useCreateRegion,
  useDeleteRegion,
  useOcrCanvasRegion,
  useUpdateRegion,
} from "../api";
import { ConfirmModal } from "./ConfirmModal";
import type { CanvasRegion, HuntDetail, RegionRole, RegionShape } from "../types";
```

(Only the `useOcrCanvasRegion` import and the new `ConfirmModal` import are additions — everything
else in that block is unchanged.)

Inside the component, after the existing `const createPuzzle = useCreatePuzzleFromRegion(uid);`
line, add:

```tsx
  const ocrRegion = useOcrCanvasRegion();
  const [ocrConfirmOpen, setOcrConfirmOpen] = useState(false);
  const [pendingOcrText, setPendingOcrText] = useState<string | null>(null);
```

After the `puzzleOptions`/`roundOptions` block (any point before `save`/`createAndLink`/`remove`
is fine — place it right after `const busy = ...` for locality), add:

```tsx
  // Whichever shape is currently on screen: a freshly drawn one, or an
  // existing region's. Exactly one of region/pendingShape is ever set (see
  // the Props comment above), so this is never both.
  const currentShape: RegionShape | null = pendingShape ?? region?.shape ?? null;

  const extractText = async () => {
    if (!currentShape) return;
    setErr(null);
    try {
      const { text } = await ocrRegion.mutateAsync({ canvasId, shape: currentShape });
      if (!text) {
        setErr("No text found in that region.");
        return;
      }
      if (note.trim()) {
        setPendingOcrText(text);
        setOcrConfirmOpen(true);
      } else {
        setNote(text);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not extract text.");
    }
  };
```

- [ ] **Step 2: Wire the button into the Notes field's label, and add the confirm modal**

Replace the existing `Textarea` for Notes:

```tsx
        <Textarea label="Notes" placeholder="Transcribe what the region says…"
                  description="Free text. Transcription is the usual use."
                  autosize minRows={3} maxRows={10}
                  value={note} onChange={(e) => setNote(e.currentTarget.value)} />
```

with:

```tsx
        <Textarea
          label={
            <Group justify="space-between" wrap="nowrap" gap="xs">
              <span>Notes</span>
              <Button size="xs" variant="subtle" onClick={extractText}
                      loading={ocrRegion.isPending} disabled={!currentShape}>
                Extract text
              </Button>
            </Group>
          }
          placeholder="Transcribe what the region says…"
          description="Free text. Transcription is the usual use."
          autosize minRows={3} maxRows={10}
          value={note} onChange={(e) => setNote(e.currentTarget.value)} />
```

Then, right before the closing `</Modal>` tag (after the final `</Stack>`'s matching `</Group>`,
i.e. as a sibling of the `Stack` inside `Modal`, not nested inside it), add:

```tsx
      <ConfirmModal
        opened={ocrConfirmOpen}
        onClose={() => setOcrConfirmOpen(false)}
        onConfirm={() => {
          if (pendingOcrText !== null) setNote(pendingOcrText);
          setOcrConfirmOpen(false);
        }}
        title="Replace existing note?"
        message="This will replace the current Notes text with the extracted text."
        confirmLabel="Replace"
      />
```

So the end of the file reads:

```tsx
        </Group>
      </Stack>
      <ConfirmModal
        opened={ocrConfirmOpen}
        onClose={() => setOcrConfirmOpen(false)}
        onConfirm={() => {
          if (pendingOcrText !== null) setNote(pendingOcrText);
          setOcrConfirmOpen(false);
        }}
        title="Replace existing note?"
        message="This will replace the current Notes text with the extracted text."
        confirmLabel="Replace"
      />
    </Modal>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`

Expected: no errors.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`

Expected: build succeeds with no errors.

- [ ] **Step 5: Manual check in the browser**

Run the app (see the project's `run` skill/dev-server instructions if unsure how), open a
canvas, draw a region, click "Extract text" against a crop that actually contains readable
printed text, and confirm the Notes textarea fills in. Then type something into Notes first and
click "Extract text" again — confirm the replace-confirmation dialog appears and only replaces
the note on "Replace", not on close/cancel. This project has no frontend test runner, so this
manual pass is the only check beyond typecheck/build — do not skip it.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/RegionModal.tsx
git commit -m "feat(web): add an Extract text button to the region editor"
```

---

### Task 7: `EvidencePanel.tsx` — "Extract text" for the upload/paste clue form

**Files:**
- Modify: `frontend/src/components/EvidencePanel.tsx`

- [ ] **Step 1: Add the hook, state, and the extract handler**

In `frontend/src/components/EvidencePanel.tsx`, update the import from `../api`:

```tsx
import {
  useAddEvidence,
  useDeleteEvidence,
  useEditClueNote,
  useEvidence,
  useOcrEvidenceImage,
  useUpdateEvidence,
  useUploadMedia,
} from "../api";
```

(Only `useOcrEvidenceImage` is new — `ConfirmModal` is already imported in this file.)

After the existing `const editClueNote = useEditClueNote(uid);` line, add:

```tsx
  const ocrClueImage = useOcrEvidenceImage(uid);
  const [clueOcrConfirmOpen, setClueOcrConfirmOpen] = useState(false);
  const [pendingClueOcrText, setPendingClueOcrText] = useState<string | null>(null);
```

After the existing `onCluePaste` function, add:

```tsx
  /** OCR the attached/pasted image and fill the clue note with the result.
   *
   *  Unlike RegionModal's Textarea, this note field is a single-line
   *  TextInput that submits on Enter, so newlines in the OCR result are
   *  collapsed to spaces rather than preserved -- a multi-line result would
   *  otherwise just run together invisibly in a one-line box anyway.
   */
  async function extractClueText() {
    if (!clueFile) return;
    try {
      const { text } = await ocrClueImage.mutateAsync(clueFile);
      const collapsed = text.replace(/\s+/g, " ").trim();
      if (!collapsed) {
        notifyError(new Error("No text found in that image."));
        return;
      }
      if (clueNote.trim()) {
        setPendingClueOcrText(collapsed);
        setClueOcrConfirmOpen(true);
      } else {
        setClueNote(collapsed);
      }
    } catch (err) {
      notifyError(err);
    }
  }
```

- [ ] **Step 2: Add the button and the confirm modal to the render**

In the clue-logging `Group` (containing the Source `Select`, the note `TextInput`, the `FileInput`,
and the "Log clue" `Button`), add a new button between the `FileInput` and the "Log clue" button:

```tsx
          <FileInput
            size="xs"
            placeholder="Image (or paste)"
            accept="image/png,image/jpeg,image/gif,image/webp"
            value={clueFile}
            onChange={setClueFile}
            clearable
            w={180}
          />
          <Button
            size="xs"
            variant="light"
            onClick={extractClueText}
            loading={ocrClueImage.isPending}
            disabled={!clueFile}
          >
            Extract text
          </Button>
          <Button
            size="xs"
            onClick={submitClue}
            loading={uploadMedia.isPending || addEvidence.isPending}
          >
            Log clue
          </Button>
```

`EvidencePanel`'s `return` is a single `<Stack gap="sm">...</Stack>` (no fragment), currently
ending like this (the last few lines of the function, right before the file's final two
top-level component definitions):

```tsx
      )}
    </Stack>
  );
}
```

Modals render via a portal regardless of where they sit in the tree, so add the new
`ConfirmModal` as one more child inside that `Stack`, right before its closing tag:

```tsx
      )}
      <ConfirmModal
        opened={clueOcrConfirmOpen}
        onClose={() => setClueOcrConfirmOpen(false)}
        onConfirm={() => {
          if (pendingClueOcrText !== null) setClueNote(pendingClueOcrText);
          setClueOcrConfirmOpen(false);
        }}
        title="Replace existing note?"
        message="This will replace the current note text with the extracted text."
        confirmLabel="Replace"
      />
    </Stack>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`

Expected: no errors.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`

Expected: build succeeds with no errors.

- [ ] **Step 5: Manual check in the browser**

Paste or attach an image with real printed text into the clue form, click "Extract text", confirm
the note field fills. Type a note first, attach an image, click "Extract text" again, confirm the
replace-confirmation dialog appears and behaves the same way as Task 6's. This project has no
frontend test runner — do not skip this manual pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/EvidencePanel.tsx
git commit -m "feat(web): add an Extract text button to the clue-logging form"
```

---

### Task 8: Full verification and session handoff

**Files:**
- Modify: `.ai/active/session-summary.md`

- [ ] **Step 1: Run the full backend test suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/full_suite.log 2>&1; echo "exit=$?"; tail -20 /tmp/full_suite.log`

Expected: all tests pass (baseline before this feature was 1410 passed; this plan adds roughly
3 (`test_ocr.py`) + 6 (`TestCanvasOcrPreview`) + 7 (`TestEvidenceOcrPreview`) = 16 new tests).

- [ ] **Step 2: Run ruff and basedpyright across every changed backend file**

Run:
```bash
pipenv run ruff check bot/core/ocr.py bot/web/actions.py bot/web/api.py tests/test_ocr.py tests/test_canvas_web.py tests/test_media_web.py
pipenv run basedpyright bot/core/ocr.py bot/web/actions.py bot/web/api.py
```

Expected: `All checks passed!` and `0 errors, 0 warnings, 0 notes`.

- [ ] **Step 3: Typecheck and build the frontend once more, as a whole**

Run: `cd frontend && npx tsc --noEmit && npm run build`

Expected: no errors, build succeeds.

- [ ] **Step 4: Update the session summary**

In `.ai/active/session-summary.md`, replace the "OCR — asked about, not decided or built" section
(and its heading) with a short "OCR — built" section summarising: the two new routes, the pure
`bot/core/ocr.py` module, the two frontend wire-ups, and the manual-browser-check caveat (no
frontend test runner exists). Update the `Status:` line in the header to name this as the
most-recently-completed stage.

- [ ] **Step 5: Final commit**

```bash
git add .ai/active/session-summary.md
git commit -m "docs: mark evidence OCR feature complete in the session summary"
```

---

## Verification

- Backend: `timeout 180 pipenv run python -m pytest tests/ -q` — all green.
- Lint/types: `ruff check` and `basedpyright` on every touched `.py` file — clean.
- Frontend: `npx tsc --noEmit` and `npm run build` — clean. No frontend test runner exists in
  this project (a pre-existing, documented gap — see the design spec's Testing section), so the
  two manual browser passes in Tasks 6 and 7 are not optional polish; they are the only check
  that catches a wrong tile URL, an inverted confirm, or OCR text landing in the wrong field.

## Rollback / recovery notes

Every task is its own commit, in dependency order (core module → backend routes → frontend hooks
→ frontend wiring → docs). If a later task reveals a wrong decision in an earlier one (e.g. the
`Textarea` `label` prop rejecting a `ReactNode` — verify this in Task 6 Step 3's typecheck, and if
it fails, fall back to a `Group` placed above the `Textarea` instead of inside its `label` prop),
fix forward in a new commit rather than amending; each commit here is small enough that reverting
just one is cheap if needed.
