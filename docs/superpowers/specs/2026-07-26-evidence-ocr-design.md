# OCR text extraction for evidence images

Date: 2026-07-26
Status: Design. Not yet planned or implemented.

## Goal

Two evidence-attached-image paths already exist: an informs-region's crop (built earlier this
session, see `2026-07-26-canvas-image-evidence-design.md`'s "Requested follow-up") and the
existing upload/paste flow in the clue-logging form (`EvidencePanel.tsx`). Both end with a person
typing out, by hand, whatever the image says, into a note field. OCR is a time-saving *draft* for
that note — not a replacement for a human reading the image, and not stored anywhere as ground
truth.

The user asked for this against two questions: "no idea if this is even possible" (a spike this
session confirmed it is — see below) and "would be looking for OCR as an option for cropped areas
of a big canvas or for uploaded images/pasted image" (both surfaces, not just canvas).

## Spike findings (already run this session)

`tesseract` (system binary, via `pytesseract`) is installed on both the Plesk host (root
confirmed available — see `[[plesk-root-access]]` memory) and the local dev machine. Three
synthetic tests, not real hunt content:

- Clean, upright, typed text: **perfect transcription**.
- Same text at a 5° rotation: **noticeably garbled** — lines merge, `#7` misread as `47`.
- Smaller/denser typed text, no rotation: **mostly readable, small errors** (`is` → `i=`, a
  dropped hyphen).

Conclusion: tesseract handles the easy case well and degrades on skew and small text, with no
preprocessing (deskew, upscaling, thresholding) applied. This is an acceptable risk for a
*draft note the user reviews before saving*, and the reason the design below treats OCR output as
something you can always just retype over, never as authoritative.

## Decisions (locked, from user Q&A this session)

- **Explicit trigger, not automatic.** An "Extract text" button, not something that runs on every
  attach. Avoids wasted work on non-text images (a photo of a physical prop, a diagram) and keeps
  the user in control of when it runs.
- **Warn-and-confirm before overwriting a non-empty note.** If the note field already has text
  when "Extract text" is clicked, confirm before replacing it — never silently discard something
  the user already typed.
- **Preview-before-submit, for both surfaces.** The button computes/derives the image and runs
  OCR *without saving anything* — no media row, no evidence row — and fills the note textarea.
  The user reviews/edits, then submits the region or the clue exactly as today; that submit is
  what actually creates the real crop/upload and evidence row, completely unchanged from the
  code that already exists.
- **Synchronous, in-request execution** (`run_in_executor`, not a background job with polling).
  A pytesseract pass on a small crop is sub-second — nothing like the multi-second tile-pyramid
  build the `pending → ready` polling pattern exists for. Adding job-state/polling machinery here
  would be complexity with no payoff at this size.

## Architecture

One new pure module, `bot/core/ocr.py`:

```python
def extract_text(image_bytes: bytes) -> str:
    """pytesseract wrapper. No DAOs, no Quart -- same split as image_tiler.py."""
```

Two new thin async routes, both calling into `run_in_executor`:

1. **Canvas region preview** — `POST /api/canvases/<id>/ocr-preview`, body `{"shape": {...}}`.
   Same permission bar as drawing a region (any member who can see an unarchived hunt, not
   organiser-gated — matches `create_region`). Validates the rect with the existing
   `canvas_shape.rect_bounds()`, looks up the canvas's ORIGINAL (untiled) media the same way
   `_crop_region_media` does, and crops + OCRs **in one `run_in_executor` call** (crop then
   extract composed together, to avoid two separate thread-pool round trips for one interactive
   click). Reuses the existing `image_tiler.crop_bytes()` — the same function the real,
   persisted informs-crop uses — so there is exactly one place that turns a rect into pixels.
   Nothing is saved: no media row, no evidence row.
2. **Generic evidence preview** — `POST /api/hunts/<uid>/evidence/ocr-preview`, a raw multipart
   file. Same permission bar as logging evidence (any member who can see an unarchived hunt).
   Validates the bytes with the existing `media_store.sniff_image_type()` (the same check
   `save_image_bytes` runs) but does not write anything to disk or to `media`/`evidence_items` —
   OCRs the bytes directly and returns text.

Frontend: an "Extract text" button in `RegionModal.tsx` (enabled once a rect is drawn; fills the
existing `Textarea` "Notes" field — whose placeholder, "Transcribe what the region says…", is
already exactly what this automates) and in `EvidencePanel.tsx`'s clue-logging form (enabled once
`clueFile` is set; fills the single-line `TextInput` note field). The `EvidencePanel` field is
single-line and submits on Enter — OCR output may be multi-line, so the frontend collapses
consecutive whitespace/newlines before filling that particular field, while `RegionModal`'s
`Textarea` gets the OCR text unchanged (newlines preserved, no such collapsing needed there).

