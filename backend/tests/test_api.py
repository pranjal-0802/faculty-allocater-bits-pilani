import time

import pytest
from conftest import course, faculty, lecture, pref, problem
from fastapi.testclient import TestClient

from allocator.api import main
from allocator.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    with TestClient(main.app) as c:
        yield c


def test_problem_run_and_exports(client):
    p = problem([faculty("a"), faculty("b")], [course("X", lecture()), course("Y", lecture())],
                [pref("a", "X"), pref("b", "Y")])
    assert client.post("/api/problems", json=p.model_dump(mode="json")).status_code == 201
    assert client.get("/api/problems").json()[0]["id"] == "t"

    run = client.post("/api/problems/t/runs", json={"settings": {"time_limit_seconds": 5, "num_workers": 1}}).json()
    for _ in range(100):
        status = client.get(f"/api/runs/{run['id']}/status").json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert status == "completed"

    body = client.get(f"/api/runs/{run['id']}").json()
    assert body["result"]["allocation"]["assignments"]
    csv = client.get(f"/api/runs/{run['id']}/export/assignments.csv").text
    assert csv.startswith("Course,")
    sections = client.get(f"/api/runs/{run['id']}/export/timetable.json").json()
    assert {s["courseCode"] for s in sections} == {"X", "Y"}


def test_locks_and_person_patch(client):
    p = problem([faculty("a")], [course("X", lecture())], [pref("a", "X")])
    client.post("/api/problems", json=p.model_dump(mode="json"))
    locks = [{"person_id": "a", "course_code": "X", "target": "L1", "mode": "require"}]
    assert client.put("/api/problems/t/locks", json=locks).status_code == 200
    patched = client.patch("/api/problems/t/people/a", json={"load_max": 4}).json()
    assert patched["load_max"] == 4
    assert client.get("/api/problems/t").json()["locks"][0]["target"] == "L1"


def test_template_download_and_strict_upload(client):
    blank = client.get("/api/template.xlsx?department=ME")
    assert blank.status_code == 200 and blank.content[:2] == b"PK"
    r = client.post("/api/problems/import", content=blank.content,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 422
    issues = r.json()["detail"]["issues"]
    assert any(i["sheet"] == "Courses" and i["severity"] == "error" for i in issues)

    p = problem([faculty("a")], [course("ME F219", lecture())], [pref("a", "ME F219")])
    p.courses[0].title = "Manufacturing Processes"
    client.post("/api/problems", json=p.model_dump(mode="json"))
    filled = client.get("/api/template.xlsx?problem_id=t").content
    r = client.post("/api/problems/import?department=ME&name=again", content=filled,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 201 and r.json()["format"] == "template"

    schema = client.get("/api/schema").json()
    assert [s["name"] for s in schema["template"]["sheets"]] == ["Courses", "Sections", "Faculty", "Research Scholars"]
    assert "properties" in schema["problem"]


def test_multipart_upload(client):
    p = problem([faculty("a")], [course("ME F219", lecture())], [pref("a", "ME F219")])
    p.courses[0].title = "Manufacturing Processes"
    client.post("/api/problems", json=p.model_dump(mode="json"))
    filled = client.get("/api/template.xlsx?problem_id=t").content
    r = client.post("/api/problems/import?department=ME&name=multipart",
                    files={"file": ("allocation.xlsx", filled, "application/octet-stream")})
    assert r.status_code == 201 and r.json()["format"] == "template"
    r = client.post("/api/problems/import", files={"other": ("x.xlsx", filled)})
    assert r.status_code == 400


def test_bad_upload_is_rejected(client):
    r = client.post("/api/problems/import", content=b"not a workbook",
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 422


def test_unknown_ids_404(client):
    assert client.get("/api/problems/nope").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/problems/..%2Fx").status_code in (404, 422)
