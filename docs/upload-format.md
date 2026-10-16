# Course allocation upload: how to fill the template

This is for whoever prepares a department's course allocation each semester, usually the HoD's office or the DCA convener.

Instead of sending the allocation workbook in its own format, please fill in the **allocation template** and upload it. It has the same columns as the faculty form you already use: lecture, practical and tutorial preferences, "Taken Consecutively" and Teaching Allocation. The difference is that it can be checked automatically. Download a blank template from the allocator's **New allocation** page, or start from a previous semester's allocation: its courses, sections and faculty come pre-filled, ready to adjust.

The template has three sheets to fill. The **Instructions** sheet inside the file repeats everything here. Research scholars are not part of this allocation, so leave them out.

## 1. Courses

One row for every course your department staffs this semester.

| Column | Required | What to write |
|---|---|---|
| Course Number | yes | e.g. `ME F219` |
| Course Title | yes | |
| L / P / U | | Lecture units, lab units and total, as in ERP. One lab unit is two contact hours. |
| Strength | | Expected registrations |
| Category | yes | `CDC` if the course is compulsory for any programme, `EL` for an elective (discipline, humanities or open), `PROJECT` for study, lab and design projects. A course that is a CDC for one programme and an elective for another is a `CDC`. Get this right: nobody is given two CDCs. |
| Level | | `FD` or `HD`. Blank: `HD` for G-numbered courses. HD is a level, not a category. |
| Service Course | | `Yes` if its sections are shared out as a department duty (e.g. BITS F103 labs). Service courses don't count as CDCs. |

## 2. Sections

One row for every section the course runs: `L1`, `L2` … for lectures, `T1` … for tutorials, `P1` … for practicals. Project courses need no rows. The result has one row for each of these sections, saying who takes it.

| Column | Required | What to write |
|---|---|---|
| Course Number | yes | Must be on the Courses sheet |
| Section Number | yes | `L1`, `T3`, `P10` … |
| Faculty Needed | | Default 1. Write `0` for a section run by research scholars only; it is still listed in the result. |
| Pre-assigned Instructor 1–3 | | Any faculty member already fixed to this section. They will not be moved. |

**Half-semester splits** (two faculty each taking a section for half the semester) are not decided by the allocator. Arrange them by hand after the allocation, or pre-assign both people.

## 3. Faculty

One row for every faculty member available to teach.

| Column | What to write |
|---|---|
| Faculty Name (required), PSRN, Email, Designation | PSRN is needed to send the allocation to ERP. |
| Teaching Allocation | The percentage of time the person opts for teaching, as on your form. The standard load is the department's total teaching units divided by its number of faculty; 50 earns exactly that, and the target scales with it: 70 asks for 1.4 times the standard, 40 for 0.8 times. A blank counts as 50. |
| Min Load / Max Load | Only to override the target worked out from Teaching Allocation |
| External Load / Details | Units already carried elsewhere, e.g. `3` and `WILP – Product Design`. A WILP course is 3 units, shared among its faculty. |
| LECTURE Preference 1–3, PRACTICAL Preference 1–2, TUTORIAL Preference 1–3 | Pick from the drop-down (course number – title) |
| … Taken Consecutively 1–3 | **The number of semesters in a row** the person has taught that course up to now: `0`, `1`, `2`, `3` … At `3` the course rotates to someone else this semester. `Yes`/`No` is still accepted, but a bare `Yes` doesn't say how many semesters, so the rotation rule can't be applied and it is flagged. |
| Remarks | Anything else |

## The rules the allocator applies

- **Load follows Teaching Allocation.** Each person's target is the standard load times their percentage / 50. Units are counted as in the timetable app's course-load report: a lecture's units are shared among the faculty on it, a practical counts its lab units, and a tutorial counts 1. The target is a ceiling: the allocator goes over it only when there is no other way to staff a section. If everyone's targets add up to less than the work, the excess is shared out as evenly as possible.
- **At most one CDC each.** Two courses can be a CDC and an elective, or two electives, but not two CDCs. Any section of a CDC counts. Only first-degree CDCs count: service courses (BITS F103, BITS F111) and higher-degree courses do not.
- **ICs are not part of it.** The department picks each course's IC from its instructors once the allocation is made.
- **Rotation.** A course someone has taught 3 semesters running goes to someone else.
- **Pre-assignments win.** Anyone you pre-assign stays, even against the CDC or rotation rule; the allocator will point it out.
- **Short of volunteers.** A course with fewer volunteers than places (say two lecture sections and one person who asked) is flagged separately, before and after the run, with who ended up taking the places.

## Rules for filling in

- **Keep the structure.** Don't rename sheets or headers, or insert columns before existing ones.
- **Leave blanks blank.** Write nothing rather than `NA`, `-` or `nil`.
- **Names must match exactly.** Pre-assigned instructors must be spelt as on the Faculty sheet.
- **Category is required.** Every course must say `CDC`, `EL` or `PROJECT`.
- **Choices outside this semester.** A preference for a course that isn't on the Courses sheet is accepted but ignored, with a warning.

## What happens on upload

Every row is checked. If anything is an **error**, such as a missing required column, an unknown course, a section number like `X1`, a duplicate name, a pre-assigned person who isn't listed, or "Taken Consecutively" written as `often`, the whole upload is rejected. Every problem is listed with its sheet, row and column, so you can fix them all in one go. **Warnings**, like a preference for a course not offered, don't block the upload; they are shown with the imported data.

For developers, the machine-readable version of this format is at `/api/schema`.