## Data flow

**Canvas:** draw a rect in `RegionModal` → click "Extract text" → `POST {shape}` to the canvas
preview route → server crops the original image on the fly (not saved) and OCRs it → response
fills the Notes textarea (confirm first if non-empty) → user reviews/edits → submits the region
as today. If linked as `informs`, the REAL crop is computed again and saved as its own media row
by the existing (already-shipped) `_crop_region_media` path — recomputed, not reused, because the
preview's crop was never persisted and the two requests are independent.

**Upload/paste:** attach or paste an image in the clue form → click "Extract text" → the file's
bytes (already held client-side in `clueFile`, not yet uploaded — `EvidencePanel` defers the real
upload to submit) are POSTed to the generic preview route → server validates and OCRs, saves
nothing → response fills the note field (confirm first if non-empty) → user reviews/edits →
clicks "Log clue" as today, which is what actually uploads the file and creates the evidence row.

## Error handling

Deliberately **not** the "never raises" contract `_crop_region_media` uses — that one guards a
real evidence write and must degrade silently; this is a user-initiated action whose entire
purpose is giving the user something back, so failure has to be visible:

- A malformed rect surfaces the existing `ShapeRejected` message as a 400 (canvas route only).
- An unrecognised/corrupt image on the generic route reuses `sniff_image_type`'s existing
  rejection (400), same message `save_image_bytes` already gives on the real upload path.
- A crop or tesseract failure is logged (`logger.exception`) and returns a generic "couldn't
  extract text" error; the frontend shows this as a toast/inline message, and it must never block
  the user from typing the note by hand and submitting normally regardless.
- An empty OCR result (no text found) is **not** an error — returns `{"text": ""}`; the frontend
  shows a small "no text found" hint rather than doing nothing with no feedback.

## Testing

- `bot/core/ocr.py`: real-pytesseract tests against a real generated image, same convention as
  `test_image_tiler.py`'s `_write_image()` (via `pyvips.Image.text()`) — not mocked, since a
  mocked "returns whatever string" test proves nothing about the wrapper being correct. Include a
  case proving it's not a no-op / fixed-string stub (recognisable text comes back, not just any
  non-empty string).
- Route tests mock `bot.core.ocr.extract_text` (and `image_tiler.crop_bytes` for the canvas
  route), the same way `_crop_region_media`'s existing tests do — route tests are about wiring
  and permissions, not OCR accuracy. Cover: permission bar parity with the real write routes,
  malformed rect → 400, unrecognised file → 400, extraction failure → the generic error (not a
  500 that takes the whole request down), nothing gets written to `MediaJsonDb`/`EvidenceJsonDb`
  on any of these paths (the "preview never persists" invariant).
- No frontend test runner exists in this project (a known, pre-existing gap — see
  `session-summary.md`'s "Known gaps and risks"). The button/confirm-dialog/whitespace-collapsing
  logic is typecheck (`tsc --noEmit`) and manual-inspection only, same caveat as the rest of the
  canvas UI.

## Deployment

`pytesseract` becomes a real `Pipfile` dependency (currently only `pip install`ed ad hoc in this
session's spike, not committed). The system `tesseract` + `tesseract-langpack-eng` packages must
be present on every host that runs this code path:

- Plesk (AlmaLinux/RHEL): `dnf install tesseract tesseract-langpack-eng` — note the base
  `tesseract` package on this distro already bundles `eng.traineddata`, so installing the EPEL
  `tesseract-langpack-eng` alongside it **conflicts** (`file ... conflicts between attempted
  installs`); installing `tesseract` alone is sufficient for English. Confirmed done on the
  `dedivps-107912` host this session.
- Local dev (Debian): `apt install tesseract-ocr tesseract-ocr-eng`. Confirmed done this session.

English-only for now — no other language pack installed or asked for. Not a new external network
dependency: recognition runs entirely on the host, same offline/local character as the rest of
this feature (unlike the rejected cloud-vision-API alternative from the earlier discussion).

## Out of scope

- Any language other than English.
- Automatic/on-attach OCR (explicitly rejected above).
- Persisting OCR output anywhere other than a client-side note-field fill (no new column, no
  "ocr_text" audit trail).
- Preprocessing to improve accuracy (deskew, upscaling, binarisation) — the spike's degraded
  cases are accepted as-is for a first version; revisit only if real usage shows it is not
  "close enough to edit," not preemptively.
- No role-specific behaviour for `locates` vs `informs`. The Notes `Textarea` OCR fills belongs
  to the *region* (`CanvasRegionData.note`), not exclusively to evidence — a `locates` region has
  this field too, it just never becomes an evidence row's note the way an `informs` region's does
  (see `canvas.py`'s existing `create_region`/`update_region`). The preview route itself needs
  only a rect and the canvas's image, not a role, so it works identically either way.
