"""Faithfully reproduce meta-tab generation against a live sheet, then bisect.

Live-test #1: creating a meta makes the tab + header, then spins and 500s on the
contribution-table addTable — but an isolated addTable (diagnose_addtable.py)
succeeds. So something in the FULL generate_puzzle_tab pre-state is the trigger.

This runs the real generate_puzzle_tab once (faithful repro), and a set of bisect
cases that apply generate's pre-batch minus one element, then addTable, on fresh
throwaway DIAG-* tabs. Prints PASS/FAIL per case and deletes the tabs.

    .venv/bin/python scripts/diagnose_generate.py <SPREADSHEET_ID> [--keep]
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

import asyncio  # noqa: E402

from bot.cogs.puzzles_gsheet import GoogleSheets  # noqa: E402
from bot.cogs import sheet_layout  # noqa: E402
from bot.store.puzzle_data import PuzzleData  # noqa: E402
from bot.utils import config  # noqa: E402
from bot.utils.gsheets import get_sheet, execute_sync  # noqa: E402

SSID = sys.argv[1] if len(sys.argv) > 1 else None
KEEP = "--keep" in sys.argv[2:]
if not SSID:
    print(__doc__)
    sys.exit(2)


def _cog():
    cog = GoogleSheets.__new__(GoogleSheets)
    cog.set_spreadsheet_id(SSID)
    return cog


def _new_tab(title):
    res = execute_sync(get_sheet().batchUpdate(spreadsheetId=SSID, body={
        "requests": [{"addSheet": {"properties": {"title": title}}}]}))
    return res["replies"][0]["addSheet"]["properties"]["sheetId"]


def _del_tab(pid):
    try:
        execute_sync(get_sheet().batchUpdate(spreadsheetId=SSID, body={
            "requests": [{"deleteSheet": {"sheetId": pid}}]}))
    except Exception:
        pass


def _addtable(pid, header_row=3):
    return {"addTable": {"table": {
        "name": "Contributions",
        "range": {"sheetId": pid, "startRowIndex": header_row, "endRowIndex": header_row + 1,
                  "startColumnIndex": 0, "endColumnIndex": 4},
        "columnProperties": [{"columnIndex": i, "columnName": n}
                             for i, n in enumerate(["key", "Type", "Source", "Content"])]}}}


# ---- 1. Faithful repro: the real generate_puzzle_tab -----------------------
def faithful():
    cog = _cog()
    p = PuzzleData()
    p.id = 999999
    p.name = "DIAG Meta Faithful"
    p.url = "http://example.com/diag"
    p.is_target = True
    page_id = None
    try:
        page_id = asyncio.run(cog.generate_puzzle_tab(p, is_target=True))
        # Inspect where the table + header landed.
        meta = execute_sync(get_sheet().get(
            spreadsheetId=SSID, ranges=[f"'{cog.get_page_name_by_id(page_id)}'!A4:H5"],
            includeGridData=True,
            fields="sheets(tables(range,columnProperties),data(rowData(values(formattedValue))))"))
        s = meta["sheets"][0]
        tbl = (s.get("tables") or [{}])[0]
        print(f"[PASS] faithful generate_puzzle_tab  page_id={page_id}")
        print(f"        table range={tbl.get('range')}")
        rows = s.get("data", [{}])[0].get("rowData", [])
        for ri, row in enumerate(rows, start=4):
            cells = [c.get("formattedValue") for c in row.get("values", [])]
            print(f"        row {ri}: {cells}")
        return ("PASS", "")
    except Exception as e:  # noqa: BLE001
        return ("FAIL", str(e).split("\n")[0][:200])
    finally:
        if page_id is not None and not KEEP:
            _del_tab(page_id)


# ---- 2. Bisect: generate's pre-batch minus one element, then addTable ------
def _label_cells(cog, pid, offset):
    cells, _ = sheet_layout.puzzle_tab_layout(is_target=True)
    return [cog.update_cell(c["value"], cog.get_row(c["a1"]),
                            cog.get_column(c["a1"]) + offset, c["type"], pid) for c in cells]


def _merges(pid, offset):
    _, merges = sheet_layout.puzzle_tab_layout(is_target=True)
    return [{"mergeCells": {"range": {"sheetId": pid, "startRowIndex": sr, "endRowIndex": er,
            "startColumnIndex": sc + offset, "endColumnIndex": ec + offset},
            "mergeType": "MERGE_ALL"}} for (sr, er, sc, ec) in merges]


def _hyperlink(cog, pid, offset):
    return cog.update_cell('=HYPERLINK("http://x","Diag")',
                           cog.get_row(config.puzzle_cell_name),
                           cog.get_column(config.puzzle_cell_name) + offset,
                           cog.FORMULA_INPUT, pid)


def bisect_case(name, build_pre):
    cog = _cog()
    pid = _new_tab("DIAG-G-" + name[:20].replace(" ", "_"))
    try:
        pre = build_pre(cog, pid)
        if pre:
            execute_sync(get_sheet().batchUpdate(spreadsheetId=SSID,
                         body={"requests": pre}), max_retries=0)
        execute_sync(get_sheet().batchUpdate(spreadsheetId=SSID,
                     body={"requests": [_addtable(pid)]}), max_retries=0)
        return (name, "PASS", "")
    except Exception as e:  # noqa: BLE001
        return (name, "FAIL", str(e).split("\n")[0][:200])
    finally:
        if not KEEP:
            _del_tab(pid)


def main():
    print(f"config.puzzle_cell_name = {config.puzzle_cell_name!r}\n")
    print("=== faithful repro (real generate_puzzle_tab) ===")
    verdict, detail = faithful()
    if detail:
        print(f"[{verdict}] {detail}")

    off = GoogleSheets.TARGET_HEADER_SHIFT
    cases = [
        ("full pre-batch (labels+merges+hyperlink+format+bold)",
         lambda c, p: _label_cells(c, p, off) + _merges(p, off) + [_hyperlink(c, p, off)]
         + c._tab_format_requests(p, label_col=off) + c._label_bold_requests(p,
             sheet_layout.puzzle_tab_layout(is_target=True)[0], off)),
        ("minus whole-sheet font (format without the sheet-wide repeatCell)",
         lambda c, p: _label_cells(c, p, off) + _merges(p, off) + [_hyperlink(c, p, off)]
         + [r for r in c._tab_format_requests(p, label_col=off)
            if not (r.get("repeatCell", {}).get("range") == {"sheetId": p})]),
        ("only merges + frozen",
         lambda c, p: _merges(p, off) + [{"updateSheetProperties": {"properties": {"sheetId": p,
             "gridProperties": {"frozenRowCount": 3}}, "fields": "gridProperties.frozenRowCount"}}]),
        ("only whole-sheet font",
         lambda c, p: [{"repeatCell": {"range": {"sheetId": p},
             "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "JetBrains Mono"}}},
             "fields": "userEnteredFormat.textFormat.fontFamily"}}]),
    ]
    print("\n=== bisect cases (pre-batch then addTable A4:D4) ===")
    for name, build in cases:
        n, v, d = bisect_case(name, build)
        print(f"[{v}] {n}")
        if d:
            print(f"        {d}")


if __name__ == "__main__":
    main()
