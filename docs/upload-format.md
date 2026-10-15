# Course allocation upload: how to fill the template

This is for whoever prepares a department's course allocation each semester, usually the HoD's office or the DCA convener.

Instead of sending the allocation workbook in its own format, please fill in the **allocation template** and upload it. It has the same columns as the forms you already use: lecture, practical and tutorial preferences for faculty, and Tute-1/Practical-1 for research scholars. The difference is that it can be checked automatically. Download it from the allocator's home page (**Download blank template**), or from an earlier semester's problem (**Download as template**), which comes pre-filled with that semester's courses and people.

The template has four sheets to fill. The **Instructions** sheet inside the file repeats everything here.

## 1. Courses

One row for every course your department staffs this semester.

| Column | Required | What to write |
|---|---|---|
| Course Number | yes | e.g. `ME F219` |
| Course Title | yes | |
| L / P / U | | Lecture units, lab units and total, as in ERP. One lab unit is two contact hours. |
| Strength | | Expected registrations |
| Category | | `CDC`, `EL`, `HD` or `PROJECT`. Default `CDC`. |
| Service Course | | `Yes` if its sections are shared out as a department duty (e.g. BITS F103 labs). |
| IC Required | | Default `Yes` |
| Pre-assigned IC | | Only if the IC is already decided. It must match a name on the Faculty sheet. |
| IC Units | | Load for being IC. Projects normally 1. |

## 2. Sections

One row for every section: `L1`, `L2` … for lectures, `T1` … for tutorials, `P1` … for practicals. Project courses need no rows.

| Column | Required | What to write |
|---|---|---|
| Course Number | yes | Must be on the Courses sheet |
| Section Number | yes | `L1`, `T3`, `P10` … |
| Faculty Needed | | Default 1 |
| Scholars Needed | | A number, or a range like `1-2`. Default 0 for lectures, 1 for tutorials and practicals. |
| Pre-assigned Instructor 1–3 | | Anyone already fixed to this section. They will not be moved. |

**Half-semester splits** (two faculty each taking a section for half the semester) are not decided by the allocator. Arrange them by hand after the allocation, or pre-assign both people.

## 3. Faculty

One row for every faculty member available to teach.

| Column | What to write |
|---|---|
| Faculty Name (required), PSRN, Email, Designation | PSRN is needed to send the allocation to ERP. |
| Teaching Allocation | As on your current form |
| Min Load / Max Load | Only to override the usual range for this person |
| External Load / Details | Units already carried elsewhere, e.g. `3` and `WILP – Product Design` |
| LECTURE Preference 1–3, PRACTICAL Preference 1–2, TUTORIAL Preference 1–3 | Pick from the drop-down (course number – title) |
| … Taken Consecutively 1–3 | `Yes` if the person has taught that course in consecutive semesters |
| Remarks | Anything else |

## 4. Research Scholars

| Column | What to write |
|---|---|
| Name (required), Email Address | |
| I am ______ in the next semester | Institute Scholar, SRF/JRF, … |
| Supervisor's Name | Must match a name on the Faculty sheet |
| Max Hours | Only to cap this scholar's contact hours |
| Tute-1, Tute-2, Practical-1, Practical-2 | Pick from the drop-down |

## Rules

- **Keep the structure.** Don't rename sheets or headers, or insert columns before existing ones.
- **Leave blanks blank.** Write nothing rather than `NA`, `-` or `nil`.
- **Names must match exactly.** Names used elsewhere (IC, pre-assigned instructors, supervisors) must be spelt as on the Faculty or Research Scholars sheet.
- **Choices outside this semester.** A preference for a course that isn't on the Courses sheet is accepted but ignored, with a warning.

## What happens on upload

Every row is checked. If anything is an **error**, such as a missing required column, an unknown course, a section number like `X1`, a duplicate name, or a pre-assigned person who isn't listed, the whole upload is rejected. Every problem is listed with its sheet, row and column, so you can fix them all in one go. **Warnings**, like a preference for a course not offered, don't block the upload; they are shown with the imported data.

For developers, the machine-readable version of this format is at `/api/schema`.
