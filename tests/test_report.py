import json
from pathlib import Path
import re
import sqlite3
import shutil
import subprocess

import pytest

from report import entity_name, generate_report
from runner import MAX_REPORT_BODY_BYTES, report_body, sanitize_exchange, write_database


def make_database(folder: Path, name: str, started: str, *, failed: bool = False, schema=None) -> Path:
    folder.mkdir(parents=True)
    path = folder / "evidence.sqlite"
    identity = {"build_id": name, "component_version": "1.0", "configuration_id": "staging",
                "schema_location": "schema.json", "base_url": "https://api.example.test",
                "config_sha256": "a" * 64, "schema_sha256": "b" * 64,
                "started_at": started, "schemathesis_version": "4.28.0", "tracecov_version": "0.24.2",
                "config": {"check_exceptions": []}}
    observations = [{"case_id": "case-1", "method": "GET", "path": "/items", "phase": "fuzzing",
                     "response_status": 200,
                     "request": {"method": "GET", "uri": "https://api.example.test/items", "headers": {}, "body": {"status": "absent"}},
                     "response": {"status": 200, "headers": {"Content-Type": ["application/json"]},
                                  "body": {"status": "available", "text": "[]"}},
                     "checks": [{"name": "status_code_conformance", "status": "failure" if failed else "success",
                                 "failure_info": {"failure": {"type": "BadResponse", "message": "bad"}} if failed else None}]}]
    scenarios = [{"operation": "GET /items", "phase": "fuzzing", "status": "failure" if failed else "success", "skip_reason": None},
                 {"operation": "GET /items", "phase": "examples", "status": "skip", "skip_reason": "no examples"}]
    verdict = {"passed": not failed, "issues": ["bad"] if failed else [], "operations": {"tested": 1}}
    write_database(path, identity, {("GET", "/items")}, observations, scenarios, {}, verdict, schema)
    return path


def embedded_data(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match.group(1))


def test_report_aggregates_sorts_and_handles_older_and_bad_databases(tmp_path):
    older = make_database(tmp_path / "archive" / "older", "old", "2026-01-01T00:00:00+00:00")
    newest = make_database(tmp_path / "archive" / "newest", "new", "2026-02-01T00:00:00+00:00", failed=True)
    (newest.parent / "coverage.html").write_text("<html>TraceCov</html>", encoding="utf-8")
    with sqlite3.connect(older) as db:
        db.execute("ALTER TABLE observation DROP COLUMN request_json")
        db.execute("ALTER TABLE observation DROP COLUMN response_json")
        db.execute("ALTER TABLE operation DROP COLUMN tags_json")
        db.execute("ALTER TABLE operation DROP COLUMN summary")
    broken = tmp_path / "archive" / "broken"
    broken.mkdir()
    (broken / "evidence.sqlite").write_text("not a database", encoding="utf-8")
    output = tmp_path / "archive" / "report.html"
    count, warnings = generate_report(tmp_path / "archive", output)
    assert count == 2 and len(warnings) == 1
    data = embedded_data(output)
    assert [run["build_number"] for run in data["runs"]] == ["new", "old"]
    assert data["runs"][0]["failure_count"] == 1
    assert data["runs"][0]["coverage_href"] == "newest/coverage.html"
    assert data["runs"][1]["coverage_href"] is None
    assert data["runs"][1]["http_available"] is False
    assert data["runs"][1]["operations"][0]["cases"][0]["request"] is None
    assert data["runs"][1]["operations"][0]["entity"] == "Items"
    assert data["runs"][0]["operations"][0]["scenarios"][1]["skip_reason"] == "no examples"


def test_operation_entity_uses_schema_tag_with_legacy_path_fallback(tmp_path):
    make_database(tmp_path / "run", "tagged", "2026-02-01T00:00:00+00:00",
                  schema={"paths": {"/items": {"get": {"tags": ["Inventory", "Items"], "summary": "List inventory"}}}})
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    operation = embedded_data(output)["runs"][0]["operations"][0]
    assert operation["entity"] == "Inventory" and operation["summary"] == "List inventory"
    assert entity_name({"path": "/api/v1/audit-logs/{id}"}) == "Audit Logs"
    assert entity_name({"path": "/api/v2/{id}"}) == "Other operations"


@pytest.mark.skipif(shutil.which("node") is None, reason="UI interaction test requires Node.js")
def test_report_run_navigation_and_nested_disclosures(tmp_path):
    make_database(tmp_path / "old", "older", "2026-01-01T00:00:00+00:00")
    make_database(tmp_path / "new", "newer", "2026-02-01T00:00:00+00:00", failed=True,
                  schema={"paths": {"/items": {"get": {"tags": ["Inventory"]}}}})
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    root = Path(__file__).resolve().parents[1]
    subprocess.run([shutil.which("node"), str(root / "tests/report_ui.test.cjs"), str(output),
                    str(root / "report_assets/app.js")], check=True, capture_output=True, text=True)


def test_sanitized_http_exchange_and_body_limits():
    request = {"method": "POST", "uri": "https://user:pass@example.test/items?token=secret&filter=open",
               "headers": {"Authorization": ["Bearer secret"], "Content-Type": ["application/json"]},
               "body": {"$base64": "eyJwYXNzd29yZCI6ICJzZWNyZXQiLCAibmFtZSI6ICJvayJ9"}}
    response = {"status_code": 200, "headers": {"Content-Type": ["application/json"], "Set-Cookie": ["session=secret"]},
                "content": {"$base64": "eyJhY2Nlc3NfdG9rZW4iOiAic2VjcmV0IiwgIm5hbWUiOiAib2sifQ=="}}
    exchange = {"request": request, "response": response}
    sanitized = {side: sanitize_exchange(exchange, side) for side in ("request", "response")}
    serialized = json.dumps(sanitized)
    assert "secret" not in serialized and "user:pass" not in serialized
    assert "[REDACTED]" in serialized and "filter=open" in serialized
    assert sanitized["response"]["body"]["status"] == "available"
    assert report_body({"$base64": "YWJj"}, {"Content-Type": ["text/plain"]})["status"] == "omitted: non-JSON body"
    large = json.dumps({"value": "x" * MAX_REPORT_BODY_BYTES}).encode()
    import base64
    limited = report_body({"$base64": base64.b64encode(large).decode()}, {"Content-Type": ["application/json"]})
    assert limited["status"] == "truncated"
    assert len(limited["text"].encode()) <= MAX_REPORT_BODY_BYTES


def test_report_payload_cannot_close_script(tmp_path):
    path = make_database(tmp_path / "run", "</script><script>alert(1)</script>", "2026-02-01T00:00:00+00:00")
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    html = output.read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in html
    assert embedded_data(output)["runs"][0]["build_number"] == "</script><script>alert(1)</script>"
