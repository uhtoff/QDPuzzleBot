"""Probe live Sheets behaviour for the feeder matrix tab's checkbox columns.

Nothing in this codebase has ever set a checkbox or any data validation, so the
whole tick mechanism of the feeder matrix rests on `columnType: "BOOLEAN"` in an
`addTable` request's `columnProperties`. That field exists in the pinned client's
discovery doc, but "exists in the schema" and "does what we need" are different
claims, and every Sheets test in this repo asserts request SHAPE against mocks —
so the suite cannot settle any of this.

Four questions, each of which changes the design of the sync if the answer differs
from the assumption:

  Q1  Does addTable accept columnProperties with columnType BOOLEAN on a range
      spanning ONLY the header row? (Both existing table-creation paths build a
      table that way, so this is the shape we would copy.)
  Q2  When the table grows via updateTable {"fields": "range"}, does the BOOLEAN
      column type apply to the NEW rows? (reconcile_table grows tables exactly so;
      if the type does not propagate, every render would have to re-apply it.)
  Q3  What does a TICKED box read back as through values().get? The plan assumes
      the string "TRUE" (FORMATTED_VALUE), not a bool.
  Q4  What does an ALL-UNTICKED column read back as — blank strings, "FALSE", or
      is `values` omitted from the response entirely? parse_ticks must cope.

Run from anywhere, with the bot's venv:

    pipenv run python scripts/diagnose_feeder_matrix.py <SPREADSHEET_ID> [--keep]

Creates throwaway tabs prefixed "DIAG-FM", prints PASS/FAIL and the raw read-back
values per case, and deletes the tabs again unless --keep is given. No bot state
and no existing tab is touched.
"""
import os
import sys

# Runnable from anywhere: put the repo root on sys.path (so `import bot` works) and
# chdir there (credentials load from the relative path google_secrets.json).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

from bot.utils.gsheets import get_sheet, execute_sync  # noqa: E402

TAB = "DIAG-FM"
# Mirrors the real matrix: 3 bot columns, a solver note, then id-bearing tick columns.
HEADER = ["key", "item", "Len", "note", "main #100", "Mini 1 #101"]
TICK_COLS = (4, 5)


def _batch(ssid, requests):
    return execute_sync(get_sheet().batchUpdate(
        spreadsheetId=ssid, body={"requests": requests}))


def _new_tab(ssid, title):
    res = _batch(ssid, [{"addSheet": {"properties": {"title": title}}}])
    return res["replies"][0]["addSheet"]["properties"]["sheetId"]


def _drop_tab(ssid, page_id):
    _batch(ssid, [{"deleteSheet": {"sheetId": page_id}}])


def _write(ssid, a1, rows):
    execute_sync(get_sheet().values().update(
        spreadsheetId=ssid, range=a1,
        valueInputOption="USER_ENTERED", body={"values": rows}))


def _read(ssid, a1):
    return execute_sync(get_sheet().values().get(spreadsheetId=ssid, range=a1))


def _tables(ssid, page_id):
    meta = execute_sync(get_sheet().get(spreadsheetId=ssid))
    for s in meta["sheets"]:
        if s["properties"]["sheetId"] == page_id:
            return s.get("tables") or []
    return []


def main(ssid, keep):
    page_id = _new_tab(ssid, TAB)
    print(f"created tab {TAB} (sheetId={page_id})\n")
    try:
        # --- Q1: addTable with BOOLEAN columnProperties over a header-only range ---
        _write(ssid, f"'{TAB}'!A1", [HEADER])
        header_range = {
            "sheetId": page_id,
            "startRowIndex": 0, "endRowIndex": 1,
            "startColumnIndex": 0, "endColumnIndex": len(HEADER),
        }
        request = {"addTable": {"table": {
            "name": f"Feeder Matrix {page_id}",
            "range": header_range,
            "columnProperties": [
                {"columnIndex": i, "columnName": HEADER[i],
                 **({"columnType": "BOOLEAN"} if i in TICK_COLS else {})}
                for i in range(len(HEADER))
            ],
        }}}
        try:
            res = _batch(ssid, [request])
            table_id = res["replies"][0]["addTable"]["table"]["tableId"]
            print("Q1 addTable + columnType BOOLEAN on a header-only range: PASS")
            print(f"   tableId={table_id}")
        except Exception as exc:
            print("Q1 addTable + columnType BOOLEAN on a header-only range: FAIL")
            print(f"   {type(exc).__name__}: {str(exc)[:400]}")
            print("\n   -> The design cannot use columnType at creation. Next thing to try "
                  "is a separate setDataValidation over the tick range.")
            return

        got = _tables(ssid, page_id)
        print(f"   tables on tab after create: {len(got)}")
        for t in got:
            print(f"   columnProperties: {t.get('columnProperties')}")

        # --- Q2: does the type propagate to rows added by updateTable ---
        _write(ssid, f"'{TAB}'!A2", [
            ["f:12", "ALBATROSS", 9, "fits SEA", "", ""],
            ["f:13", "MARZIPAN", 8, "", "", ""],
        ])
        grown = dict(header_range, endRowIndex=3)
        _batch(ssid, [{"updateTable": {
            "table": {"tableId": table_id, "range": grown}, "fields": "range"}}])
        after = _tables(ssid, page_id)
        props = after[0].get("columnProperties") if after else None
        booleans = [p.get("columnIndex") for p in (props or [])
                    if p.get("columnType") == "BOOLEAN"]
        ok2 = sorted(booleans) == sorted(TICK_COLS)
        print(f"\nQ2 BOOLEAN survives updateTable(fields=range): "
              f"{'PASS' if ok2 else 'FAIL'}")
        print(f"   BOOLEAN column indices after grow: {booleans} (want {list(TICK_COLS)})")

        # --- Q3: what does a TICKED box read back as ---
        _write(ssid, f"'{TAB}'!E2", [["TRUE"]])
        resp = _read(ssid, f"'{TAB}'!A2:F2")
        row = (resp.get("values") or [[]])[0]
        print("\nQ3 read-back of a TICKED cell:")
        print(f"   raw row: {row!r}")
        cell = row[4] if len(row) > 4 else "<row right-trimmed>"
        print(f"   cell E2 = {cell!r}  (type {type(cell).__name__})")
        print(f"   parse_ticks assumes str(cell).strip().upper() == 'TRUE' -> "
              f"{str(cell).strip().upper() == 'TRUE'}")

        # --- Q4: what does an ALL-UNTICKED column read back as ---
        resp = _read(ssid, f"'{TAB}'!F1:F10")
        print("\nQ4 read-back of an ALL-UNTICKED column (F):")
        print(f"   'values' key present: {'values' in resp}")
        print(f"   raw: {resp.get('values')!r}")

        # Whole-tab read, which is what the sync actually does.
        resp = _read(ssid, f"'{TAB}'!A:Z")
        print("\n   whole-tab read (what sync_feeder_claims does):")
        for i, r in enumerate(resp.get("values") or []):
            print(f"     row {i}: {r!r}")
    finally:
        if keep:
            print(f"\n--keep: leaving tab {TAB} in place")
        else:
            _drop_tab(ssid, page_id)
            print(f"\ndeleted tab {TAB}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    main(args[0], keep="--keep" in sys.argv)
