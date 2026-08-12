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
  Q5  Q2 probed ROW growth. When the table grows a COLUMN the same way
      (updateTable {"fields": "range"} over a wider endColumnIndex), does the
      newly covered column get a columnType at all? A mini added after the tab
      was created gets its column exactly so, and if the type does not come with
      it that mini's column has no checkbox — solvers cannot tick it.
      FINDING (probed live 2026-08-12): the new column comes back with NO
      columnType — see the recorded output at the bottom of this docstring — so
      render_feeder_matrix must re-send columnProperties alongside the widen.
      Q5b confirms `"fields": "range,columnProperties"` fixes it.

Run from anywhere, with the bot's venv:

    pipenv run python scripts/diagnose_feeder_matrix.py <SPREADSHEET_ID> [--keep]

Creates throwaway tabs prefixed "DIAG-FM", prints PASS/FAIL and the raw read-back
values per case, and deletes the tabs again unless --keep is given. No bot state
and no existing tab is touched.

Q5's recorded run (scratch spreadsheet, 2026-08-12):

    Q5a BOOLEAN on a column added by updateTable(fields=range): FAIL
       BOOLEAN column indices after the widen: [4, 5] (want [4, 5, 6])
    Q5b re-sending columnProperties with fields=range,columnProperties: PASS
       BOOLEAN column indices: [4, 5, 6]
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


def case5(ssid, keep):
    """Q5: does a column the Table GAINS get the tick column's BOOLEAN type?

    Its own tab, because it needs a table whose column span grows after
    creation — the shape render_feeder_matrix produces when a mini is added to a
    Minimeta that already has a matrix tab.
    """
    tab = TAB + "5"
    page_id = _new_tab(ssid, tab)
    print(f"\n=== Q5 (tab {tab}, sheetId={page_id}) ===")
    try:
        _write(ssid, f"'{tab}'!A1", [HEADER])
        header_range = {
            "sheetId": page_id,
            "startRowIndex": 0, "endRowIndex": 1,
            "startColumnIndex": 0, "endColumnIndex": len(HEADER),
        }
        res = _batch(ssid, [{"addTable": {"table": {
            "name": f"Feeder Matrix {page_id}",
            "range": header_range,
            "columnProperties": [
                {"columnIndex": i, "columnName": HEADER[i],
                 **({"columnType": "BOOLEAN"} if i in TICK_COLS else {})}
                for i in range(len(HEADER))
            ],
        }}}])
        table_id = res["replies"][0]["addTable"]["table"]["tableId"]

        # A mini added later: its label is written into the next column, then the
        # Table is widened over it -- exactly what render_feeder_matrix does.
        grown_header = HEADER + ["Mini 2 #102"]
        new_col = len(HEADER)
        _write(ssid, f"'{tab}'!A1", [grown_header])
        _batch(ssid, [{"updateTable": {
            "table": {"tableId": table_id,
                      "range": dict(header_range, endColumnIndex=len(grown_header))},
            "fields": "range"}}])

        props = (_tables(ssid, page_id)[0] or {}).get("columnProperties") or []
        booleans = sorted(p.get("columnIndex") for p in props
                          if p.get("columnType") == "BOOLEAN")
        want = sorted(list(TICK_COLS) + [new_col])
        ok = booleans == want
        print(f"Q5a BOOLEAN on a column added by updateTable(fields=range): "
              f"{'PASS' if ok else 'FAIL'}")
        print(f"   BOOLEAN column indices after the widen: {booleans} (want {want})")
        print(f"   columnProperties: {props}")
        if ok:
            print("   -> the type comes with the widen; no behaviour change needed.")
            return

        # Q5b: the remedy -- re-send columnProperties with the widen.
        _batch(ssid, [{"updateTable": {
            "table": {
                "tableId": table_id,
                "range": dict(header_range, endColumnIndex=len(grown_header)),
                "columnProperties": [
                    {"columnIndex": i, "columnName": grown_header[i],
                     **({"columnType": "BOOLEAN"} if i >= TICK_COLS[0] else {})}
                    for i in range(len(grown_header))
                ],
            },
            "fields": "range,columnProperties"}}])
        props = (_tables(ssid, page_id)[0] or {}).get("columnProperties") or []
        booleans = sorted(p.get("columnIndex") for p in props
                          if p.get("columnType") == "BOOLEAN")
        ok = booleans == want
        print(f"\nQ5b re-sending columnProperties with fields=range,columnProperties: "
              f"{'PASS' if ok else 'FAIL'}")
        print(f"   BOOLEAN column indices: {booleans} (want {want})")
        print(f"   columnProperties: {props}")
        # Q5c: the remedy has to survive a BLANKED column too -- when a mini is
        # deleted, stable_matrix_header empties its header cell rather than
        # re-packing (so no tick is inherited), so the columnProperties we send
        # alongside a later widen contain an entry with an empty columnName.
        wider = grown_header + ["Mini 3 #103"]
        blanked_header = list(wider)
        blanked_header[TICK_COLS[1]] = ""        # Mini 1 deleted
        _write(ssid, f"'{tab}'!A1", [blanked_header])
        try:
            _batch(ssid, [{"updateTable": {
                "table": {
                    "tableId": table_id,
                    "range": dict(header_range, endColumnIndex=len(wider)),
                    "columnProperties": [
                        {"columnIndex": i, "columnName": blanked_header[i],
                         **({"columnType": "BOOLEAN"}
                            if i >= TICK_COLS[0] and blanked_header[i] else {})}
                        for i in range(len(wider))
                    ],
                },
                "fields": "range,columnProperties"}}])
            props = (_tables(ssid, page_id)[0] or {}).get("columnProperties") or []
            print("\nQ5c columnProperties containing an EMPTY columnName: PASS")
            print(f"   columnProperties: {props}")
        except Exception as exc:
            print("\nQ5c columnProperties containing an EMPTY columnName: FAIL")
            print(f"   {type(exc).__name__}: {str(exc)[:400]}")
    finally:
        if keep:
            print(f"--keep: leaving tab {tab} in place")
        else:
            _drop_tab(ssid, page_id)
            print(f"deleted tab {tab}")


