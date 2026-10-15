"""
Minimal .xlsx writer using only the standard library.

Enough for the upload template: text, numbers and formulas; a styled header
row; frozen panes; column widths; hidden sheets; and list drop-downs (data
validation). Strings are written inline, so there is no shared-string table
to keep in step.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# Style ids, in the order of cellXfs in _STYLES.
PLAIN, HEADER, WRAP, TITLE, REQUIRED_HEADER = 0, 1, 2, 3, 4

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="14"/><name val="Calibri"/></font>
</fonts>
<fills count="4">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF2F5D8A"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1B3A5C"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def col_letter(n: int) -> str:
    """1 -> A, 27 -> AA."""
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


@dataclass
class Formula:
    expr: str


@dataclass
class Validation:
    """A drop-down list on a range, sourced from a range elsewhere."""

    sqref: str  # e.g. "K2:K500"
    source: str  # e.g. "Lists!$A$2:$A$500"
    #: "warning" lets people type a value not in the list (after a prompt);
    #: "stop" forbids it.
    strictness: str = "warning"


@dataclass
class SheetData:
    name: str
    rows: dict[int, dict[int, object]] = field(default_factory=dict)
    styles: dict[tuple[int, int], int] = field(default_factory=dict)
    widths: dict[int, float] = field(default_factory=dict)
    freeze_rows: int = 0
    freeze_cols: int = 0
    hidden: bool = False
    validations: list[Validation] = field(default_factory=list)
    row_heights: dict[int, float] = field(default_factory=dict)

    def set(self, row: int, col: int, value: object, style: int | None = None) -> None:
        if value is None or value == "":
            return
        self.rows.setdefault(row, {})[col] = value
        if style is not None:
            self.styles[(row, col)] = style


def _cell_xml(row: int, col: int, value: object, style: int) -> str:
    ref = f"{col_letter(col)}{row}"
    s = f' s="{style}"' if style else ""
    if isinstance(value, Formula):
        return f'<c r="{ref}"{s}><f>{escape(value.expr)}</f></c>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value:g}</v></c>' if isinstance(value, float) else f'<c r="{ref}"{s}><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(sheet: SheetData) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']

    if sheet.freeze_rows or sheet.freeze_cols:
        top_left = f"{col_letter(sheet.freeze_cols + 1)}{sheet.freeze_rows + 1}"
        pane_attrs = []
        if sheet.freeze_cols:
            pane_attrs.append(f'xSplit="{sheet.freeze_cols}"')
        if sheet.freeze_rows:
            pane_attrs.append(f'ySplit="{sheet.freeze_rows}"')
        active = "bottomRight" if sheet.freeze_rows and sheet.freeze_cols else ("bottomLeft" if sheet.freeze_rows else "topRight")
        parts.append(
            f'<sheetViews><sheetView workbookViewId="0"><pane {" ".join(pane_attrs)} '
            f'topLeftCell="{top_left}" activePane="{active}" state="frozen"/>'
            f'<selection pane="{active}" activeCell="{top_left}" sqref="{top_left}"/></sheetView></sheetViews>'
        )
    parts.append('<sheetFormatPr defaultRowHeight="15"/>')
    if sheet.widths:
        parts.append("<cols>")
        for col in sorted(sheet.widths):
            parts.append(f'<col min="{col}" max="{col}" width="{sheet.widths[col]}" customWidth="1"/>')
        parts.append("</cols>")

    parts.append("<sheetData>")
    for row in sorted(sheet.rows):
        height = sheet.row_heights.get(row)
        attrs = f' ht="{height}" customHeight="1"' if height else ""
        cells = "".join(
            _cell_xml(row, col, sheet.rows[row][col], sheet.styles.get((row, col), 0))
            for col in sorted(sheet.rows[row])
        )
        parts.append(f'<row r="{row}"{attrs}>{cells}</row>')
    parts.append("</sheetData>")

    if sheet.validations:
        parts.append(f'<dataValidations count="{len(sheet.validations)}">')
        for v in sheet.validations:
            parts.append(
                f'<dataValidation type="list" errorStyle="{v.strictness}" allowBlank="1" '
                f'showErrorMessage="1" sqref="{v.sqref}"><formula1>{escape(v.source)}</formula1></dataValidation>'
            )
        parts.append("</dataValidations>")
    parts.append('<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def write_workbook(sheets: list[SheetData]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   f"{overrides}</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        hidden = ' state="hidden"'
        sheet_entries = "".join(
            f'<sheet name="{escape(s.name)}" sheetId="{i}"{hidden if s.hidden else ""} r:id="rId{i}"/>'
            for i, s in enumerate(sheets, 1)
        )
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f"<sheets>{sheet_entries}</sheets><calcPr calcId=\"191029\" fullCalcOnLoad=\"1\"/></workbook>")
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        styles_id = len(sheets) + 1
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{rels}<Relationship Id="rId{styles_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))
    return buf.getvalue()
