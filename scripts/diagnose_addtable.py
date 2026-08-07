"""Bisect the contribution-table addTable 500/400 against a live spreadsheet.

Milestone D live-test issue #1: creating a meta 500s in `_add_contribution_table`
(Google returns "Internal error encountered."; an earlier build got the cleaner
400 "You can't insert a table over a merged range."). This script reproduces the
addTable request under controlled conditions to isolate the trigger.

Run from the repo root (where google_secrets.json lives), with the bot's venv:

    .venv/bin/python scripts/diagnose_addtable.py <SPREADSHEET_ID> [--keep]

It creates a few throwaway tabs (prefixed "DIAG-"), attempts addTable on each
under a different condition, prints PASS/FAIL per case, and deletes the tabs
again unless --keep is given. No bot state is touched.

Hypothesis under test: a native Table fails when its COLUMNS (A-D) contain merged
cells, even when the merges live in rows ABOVE the table. The target tab shifts
the header right by 1, so the flavour/progress merges span C..R while the table
sits in A..D -> columns C,D are shared. Case "merge_in_table_cols" vs
"merge_clear_of_table_cols" decides it.
"""
import os
import sys

# Runnable from anywhere: put the repo root on sys.path (so `import bot` works) and
# chdir there (the Sheets credentials are loaded by the relative path google_secrets.json).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

from bot.utils.gsheets import get_sheet, execute_sync  # noqa: E402

CONTRIBUTION_HEADER = ["key", "Type", "Source", "Content"]


def _new_tab(ssid, title):
    res = execute_sync(get_sheet().batchUpdate(spreadsheetId=ssid, body={
        "requests": [{"addSheet": {"properties": {"title": title}}}]}))
    return res["replies"][0]["addSheet"]["properties"]["sheetId"]


def _delete_tab(ssid, page_id):
    execute_sync(get_sheet().batchUpdate(spreadsheetId=ssid, body={
        "requests": [{"deleteSheet": {"sheetId": page_id}}]}))


def _freeze(page_id):
    return {"updateSheetProperties": {
        "properties": {"sheetId": page_id, "gridProperties": {"frozenRowCount": 3}},
        "fields": "gridProperties.frozenRowCount"}}


def _merge(page_id, r0, r1, c0, c1):
    return {"mergeCells": {"range": {"sheetId": page_id,
            "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}, "mergeType": "MERGE_ALL"}}


def _add_table(page_id, start_row=3, start_col=0):
    return {"addTable": {"table": {
        "name": "DiagContributions",
        "range": {"sheetId": page_id,
                  "startRowIndex": start_row, "endRowIndex": start_row + 1,
                  "startColumnIndex": start_col,
                  "endColumnIndex": start_col + len(CONTRIBUTION_HEADER)},
        "columnProperties": [{"columnIndex": start_col + i, "columnName": n}
                             for i, n in enumerate(CONTRIBUTION_HEADER)]}}}


# Each case: (name, [pre-requests applied first], add-table request).
def _cases(page_id):
    return [
        ("baseline (no frozen, no merges, table A4:D4)",
         [], _add_table(page_id)),
        ("frozen rows only",
         [_freeze(page_id)], _add_table(page_id)),
        ("merge_in_table_cols (flavour/progress span C..R, table A4:D4)",
         [_merge(page_id, 0, 1, 2, 8), _merge(page_id, 1, 2, 2, 17),
          _merge(page_id, 2, 3, 2, 17)], _add_table(page_id)),
        ("merge_clear_of_table_cols (merges span E..R, table A4:D4)",
         [_merge(page_id, 0, 1, 4, 8), _merge(page_id, 1, 2, 4, 17),
          _merge(page_id, 2, 3, 4, 17)], _add_table(page_id)),
        ("table to the right of merges (merges C..R, table F4:I4)",
         [_merge(page_id, 0, 1, 2, 8), _merge(page_id, 1, 2, 2, 17),
          _merge(page_id, 2, 3, 2, 17)], _add_table(page_id, start_col=5)),
        ("full repro (frozen + merges C..R, table A4:D4)",
         [_freeze(page_id), _merge(page_id, 0, 1, 2, 8),
          _merge(page_id, 1, 2, 2, 17), _merge(page_id, 2, 3, 2, 17)],
         _add_table(page_id)),
    ]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    ssid = sys.argv[1]
    keep = "--keep" in sys.argv[2:]

    results = []
    for i, (name, pre, add) in enumerate(_cases(page_id=0)):
        title = f"DIAG-{i}"
        page_id = _new_tab(ssid, title)
        # Re-point the case requests at the real page id.
        _, pre, add = _cases(page_id=page_id)[i]
        try:
            if pre:
                execute_sync(get_sheet().batchUpdate(
                    spreadsheetId=ssid, body={"requests": pre}), max_retries=0)
            execute_sync(get_sheet().batchUpdate(
                spreadsheetId=ssid, body={"requests": [add]}), max_retries=0)
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001 - we want the raw API message
            results.append((name, "FAIL", str(e).split("\n")[0][:200]))
        finally:
            if not keep:
                try:
                    _delete_tab(ssid, page_id)
                except Exception:
                    pass

    print("\n=== addTable diagnostic results ===")
    for name, verdict, detail in results:
        print(f"[{verdict}] {name}")
        if detail:
            print(f"        {detail}")
    print("\nReading the result:")
    print("- If 'merge_in_table_cols' FAILS but 'merge_clear_of_table_cols' PASSES")
    print("  -> root cause = merged cells inside the table's columns. Fix: keep the")
    print("     header merges out of columns A-D (or move the table clear of them).")
    print("- If 'frozen rows only' FAILS -> the frozen boundary is the trigger.")
    print("- If 'baseline' itself FAILS -> the Tables API is unavailable on this sheet.")


if __name__ == "__main__":
    main()
