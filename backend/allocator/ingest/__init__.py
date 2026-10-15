"""
Turning uploads into problems.

Two formats are accepted:

* the upload template (template.py): the format departments are asked to use;
  read strictly, with every problem reported by sheet, row and column;
* a legacy department workbook (workbook.py): the free-form Mechanical
  workbook this project started from, read best-effort, kept so past
  semesters can be replayed and compared.
"""

from __future__ import annotations

from enum import Enum

from allocator.domain import Problem
from allocator.ingest.template import TemplateError, is_template, read_template
from allocator.ingest.workbook import ImportOptions, import_workbook
from allocator.ingest.xlsx import read_workbook


class UploadFormat(str, Enum):
    AUTO = "auto"
    TEMPLATE = "template"
    LEGACY = "legacy"


def detect_format(data: bytes) -> UploadFormat:
    return UploadFormat.TEMPLATE if is_template(read_workbook(data)) else UploadFormat.LEGACY


def import_upload(
    data: bytes,
    fmt: UploadFormat = UploadFormat.AUTO,
    department: str = "",
    name: str = "",
    semester: str = "",
    default_staffing: bool = False,
) -> tuple[Problem, UploadFormat]:
    if fmt == UploadFormat.AUTO:
        fmt = detect_format(data)
    if fmt == UploadFormat.TEMPLATE:
        return read_template(data, department=department, name=name, semester=semester), fmt
    problem = import_workbook(
        data,
        ImportOptions(
            department=department or "ME",
            name=name,
            semester=semester,
            staffing_from_reference=not default_staffing,
        ),
    )
    return problem, fmt


__all__ = ["TemplateError", "UploadFormat", "detect_format", "import_upload"]
