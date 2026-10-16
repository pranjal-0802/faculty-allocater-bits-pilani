"""
Check the standard-library .xlsx reader and writer against openpyxl.

The project reads and writes .xlsx without openpyxl so that it has no
spreadsheet dependency at run time. These tests keep it honest: whatever the
template writer produces, openpyxl must read identically, and on the real
department workbook the two readers must agree on every cell the importer
uses. (They differ on date cells, which the stdlib reader returns as Excel
serial numbers; no template or importer column holds a date.)
"""

import datetime
import io

import pytest
from conftest import WORKBOOK

from allocator.ingest.template import write_template
from allocator.ingest.xlsx import read_workbook

openpyxl = pytest.importorskip("openpyxl")


def _value(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v)
    return s if s.strip() else None


def _differences(source) -> list[str]:
    mine = read_workbook(source)
    wb = openpyxl.load_workbook(io.BytesIO(source) if isinstance(source, bytes) else source, data_only=True)
    out = []
    for ws in wb.worksheets:
        sheet = mine[ws.title]
        assert sheet.hidden == (ws.sheet_state != "visible"), ws.title
        theirs = {}
        dates = set()
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, (datetime.datetime, datetime.date, datetime.time)):
                    dates.add((cell.row, cell.column))
                elif _value(cell.value) is not None:
                    theirs[(cell.row, cell.column)] = _value(cell.value)
        for key in (set(theirs) | set(sheet.cells)) - dates:
            a, b = sheet.cells.get(key), theirs.get(key)
            if a == b:
                continue
            try:
                if a is not None and b is not None and abs(float(a) - float(b)) < 1e-9:
                    continue
            except ValueError:
                pass
            out.append(f"{ws.title}!{key}: stdlib={a!r} openpyxl={b!r}")
        merges = sorted((r.min_row, r.min_col, r.max_row, r.max_col) for r in ws.merged_cells.ranges)
        if sorted(sheet.merges) != merges:
            out.append(f"{ws.title}: merged ranges differ")
    return out


def test_blank_template_reads_the_same_in_openpyxl():
    assert _differences(write_template(department="ME")) == []


def test_template_structure_is_what_openpyxl_expects():
    wb = openpyxl.load_workbook(io.BytesIO(write_template(department="ME")))
    assert wb.sheetnames == ["Instructions", "Courses", "Sections", "Faculty", "Research Scholars", "Lists"]
    assert wb["Lists"].sheet_state == "hidden"
    faculty = wb["Faculty"]
    assert faculty.freeze_panes == "B2"
    sources = {v.formula1 for v in faculty.data_validations.dataValidation}
    assert "Lists!$A$2:$A$501" in sources  # course drop-downs
    assert "Lists!$B$2:$B$3" in sources  # Yes / No


@pytest.mark.skipif(not WORKBOOK.exists(), reason="ME workbook not in data/raw")
def test_department_workbook_reads_the_same_in_openpyxl():
    assert _differences(str(WORKBOOK)) == []


@pytest.mark.skipif(not WORKBOOK.exists(), reason="ME workbook not in data/raw")
def test_filled_template_reads_the_same_in_openpyxl():
    from allocator.ingest import import_upload

    problem, _ = import_upload(WORKBOOK.read_bytes(), department="ME")
    assert _differences(write_template(problem)) == []