def case6(ssid, keep):
    """Q6: what a native Table does with an EMPTIED header cell.

    stable_matrix_header blanks a deleted mini's header label so its ticks
    cannot be inherited by a neighbour. Q5c showed updateTable REFUSES a table
    whose header row has an empty cell ("Table header row cell must have a
    value"), so this asks the two questions that decides what to blank it TO:

      Q6a  does values.update even keep an empty header cell inside a Table,
           or does Sheets auto-name the column?
      Q6b  does a later widen (fields=range, no columnProperties at all) break
           on that empty cell?
      Q6c  is a non-empty placeholder accepted, and may TWO removed columns
           share the same placeholder name?
    """
    tab = TAB + "6"
    page_id = _new_tab(ssid, tab)
    header = ["key", "item", "Len", "note", "main #100", "Mini 1 #101", "Mini 2 #102"]
    ticks = (4, 5, 6)
    print(f"\n=== Q6 (tab {tab}, sheetId={page_id}) ===")
    try:
        _write(ssid, f"'{tab}'!A1", [header])
        header_range = {"sheetId": page_id, "startRowIndex": 0, "endRowIndex": 1,
                        "startColumnIndex": 0, "endColumnIndex": len(header)}
        res = _batch(ssid, [{"addTable": {"table": {
            "name": f"Feeder Matrix {page_id}",
            "range": header_range,
            "columnProperties": [
                {"columnIndex": i, "columnName": header[i],
                 **({"columnType": "BOOLEAN"} if i in ticks else {})}
                for i in range(len(header))
            ],
        }}}])
        table_id = res["replies"][0]["addTable"]["table"]["tableId"]

        # Q6a: blank two removed minis' header cells the way render does.
        blanked = list(header)
        blanked[5] = blanked[6] = ""
        _write(ssid, f"'{tab}'!A1", [blanked])
        back = (_read(ssid, f"'{tab}'!1:1").get("values") or [[]])[0]
        props = (_tables(ssid, page_id)[0] or {}).get("columnProperties") or []
        print(f"Q6a header row after blanking F1/G1: {back!r}")
        print(f"   columnProperties: {props}")

        # Q6b: a later widen over a table with an empty header cell.
        try:
            _batch(ssid, [{"updateTable": {
                "table": {"tableId": table_id,
                          "range": dict(header_range, endColumnIndex=len(header) + 1)},
                "fields": "range"}}])
            print("Q6b widen (fields=range) over an empty header cell: PASS")
        except Exception as exc:
            print("Q6b widen (fields=range) over an empty header cell: FAIL")
            print(f"   {type(exc).__name__}: {str(exc)[:200]}")

        # Q6c: a non-empty placeholder, DUPLICATED across both removed columns.
        placeheld = list(header) + ["Mini 3 #103"]
        placeheld[5] = placeheld[6] = "—"
        _write(ssid, f"'{tab}'!A1", [placeheld])
        try:
            _batch(ssid, [{"updateTable": {
                "table": {
                    "tableId": table_id,
                    "range": dict(header_range, endColumnIndex=len(placeheld)),
                    "columnProperties": [
                        {"columnIndex": i, "columnName": placeheld[i],
                         **({"columnType": "BOOLEAN"} if i >= ticks[0] else {})}
                        for i in range(len(placeheld))
                    ],
                },
                "fields": "range,columnProperties"}}])
            props = (_tables(ssid, page_id)[0] or {}).get("columnProperties") or []
            print("Q6c widen with a DUPLICATED placeholder name: PASS")
            print(f"   columnProperties: {props}")
        except Exception as exc:
            print("Q6c widen with a DUPLICATED placeholder name: FAIL")
            print(f"   {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        if keep:
            print(f"--keep: leaving tab {tab} in place")
        else:
            _drop_tab(ssid, page_id)
            print(f"deleted tab {tab}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    main(args[0], keep="--keep" in sys.argv)
    case5(args[0], keep="--keep" in sys.argv)
    case6(args[0], keep="--keep" in sys.argv)
