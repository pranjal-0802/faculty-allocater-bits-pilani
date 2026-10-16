"""
Minimal .xlsx reader using only the standard library.

An .xlsx file is a zip of XML parts. Reading cell values needs three of them:
the workbook (sheet names), the shared-string table, and each sheet. This is
enough for department forms, which are plain values; formulas are read as
their cached results, which is what Excel last displayed.

Written to avoid a dependency the deployment machine cannot always install
(openpyxl). It does not evaluate formulas or read styles.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS = {"m": _MAIN}


def column_index(ref: str) -> int:
    """'A' -> 1, 'AB' -> 28, 'C12' -> 3."""
    letters = re.match(r"[A-Z]+", ref.upper())
    if not letters:
        raise ValueError(f"Bad cell reference: {ref}")
    n = 0
    for ch in letters.group():
        n = n * 26 + ord(ch) - 64
    return n


def split_ref(ref: str) -> tuple[int, int]:
    """'C12' -> (12, 3)."""
    return int(re.search(r"\d+", ref).group()), column_index(ref)


@dataclass
class Sheet:
    name: str
    hidden: bool
    cells: dict[tuple[int, int], str] = field(default_factory=dict)
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)

    def get(self, row: int, col: int) -> str | None:
        return self.cells.get((row, col))

    @property
    def max_row(self) -> int:
        return max((r for r, _ in self.cells), default=0)

    @property
    def max_col(self) -> int:
        return max((c for _, c in self.cells), default=0)

    def rows(self) -> list[int]:
        return sorted({r for r, _ in self.cells})

    def filled(self) -> "Sheet":
        """
        Copy in which every cell of a merged range holds the range's value.

        Forms merge a course code across all its section rows; reading the
        filled copy gives each row its course without carry-forward logic.
        """
        cells = dict(self.cells)
        for r1, c1, r2, c2 in self.merges:
            value = self.cells.get((r1, c1))
            if value is None:
                continue
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    cells[(r, c)] = value
        return Sheet(self.name, self.hidden, cells, list(self.merges))


def read_workbook(source: bytes | str) -> dict[str, Sheet]:
    """Read every sheet of an .xlsx file, given its bytes or a path."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Not an .xlsx file (it is not a zip archive).") from exc

    names = set(archive.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in item.iter(f"{{{_MAIN}}}t")))

    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}

    sheets: dict[str, Sheet] = {}
    for entry in workbook.find("m:sheets", _NS):
        target = targets[entry.get(f"{{{_REL}}}id")].lstrip("/")
        path = target if target.startswith("xl/") else f"xl/{target}"
        root = ET.fromstring(archive.read(path))

        sheet = Sheet(name=entry.get("name"), hidden=entry.get("state") not in (None, "visible"))
        for cell in root.iter(f"{{{_MAIN}}}c"):
            value = _cell_value(cell, shared)
            if value is None or value.strip() == "":
                continue
            sheet.cells[split_ref(cell.get("r"))] = value
        for merge in root.iter(f"{{{_MAIN}}}mergeCell"):
            start, _, end = merge.get("ref").partition(":")
            r1, c1 = split_ref(start)
            r2, c2 = split_ref(end or start)
            sheet.merges.append((r1, c1, r2, c2))
        sheets[sheet.name] = sheet
    return sheets


def _cell_value(cell: ET.Element, shared: list[str]) -> str | None:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{{{_MAIN}}}t"))
    raw = cell.find("m:v", _NS)
    if raw is None or raw.text is None:
        return None
    if kind == "s":
        return shared[int(raw.text)]
    if kind == "b":
        return "TRUE" if raw.text == "1" else "FALSE"
    text = raw.text
    # Integral floats read back as "3" rather than "3.0".
    if kind in (None, "n"):
        try:
            number = float(text)
            if number.is_integer():
                return str(int(number))
        except ValueError:
            pass
    return text
