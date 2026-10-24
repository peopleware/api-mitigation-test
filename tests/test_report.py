import json
import base64
import gzip
import os
from pathlib import Path
import re
import sqlite3
import shutil
import subprocess
import sys
from urllib.parse import unquote

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


def report_manifest(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match.group(1))


def read_chunk(path: Path, ref: str):
    script = (path.parent / unquote(ref)).read_text(encoding="utf-8")
    arguments = json.loads("[" + script.removeprefix("window.reportChunk(").strip().removesuffix(");") + "]")
    assert arguments[0] == ref
    raw = gzip.decompress(base64.b64decode(arguments[2]))
    assert len(raw) == arguments[1]
    return json.loads(raw)


def embedded_data(path: Path) -> dict:
    """Reconstruct small test bundles to retain full evidence assertions."""
    data = report_manifest(path)
    for run in [*data["runs"], *([data["combined"]] if data["combined"] else [])]:
        run["operations"] = [op for ref in run["operations_ref"] for op in read_chunk(path, ref)]
        for operation in run["operations"]:
            cases = []
            for phase in operation["phases"]:
                for index in phase["indexes"]:
                    for summary in read_chunk(path, index["ref"]):
                        records = read_chunk(path, summary["detail_ref"])
                        cases.append(next(record for record in records if record["uid"] == summary["uid"]))
            operation["cases"] = cases
    return data


def test_combined_report_preserves_source_runs_and_rejects_incompatible_targets(tmp_path):
    make_database(tmp_path / "main", "main", "2026-01-01T00:00:00+00:00")
    logout = make_database(tmp_path / "logout", "logout", "2026-01-01T00:05:00+00:00", failed=True)
    output = tmp_path / "report.html"
    originals = {file: file.read_bytes() for file in tmp_path.rglob("evidence.sqlite")}
    generate_report(tmp_path, output, combine_runs=True)
    data = embedded_data(output)
    assert data["combine_runs"] is True
    assert len(data["runs"]) == 2
    assert [run["passed"] for run in data["runs"]] == [False, True]
    assert all(file.read_bytes() == original for file, original in originals.items())
    with sqlite3.connect(logout) as db:
        db.execute("UPDATE build SET base_url = 'https://another-api.example.test'")
    with pytest.raises(ValueError, match="same API and component version"):
        generate_report(tmp_path, output, combine_runs=True)


def test_combined_report_does_not_silently_omit_unreadable_evidence(tmp_path):
    make_database(tmp_path / "main", "main", "2026-01-01T00:00:00+00:00")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "evidence.sqlite").write_text("broken")
    with pytest.raises(ValueError, match="Cannot combine incomplete evidence"):
        generate_report(tmp_path, tmp_path / "report.html", combine_runs=True)


def test_report_retains_native_response_coverage_without_inventing_hits(tmp_path):
    database = make_database(tmp_path / "run", "native", "2026-01-01T00:00:00+00:00")
    responses = [{"status": "200", "hits": 1}, {"status": "405", "hits": 0}, {"status": "500", "hits": 0}]
    with sqlite3.connect(database) as db:
        db.execute("UPDATE operation SET coverage_json = ?", (json.dumps({"responses": responses}),))
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    assert embedded_data(output)["runs"][0]["operations"][0]["response_coverage"] == responses


@pytest.mark.parametrize("mode", ["default", "input", "output", "environment"])
def test_report_command_defaults_and_overrides(tmp_path, monkeypatch, mode):
    from report import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_MITIGATION_OUTPUT_DIR", raising=False)
    folder = tmp_path / ("artifacts" if mode == "default" else "archive")
    database = make_database(folder / "run", "archived", "2026-01-01T00:00:00+00:00")
    original = database.read_bytes()
    args = ["report.py"]
    output = folder / "report.html"
    if mode == "environment":
        monkeypatch.setenv("API_MITIGATION_OUTPUT_DIR", str(folder))
    elif mode != "default":
        monkeypatch.setenv("API_MITIGATION_OUTPUT_DIR", str(tmp_path / "unused"))
        args += ["--input-dir", str(folder)]
    if mode == "output":
        output = tmp_path / "custom.html"
        args += ["--output", str(output)]
    output.write_text("old report", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", args)
    assert main() == 0
    assert embedded_data(output)["runs"][0]["build_number"] == "archived"
    assert database.read_bytes() == original


@pytest.mark.parametrize("has_evidence", [True, False])
def test_shell_report_command_without_test_configuration(tmp_path, has_evidence):
    root = Path(__file__).resolve().parents[1]
    git_sh = Path("C:/Program Files/Git/usr/bin/sh.exe")
    shell = str(git_sh) if git_sh.is_file() else shutil.which("sh") or shutil.which("bash")
    if not shell:
        pytest.skip("Shell wrapper needs sh or bash")

    def shell_path(value):
        value = Path(value).as_posix()
        return f"/{value[0].lower()}/{value[3:]}" if len(value) > 2 and value[1:3] == ":/" else value

    env = os.environ.copy()
    for key in ("CONFIG", "BITBUCKET_BUILD_NUMBER", "BITBUCKET_CLONE_DIR", "GITHUB_WORKSPACE",
                "API_MITIGATION_BUILD_ID", "API_MITIGATION_OUTPUT_DIR", "API_MITIGATION_BEARER_TOKEN"):
        env.pop(key, None)
    if os.name == "nt":
        env["PATH"] = shell_path(Path(sys.executable).parent) + ":/usr/bin:/bin"
    if has_evidence:
        make_database(tmp_path / "artifacts" / "run", "archived", "2026-01-01T00:00:00+00:00")
    proc = subprocess.run([shell, shell_path(root / "bitbucket-pipe/run.sh"), "report"],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    if has_evidence:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert embedded_data(tmp_path / "artifacts/report.html")["runs"][0]["build_number"] == "archived"
    else:
        assert proc.returncode == 1
        assert "No evidence.sqlite files found" in proc.stderr
    assert not list(tmp_path.rglob("verdict.json"))


def test_report_aggregates_sorts_and_handles_older_and_bad_databases(tmp_path):
    older = make_database(tmp_path / "archive" / "older", "old", "2026-01-01T00:00:00+00:00")
    newest = make_database(tmp_path / "archive" / "newest", "new", "2026-02-01T00:00:00+00:00", failed=True)
    (newest.parent / "coverage.html").write_text("<html>TraceCov</html>", encoding="utf-8")
    with sqlite3.connect(older) as db:
        db.execute("ALTER TABLE observation DROP COLUMN request_json")
        db.execute("ALTER TABLE observation DROP COLUMN response_json")
        db.execute("ALTER TABLE observation DROP COLUMN parent_case_id")
        db.execute("ALTER TABLE observation DROP COLUMN verification_json")
        db.execute("ALTER TABLE observation DROP COLUMN execution_status")
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
    assert data["runs"][1]["operations"][0]["cases"][0]["execution_status"] == "executed"
    assert data["runs"][1]["operations"][0]["entity"] == "Items"
    assert data["runs"][0]["operations"][0]["scenarios"][1]["skip_reason"] == "no examples"


@pytest.mark.parametrize("has_http", [True, False])
def test_archived_generated_case_classification_preserves_verdict(tmp_path, has_http):
    from report import load_database

    database = make_database(tmp_path / "run", "archive", "2026-01-01T00:00:00+00:00", failed=True)
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE observation DROP COLUMN execution_status")
        db.execute("INSERT INTO observation (build_id, operation_id, case_id, phase) VALUES (1, 1, 'generated', 'stateful')")
        if not has_http:
            db.execute("ALTER TABLE observation DROP COLUMN request_json")
            db.execute("ALTER TABLE observation DROP COLUMN response_json")
    original = database.read_bytes()
    run = load_database(database, tmp_path / "report.html")[0]
    assert not run["passed"]
    assert run["issues"] == ["bad"]
    assert run["generated_count"] == 2
    assert run["unexecuted_count"] == int(has_http)
    assert run["executed_count"] == 1
    assert run["unknown_execution_count"] == int(not has_http)
    assert run["operations"][0]["cases"][1]["execution_status"] == ("not_executed" if has_http else "unknown")
    assert database.read_bytes() == original


def test_operation_entity_uses_schema_tag_with_legacy_path_fallback(tmp_path):
    make_database(tmp_path / "run", "tagged", "2026-02-01T00:00:00+00:00",
                  schema={"paths": {"/items": {"get": {"tags": ["Inventory", "Items"], "summary": "List inventory"}}}})
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    operation = embedded_data(output)["runs"][0]["operations"][0]
    assert operation["entity"] == "Inventory" and operation["summary"] == "List inventory"
    assert entity_name({"path": "/api/v1/audit-logs/{id}"}) == "Audit Logs"
    assert entity_name({"path": "/api/v2/{id}"}) == "Other operations"


@pytest.mark.parametrize("second_tags, expected_category", [
    (["Inventory", "Secondary"], "Inventory"),
    (["Orders"], "Items"),
    ([], "Inventory"),
])
def test_unlisted_methods_inherit_only_unambiguous_same_path_tags(tmp_path, second_tags, expected_category):
    database = make_database(tmp_path / "run", "tagged", "2026-02-01T00:00:00+00:00",
                             schema={"paths": {"/items": {"get": {"tags": [" Inventory ", "Secondary"]}}}})
    # Extend archived evidence without changing the writer or requiring the original schema.
    with sqlite3.connect(database) as db:
        db.executemany(
            "INSERT INTO operation (build_id, method, path, expected, tested, tags_json) VALUES (1, ?, ?, ?, 0, ?)",
            [("POST", "/items", 1, json.dumps(second_tags)),
             ("DELETE", "/items", 0, "[]"),
             ("DELETE", "/items/other", 0, "[]")],
        )
    original = database.read_bytes()
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    operations = {(op["method"], op["path"]): op for op in embedded_data(output)["runs"][0]["operations"]}
    assert operations[("GET", "/items")]["entity"] == "Inventory"
    assert operations[("POST", "/items")]["entity"] == (second_tags[0] if second_tags else "Items")
    assert operations[("DELETE", "/items")]["entity"] == expected_category
    assert operations[("DELETE", "/items/other")]["entity"] == "Items"
    assert database.read_bytes() == original


def test_report_embeds_offline_chartjs(tmp_path):
    make_database(tmp_path / "run", "offline", "2026-01-01T00:00:00+00:00")
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    html = output.read_text(encoding="utf-8")
    assert "Chart.js v4.5.1" in html
    assert "Permission is hereby granted" in html
    assert "__CHARTJS__" not in html
    assert not re.search(r"<script[^>]+src=", html)
    assert "sourceMappingURL" not in html
    assert html.index("Chart.js v4.5.1") < html.index("const DATA =")


@pytest.mark.skipif(shutil.which("node") is None, reason="UI interaction test requires Node.js")
@pytest.mark.parametrize("combine_runs", [False, True])
def test_report_run_navigation_and_nested_disclosures(tmp_path, combine_runs):
    make_database(tmp_path / "old", "older", "2026-01-01T00:00:00+00:00")
    database = make_database(tmp_path / "new", "newer", "2026-02-01T00:00:00+00:00", failed=True,
                             schema={"paths": {"/items": {"get": {"tags": ["Inventory"]}}}})
    with sqlite3.connect(database) as db:
        for index in range(1, 121):
            cursor = db.execute("INSERT INTO observation (build_id,operation_id,case_id,phase,response_status,execution_status) VALUES (1,1,?,'fuzzing',200,'executed')", (f"copy-{index}",))
            accepted = index == 120
            db.execute("INSERT INTO check_result (observation_id,name,status,failure_type,exception_json) VALUES (?,?,?,?,?)",
                       (cursor.lastrowid, "status_code_conformance", "failure" if accepted else "success",
                        "BadResponse" if accepted else None, '{"reason":"accepted"}' if accepted else None))
        db.executemany("INSERT INTO operation (build_id,method,path,expected,tested) VALUES (1,'GET',?,1,0)",
                       [(f"/items/paged-{index:03}",) for index in range(104)])
    output = tmp_path / "report.html"
    generate_report(tmp_path, output, combine_runs=combine_runs)
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


def test_bundle_chunks_preserve_all_evidence_and_local_case_ids(tmp_path, monkeypatch):
    import report

    monkeypatch.setattr(report, "CHUNK_BYTES", 2000)
    monkeypatch.setattr(report, "INDEX_CASES", 2)
    for name in ("first", "second"):
        database = make_database(tmp_path / name, name, "2026-01-01T00:00:00+00:00")
        with sqlite3.connect(database) as db:
            for index in range(8):
                cursor = db.execute("INSERT INTO observation (build_id,operation_id,case_id,phase,response_status,execution_status) VALUES (1,1,?,'fuzzing',200,'executed')", (f"duplicate-{index}",))
                db.execute("INSERT INTO check_result (observation_id,name,status) VALUES (?,'check','success')", (cursor.lastrowid,))
    output = tmp_path / "custom name.html"
    generate_report(tmp_path, output, combine_runs=True)
    manifest = report_manifest(output)
    assert manifest["format_version"] == 1
    assert "operations" not in manifest["runs"][0]
    assert output.stat().st_size < 1_000_000
    reconstructed = embedded_data(output)
    combined = reconstructed["combined"]["operations"][0]
    assert len(combined["cases"]) == 18
    assert len({case["uid"] for case in combined["cases"]}) == 18
    assert len(combined["phases"][0]["indexes"]) == 10
    for run in reconstructed["runs"]:
        actual = [{key: value for key, value in case.items() if key != "uid"} for case in run["operations"][0]["cases"]]
        expected = report.load_database(tmp_path / run["build_number"] / "evidence.sqlite", output)[0]["operations"][0]["cases"]
        assert actual == expected
        for index in run["operations"][0]["phases"][0]["indexes"]:
            assert len(read_chunk(output, index["ref"])) <= 2
    assert all(ref.startswith("custom%20name.data/") for ref in manifest["runs"][0]["operations_ref"])


@pytest.mark.parametrize("status,response,exception,verification,expected", [
    ("success", 200, None, None, "pass"),
    ("failure", 200, None, None, "fail"),
    ("failure", 200, {}, None, "accepted"),
    ("failure", None, {"reason": "accepted"}, None, "neutral"),
    ("unknown", 200, None, None, "neutral"),
    (None, 200, None, None, "neutral"),
    (None, 200, None, {}, "pass"),
])
def test_precomputed_classification_preserves_browser_semantics(tmp_path, status, response, exception, verification, expected):
    database = make_database(tmp_path / "run", "classified", "2026-01-01T00:00:00Z")
    with sqlite3.connect(database) as db:
        db.execute("UPDATE observation SET response_status=?, verification_json=?", (response, json.dumps(verification) if verification is not None else None))
        if status is None:
            db.execute("DELETE FROM check_result")
        else:
            db.execute("UPDATE check_result SET status=?, exception_json=?", (status, json.dumps(exception) if exception is not None else None))
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    run = embedded_data(output)["runs"][0]
    assert run["operations"][0]["kind"] == expected
    assert run["operation_counts"]["passed"] == int(expected in ("pass", "accepted"))


def test_atomic_publication_and_owned_data_cleanup(tmp_path, monkeypatch):
    database = make_database(tmp_path / "run", "first", "2026-01-01T00:00:00Z")
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    original_html = output.read_bytes()
    original_data = {file: file.read_bytes() for file in output.with_suffix(".data").rglob("*") if file.is_file()}
    # An unsuccessful publication must not orphan the old HTML's dependencies.
    original_replace = Path.replace
    def fail_html_replace(source, target):
        if Path(target) == output:
            raise OSError("interrupted publication")
        return original_replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_html_replace)
        with pytest.raises(OSError, match="interrupted publication"):
            generate_report(tmp_path, output)
    assert output.read_bytes() == original_html
    assert all(file.read_bytes() == value for file, value in original_data.items())
    assert {file for file in output.with_suffix(".data").rglob("*") if file.is_file()} == set(original_data)
    assert not list(tmp_path.glob(".report-*"))
    unrelated = output.with_suffix(".data") / "user-notes.txt"
    unrelated.write_text("keep me")
    with sqlite3.connect(database) as db:
        db.execute("UPDATE build SET build_number='replacement'")
    generate_report(tmp_path, output)
    assert embedded_data(output)["runs"][0]["build_number"] == "replacement"
    assert unrelated.read_text() == "keep me"
    assert all(not file.exists() for file in original_data)


def test_unlinked_cases_and_scenarios_remain_inspectable(tmp_path):
    database = make_database(tmp_path / "run", "unlinked", "2026-01-01T00:00:00Z")
    with sqlite3.connect(database) as db:
        db.execute("UPDATE observation SET operation_id=NULL")
        db.execute("UPDATE scenario SET operation_id=NULL")
    output = tmp_path / "report.html"
    generate_report(tmp_path, output)
    run = embedded_data(output)["runs"][0]
    assert run["case_count"] == run["check_count"] == 1
    assert run["operations"][-1]["path"] == "Unlinked evidence"
    assert run["operations"][-1]["cases"][0]["checks"][0]["status"] == "success"
    assert len(run["operations"][-1]["scenarios"]) == 2


def test_windows_publication_retries_transient_file_locks(tmp_path, monkeypatch):
    import report

    if os.name != "nt":
        pytest.skip("Windows file-lock retry")
    source = tmp_path / "stage"
    source.write_text("complete")
    destination = tmp_path / "published"
    original_replace = Path.replace
    calls = []
    def locked_once(path, target):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("scanner holds a new file")
        return original_replace(path, target)
    monkeypatch.setattr(Path, "replace", locked_once)
    monkeypatch.setattr(report.time, "sleep", lambda _: None)
    report.publish_path(source, destination)
    assert destination.read_text() == "complete"
    assert len(calls) == 2
