import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

import pytest
import yaml

from fixture_api import SCHEMA
from runner import assess, console_summary, exception_for, parse_events, path_glob_regex, read_config, schemathesis_executable, select_operations, write_database

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner.py"
EXCEPTION = {"method": "POST", "path": "/items", "failure_type": "AcceptedNegativeData",
             "reason": "Fixture accepts missing body", "owner": "test team", "expiry": "2099-01-01"}
GLOBAL_EXCEPTION = {key: value for key, value in EXCEPTION.items() if key not in {"method", "path"}}


def auth_event(child_count=1):
    value = {"method": "GET", "path": "/items"}
    cases = {"parent": {"value": value, "parent_id": None, "transition": None}}
    interactions = {"parent": {"response": {"status_code": 200}}}
    for index in range(child_count):
        cases[f"auth-{index}"] = {"value": value.copy(), "parent_id": "parent", "transition": None}
        interactions[f"auth-{index}"] = {"response": {"status_code": 401}}
    return {"ScenarioFinished": {"phase": "fuzzing", "status": "success", "recorder": {
        "label": "GET /items", "cases": cases, "interactions": interactions,
        "checks": {"parent": [{"name": "ignored_auth", "status": "success"}]}}}}


@pytest.mark.parametrize("problem", [None, "forbidden", "missing_parent", "self_parent", "other_operation",
                                     "transition", "missing_parent_response", "missing_response", "wrong_response",
                                     "missing_auth_check", "failed_auth_check", "invalid_auth_check", "conflicting_auth_check",
                                     "own_failure", "other_scenario", "unaccounted_without_checks"])
def test_auth_subrequests_require_successful_parent_evidence(tmp_path, problem):
    event = auth_event()
    recorder = event["ScenarioFinished"]["recorder"]
    child = recorder["cases"]["auth-0"]
    if problem in ("missing_parent", "self_parent"):
        child["parent_id"] = "absent" if problem == "missing_parent" else "auth-0"
    elif problem == "other_operation":
        child["value"]["method"] = "POST"
    elif problem == "transition":
        child["transition"] = {"id": "stateful-link"}
    elif problem == "missing_parent_response":
        recorder["interactions"].pop("parent")
    elif problem in ("forbidden", "missing_response", "wrong_response"):
        recorder["interactions"]["auth-0"]["response"]["status_code"] = {"forbidden": 403, "missing_response": None, "wrong_response": 200}[problem]
    elif problem == "missing_auth_check":
        recorder["checks"]["parent"][0]["name"] = "other_check"
    elif problem in ("failed_auth_check", "invalid_auth_check"):
        recorder["checks"]["parent"][0]["status"] = "failure" if problem == "failed_auth_check" else "unknown"
    elif problem == "conflicting_auth_check":
        recorder["checks"]["parent"].append({"name": "ignored_auth", "status": "failure"})
    elif problem == "own_failure":
        recorder["checks"]["auth-0"] = [{"name": "custom", "status": "failure", "failure_info": {"failure": {"type": "BadResponse"}}}]
    events = [event]
    if problem == "other_scenario":
        separate = auth_event()
        separate["ScenarioFinished"]["recorder"]["cases"].pop("auth-0")
        recorder["cases"].pop("parent")
        recorder["checks"] = {}
        events.insert(0, separate)
    path = tmp_path / "events.ndjson"
    path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
    observations, scenarios = parse_events(path)
    report = {"complete": True, "stop_reason": "completed", "operations": {"tested": 1 if problem != "other_operation" else 2},
              "test_cases": {"without_checks": 2 if problem == "unaccounted_without_checks" else int(problem != "own_failure")}}
    expected = {("GET", "/items")} | ({("POST", "/items")} if problem == "other_operation" else set())
    verdict, _ = assess({}, report, {"tracecov_version": "test", "operations": []}, observations, scenarios, expected, 0)
    verified = problem in (None, "forbidden", "unaccounted_without_checks")
    assert verdict["cases"]["verified_by_parent"] == int(verified)
    assert verdict["passed"] == (problem in (None, "forbidden"))
    auth = next(o for o in observations if o["case_id"] == "auth-0")
    assert bool(auth["verification"]) == verified
    assert auth["parent_case_id"] == child["parent_id"]


def test_auth_subrequest_evidence_round_trip(tmp_path):
    from report import load_database

    path = tmp_path / "events.ndjson"
    path.write_text(json.dumps(auth_event(1146)), encoding="utf-8")
    observations, scenarios = parse_events(path)
    verdict, _ = assess({}, {"complete": True, "stop_reason": "completed", "operations": {"tested": 1},
                            "test_cases": {"without_checks": 1146}},
                        {"tracecov_version": "test", "operations": []}, observations, scenarios, {("GET", "/items")}, 0)
    assert verdict["passed"]
    assert verdict["cases"] == {"total": 1147, "verified_by_parent": 1146}
    assert verdict["checks"]["total"] == 1
    assert "1146 verified by parent check, 1147 total" in console_summary(verdict, tmp_path, color=False)
    identity = dict.fromkeys(("component_version", "configuration_id", "schema_location", "base_url", "config_sha256",
                              "schema_sha256", "started_at", "schemathesis_version", "tracecov_version"), "test")
    identity.update(build_id="auth-run", config={})
    database = tmp_path / "evidence.sqlite"
    write_database(database, identity, {("GET", "/items")}, observations, scenarios, {}, verdict)
    run = load_database(database, tmp_path / "report.html")[0]
    assert run["passed"]
    assert run["verified_by_parent_count"] == 1146
    assert run["case_count"] == 1147
    assert run["check_count"] == run["success_count"] == 1
    child = run["operations"][0]["cases"][1]
    assert child["checks"] == []
    assert child["parent_case_id"] == "parent"
    assert child["verification"] == {"parent_case_id": "parent", "check": "ignored_auth", "status": "success"}


@pytest.mark.parametrize("problem", [None, "missing_checks", "missing_response", "invalid_status", "failure", "reported_failure", "error"])
def test_passed_operations_require_complete_successful_evidence(problem):
    expected = {("GET", "/items"), ("POST", "/items"), ("GET", "/untested")}
    observations = [{"case_id": "ok", "method": "GET", "path": "/items", "response_status": 200,
                     "checks": [{"status": "success"}]},
                    {"case_id": "post", "method": "POST", "path": "/items", "response_status": 200,
                     "checks": [{"status": "success"}]}]
    report = {"complete": True, "stop_reason": "completed", "operations": {"tested": 2}}
    scenarios = [{"operation": "GET /items", "status": "success"}]
    coverage = {"tracecov_version": "test", "operations": [], "summary": {"operations": {"percent": 66.7}}}
    if problem in ("missing_checks", "missing_response", "invalid_status", "failure"):
        observation = {**observations[0], "case_id": "bad"}
        if problem == "missing_checks":
            observation["checks"] = []
        elif problem == "missing_response":
            observation["response_status"] = None
        elif problem == "invalid_status":
            observation["checks"] = [{"status": "unknown"}]
        else:
            observation["checks"] = [{"status": "failure", "failure_info": {"failure": {"type": "ServerError"}}}]
        observations.append(observation)
    elif problem == "reported_failure":
        report["failures"] = [{"type": "ServerError", "operations": ["GET /items"]}]
    elif problem == "error":
        scenarios.append({"operation": "GET /items", "status": "error"})
    verdict, _ = assess({}, report, coverage, observations, scenarios, expected, 0)
    assert verdict["operations"] == {"schema": 3, "tested": 2, "passed": 2 if problem is None else 1,
                                     "skipped": ["GET /untested"]}


@pytest.mark.parametrize("passed", [True, False])
def test_console_summary_is_compact(passed):
    verdict = {"passed": passed, "issues": [f"Missing check or response evidence for {i}" for i in range(2000)],
               "operations": {"schema": 6, "tested": 6, "passed": 4},
               "checks": {"failed": 41, "unexplained": 39, "accepted": 2, "total": 19849},
               "duration_seconds": 24.3,
               "coverage_percent": {"operation": 80, "parameters": 25.5, "keywords": 0, "examples": None, "responses": 50}}
    coverage = {"summary": {"operations": {"covered": 6, "total": 6},
                            "parameters": {"covered": 8, "total": 16}}}
    assert console_summary(verdict, Path("artifacts/run"), coverage, color=False).splitlines() == [
        "Verdict:\t" + ("PASS" if passed else "FAIL"),
        f"Evidence:\t{Path('artifacts/run')}", "",
        "Operations:\t2 failed, 4 passed, 6 discovered",
        "Checks:\t\t41 failed (39 unexplained), 2 accepted failures, 19849 total",
        "Duration:\t24.3s", "",
        "==================== Coverage Summary ====================",
        "Operations:\t\t80%   \t(6/6)",
        "Parameters:\t\t25.5% \t(8/16)",
        "Keywords:\t\t0%    \t(N/A)",
        "Examples:\t\tN/A   \t(N/A)",
        "Responses:\t\t50%   \t(N/A)",
        "==========================================================",
    ]


@pytest.mark.parametrize("percent,minimum,shade", [(79, 80, 31), (80, 80, 32), (100, 80, 32),
                                                   (50, None, 33), (None, None, 33), (None, 80, 31)])
def test_console_summary_colors(percent, minimum, shade, monkeypatch):
    verdict = {"passed": False, "operations": {"schema": 6, "passed": 4},
               "coverage_percent": {"operation": percent}, "duration_seconds": 24.3,
               "checks": {"failed": 41, "unexplained": 39, "accepted": 2, "total": 19849}}
    config = {} if minimum is None else {"min_operation_coverage": minimum}
    output = console_summary(verdict, Path("artifacts/run"), config=config, color=True)
    assert "\033[31mFAIL\033[0m" in output
    assert "\033[31m2 failed\033[0m" in output
    assert "\033[32m4 passed\033[0m" in output
    assert "\033[37m6 discovered\033[0m" in output
    assert "\033[31m41 failed (39 unexplained)\033[0m" in output
    assert "\033[33m2 accepted failures\033[0m" in output
    assert "\033[37m19849 total\033[0m" in output
    assert "\033[37m24.3s\033[0m" in output
    label = f"{percent:g}%" if percent is not None else "N/A"
    assert f"Operations:\t\t\033[{shade}m{label.ljust(6)}\033[0m" in output
    verdict["passed"] = True
    assert "\033[32mPASS\033[0m" in console_summary(verdict, Path("artifacts/run"), color=True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033[" not in console_summary(verdict, Path("artifacts/run"))


def test_check_totals_include_accepted_and_missing_response_evidence():
    config = {"check_exceptions": [GLOBAL_EXCEPTION]}
    observations = [{"case_id": "case", "method": "GET", "path": "/items", "response_status": None,
                     "checks": [{"status": "success"},
                                {"status": "failure", "failure_info": {"failure": {"type": "AcceptedNegativeData"}}},
                                {"status": "failure", "failure_info": {"failure": {"type": "ServerError"}}}]}]
    verdict, _ = assess(config, {"operations": {"tested": 1}}, {}, observations, [], {("GET", "/items")}, 1)
    assert verdict["checks"] == {"total": 3, "failed": 2, "accepted": 1, "unexplained": 1}
    assert verdict["operations"]["passed"] == 0


@pytest.mark.parametrize("fmt", ["json", "yaml"])
@pytest.mark.parametrize("exception", [EXCEPTION, GLOBAL_EXCEPTION])
def test_check_exception_config(tmp_path, fmt, exception):
    config = {"schema": "schema.yaml", "base_url": "http://example.test",
              "component_version": "1", "configuration_id": "test", "check_exceptions": [exception]}
    path = tmp_path / f"config.{fmt}"
    path.write_text(json.dumps(config) if fmt == "json" else yaml.safe_dump(config), encoding="utf-8")
    assert read_config(path)[0] == config


@pytest.mark.parametrize("exception", [
    {**GLOBAL_EXCEPTION, "method": "GET"},
    {**GLOBAL_EXCEPTION, "path": "/items"},
    {**GLOBAL_EXCEPTION, "extra": "unexpected"},
    {key: value for key, value in GLOBAL_EXCEPTION.items() if key != "owner"},
    {**GLOBAL_EXCEPTION, "reason": ""},
    {**GLOBAL_EXCEPTION, "failure_type": None},
    {**GLOBAL_EXCEPTION, "expiry": "invalid"},
    {**EXCEPTION, "method": "FAKE"},
    {**EXCEPTION, "path": "items"},
    {**EXCEPTION, "method": None},
    "invalid",
])
def test_invalid_check_exception_config(tmp_path, exception):
    config = {"schema": "schema.yaml", "base_url": "http://example.test",
              "component_version": "1", "configuration_id": "test", "check_exceptions": [exception]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        read_config(path)


@pytest.mark.parametrize("method,path", [("POST", "/items"), ("GET", "/other"), ("DELETE", "/items/{id}")])
def test_global_check_exception_matches_all_operations(method, path):
    config = {"check_exceptions": [GLOBAL_EXCEPTION]}
    assert exception_for(config, method, path, "AcceptedNegativeData") == GLOBAL_EXCEPTION
    assert exception_for(config, method, path, "ServerError") is None
    expired = {**GLOBAL_EXCEPTION, "expiry": "2020-01-01"}
    assert exception_for({"check_exceptions": [expired]}, method, path, "AcceptedNegativeData") is None


def test_scoped_and_global_exceptions_can_coexist():
    scoped = {**EXCEPTION, "method": "post"}
    config = {"check_exceptions": [scoped, GLOBAL_EXCEPTION]}
    assert exception_for(config, "POST", "/items", "AcceptedNegativeData") == scoped
    assert exception_for(config, "GET", "/items", "AcceptedNegativeData") == GLOBAL_EXCEPTION
    assert exception_for(config, "POST", "/other", "AcceptedNegativeData") == GLOBAL_EXCEPTION
    config["check_exceptions"] = [scoped]
    assert exception_for(config, "GET", "/items", "AcceptedNegativeData") is None
    assert exception_for(config, "POST", "/other", "AcceptedNegativeData") is None


def test_schemathesis_cli_in_user_scripts(tmp_path, monkeypatch):
    import runner

    scripts = tmp_path / "scripts"
    user_scripts = tmp_path / "user-scripts"
    user_scripts.mkdir()
    executable = user_scripts / ("schemathesis.exe" if os.name == "nt" else "schemathesis")
    executable.touch()
    monkeypatch.setattr(runner.sysconfig, "get_path", lambda name, scheme=None: str(user_scripts if scheme else scripts))
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert schemathesis_executable() == str(executable)


@pytest.fixture
def server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "FIXTURE_TOKEN": "test-secret"}
    process = subprocess.Popen([sys.executable, str(ROOT / "tests/fixture_api.py"), str(port)], env=env)
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                break
        except OSError:
            time.sleep(.05)
    yield f"http://127.0.0.1:{port}"
    process.terminate()
    process.wait(timeout=5)


def run(tmp_path, server, *, fmt="json", local=False, exceptions=None, thresholds=None, token="test-secret", schema=None, build_env=None, wrapper=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = {"schema": f"{server}/openapi.json", "base_url": server,
              "component_version": "1.2.3", "configuration_id": "integration",
              "check_exceptions": [EXCEPTION] if exceptions is None else exceptions}
    if local:
        (tmp_path / "schema.yaml").write_text(yaml.safe_dump(schema or SCHEMA), encoding="utf-8")
        config["schema"] = "schema.yaml"
    config.update(thresholds or {})
    path = tmp_path / ("config.json" if fmt == "json" else "config.yaml")
    path.write_text(json.dumps(config) if fmt == "json" else yaml.safe_dump(config), encoding="utf-8")
    env = {**os.environ, "API_MITIGATION_OUTPUT_DIR": str(tmp_path / "out")}
    for key in ("API_MITIGATION_BUILD_ID", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "BITBUCKET_BUILD_NUMBER", "API_MITIGATION_BEARER_TOKEN"):
        env.pop(key, None)
    env.update({"API_MITIGATION_BUILD_ID": "local-123"} if build_env is None else build_env)
    if token is not None:
        env["API_MITIGATION_BEARER_TOKEN"] = token
    command = [sys.executable, str(RUNNER), "--config", str(path)]
    if wrapper:
        git_sh = Path("C:/Program Files/Git/usr/bin/sh.exe")
        shell = str(git_sh) if git_sh.is_file() else shutil.which("sh") or shutil.which("bash")
        if not shell:
            pytest.skip("Shell wrapper needs sh or bash")
        def shell_path(value):
            value = Path(value).as_posix()
            return f"/{value[0].lower()}/{value[3:]}" if len(value) > 2 and value[1:3] == ":/" else value

        env["PATH"] = shell_path(Path(sys.executable).parent) + ":/usr/bin:/bin"
        if wrapper == "pipe":
            env["CONFIG"] = shell_path(path)
            command = [shell, shell_path(ROOT / "bitbucket-pipe/run.sh")]
        else:
            command = [shell, shell_path(ROOT / "bitbucket-pipe/run.sh"), "--config", shell_path(path)]
    proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    dirs = [p for p in (tmp_path / "out").iterdir() if p.is_dir()] if (tmp_path / "out").exists() else []
    return proc, (dirs[0] if dirs else None)


@pytest.mark.parametrize("fmt,local", [("json", False), ("yaml", True)])
def test_config_schema_exception_and_database(tmp_path, server, fmt, local):
    proc, out = run(tmp_path, server, fmt=fmt, local=local, thresholds={"min_operation_coverage": 100})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    verdict = json.loads((out / "verdict.json").read_text())
    coverage = json.loads((out / "coverage.json").read_text())
    with sqlite3.connect(out / "evidence.sqlite") as db:
        db.execute("PRAGMA foreign_keys = ON")
        build = db.execute("SELECT id, build_number, component_version, configuration_id, config_sha256, schema_sha256, passed FROM build").fetchone()
        assert build[0] > 0 and build[1:4] == ("local-123", "1.2.3", "integration")
        assert len(build[4]) == len(build[5]) == 64 and build[6] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        for table in ("build", "operation", "scenario", "observation", "check_result", "coverage"):
            assert ("id", "INTEGER", 1) in {(row[1], row[2], row[5]) for row in db.execute(f"PRAGMA table_info({table})")}
        for table, columns in {"operation": {"build_id"}, "scenario": {"build_id", "operation_id"},
                               "observation": {"build_id", "operation_id"}, "check_result": {"observation_id"},
                               "coverage": {"build_id"}}.items():
            assert columns <= {row[3] for row in db.execute(f"PRAGMA foreign_key_list({table})")}
        assert db.execute("SELECT COUNT(*) FROM operation WHERE expected = 1 AND tested = 1").fetchone()[0] == verdict["operations"]["tested"]
        assert db.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == verdict["observed_cases"]
        assert db.execute("SELECT COUNT(*) FROM check_result").fetchone()[0] == verdict["checks"]["total"]
        assert db.execute("SELECT COUNT(*) FROM check_result WHERE status = 'failure'").fetchone()[0] == verdict["checks"]["failed"]
        assert db.execute("SELECT COUNT(*) FROM check_result WHERE status = 'failure' AND exception_json IS NOT NULL").fetchone()[0] == verdict["checks"]["accepted"]
        assert verdict["checks"]["unexplained"] == 0
        assert db.execute("SELECT COUNT(*) FROM check_result WHERE status = 'failure' AND exception_json IS NOT NULL").fetchone()[0] >= 1
        assert db.execute("SELECT percent FROM coverage WHERE dimension = 'operation'").fetchone()[0] == coverage["summary"]["operations"]["percent"]
        assert db.execute("SELECT COUNT(*) FROM observation o JOIN operation p ON o.operation_id = p.id WHERE o.build_id = p.build_id").fetchone()[0] == verdict["observed_cases"]
        assert db.execute("SELECT COUNT(*) FROM check_result c JOIN observation o ON c.observation_id = o.id").fetchone()[0] == db.execute("SELECT COUNT(*) FROM check_result").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM scenario WHERE operation_id IS NULL").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO coverage (build_id, dimension) VALUES (?, ?)", (build[0] + 1, "invalid"))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM build WHERE id = ?", (build[0],))
    assert verdict["observed_failures"][0]["failure_type"] == "AcceptedNegativeData"
    assert verdict["duration_seconds"] > 0
    assert "Coverage Summary" in proc.stdout and "accepted failures" in proc.stdout
    assert (out / "junit.xml").stat().st_size and (out / "coverage.html").stat().st_size
    assert (out.parent / "report.html").stat().st_size
    with sqlite3.connect(out / "evidence.sqlite") as db:
        request_json, response_json = db.execute("SELECT request_json, response_json FROM observation WHERE response_status IS NOT NULL LIMIT 1").fetchone()
        assert json.loads(request_json)["uri"].startswith(server)
        assert json.loads(response_json)["status"] is not None
    assert all(b"test-secret" not in path.read_bytes() for path in out.iterdir() if path.is_file())
    assert b"test-secret" not in (out.parent / "report.html").read_bytes()


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_global_exception_preserves_failure_evidence(tmp_path, server, fmt):
    proc, out = run(tmp_path, server, fmt=fmt, exceptions=[GLOBAL_EXCEPTION])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    verdict = json.loads((out / "verdict.json").read_text())
    assert verdict["passed"] and verdict["observed_failures"]
    assert all(failure["exception"] == GLOBAL_EXCEPTION for failure in verdict["observed_failures"])
    with sqlite3.connect(out / "evidence.sqlite") as db:
        failures = db.execute("SELECT failure_type, exception_json FROM check_result WHERE status = 'failure'").fetchall()
    assert failures
    assert all(kind == "AcceptedNegativeData" and json.loads(exception) == GLOBAL_EXCEPTION for kind, exception in failures)
    assert "AcceptedNegativeData" in (out / "events.ndjson").read_text()


def test_unexplained_and_expired_exception_fail(tmp_path, server):
    proc, out = run(tmp_path, server, exceptions=[])
    assert proc.returncode == 1
    assert "Unexplained" in json.loads((out / "verdict.json").read_text())["issues"][0]
    assert (out / "evidence.sqlite").exists()
    assert (out.parent / "report.html").exists()
    with sqlite3.connect(out / "evidence.sqlite") as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM check_result WHERE status = 'failure' AND exception_json IS NULL").fetchone()[0] >= 1
    expired = {**EXCEPTION, "expiry": "2020-01-01"}
    proc2, out2 = run(tmp_path / "expired", server, exceptions=[expired])
    assert proc2.returncode == 1
    assert any("Unexplained" in x for x in json.loads((out2 / "verdict.json").read_text())["issues"])


def test_thresholds_independent_and_omitted(tmp_path, server):
    proc, out = run(tmp_path, server, thresholds={"min_parameters_coverage": 100})
    assert proc.returncode == 1
    assert any("parameters coverage" in issue for issue in json.loads((out / "verdict.json").read_text())["issues"])
    assert (out / "evidence.sqlite").exists()
    assert (out.parent / "report.html").exists()


def test_zero_operations(tmp_path, server):
    proc, out = run(tmp_path, server, local=True, schema={**SCHEMA, "paths": {}})
    assert proc.returncode != 0
    assert out is not None
    assert (out / "evidence.sqlite").exists()
    assert (out.parent / "report.html").exists()
    assert "Zero tested operations" in json.loads((out / "verdict.json").read_text())["issues"]
    with sqlite3.connect(out / "evidence.sqlite") as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_report_generation_warning_preserves_success_exit(tmp_path, server, monkeypatch, capsys):
    import report
    import runner

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"schema": f"{server}/openapi.json", "base_url": server,
                                  "component_version": "1.0", "configuration_id": "integration",
                                  "read_only": True, "test_operations": [{"method": "GET", "path": "/items"}]}), encoding="utf-8")
    monkeypatch.setenv("API_MITIGATION_BUILD_ID", "report-warning")
    monkeypatch.setenv("API_MITIGATION_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("API_MITIGATION_BEARER_TOKEN", "test-secret")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("BITBUCKET_BUILD_NUMBER", raising=False)
    monkeypatch.setattr(sys, "argv", ["runner.py", "--config", str(config)])

    def fail(*args):
        raise OSError("report disk full")

    monkeypatch.setattr(report, "generate_report", fail)
    assert runner.main() == 0
    assert "Warning: HTML report generation failed: report disk full" in capsys.readouterr().err
    assert next((tmp_path / "out").glob("*/evidence.sqlite")).exists()


@pytest.mark.parametrize("selected", [
    [{"method": "get", "path": "/other"}, {"method": "POST", "path": "/items"}],
    [{"method": "get", "path": "/oth*"}, {"method": "GET", "path": "/other/**"},
     {"method": "POST", "path": "/it?ms"}],
])
def test_selected_operations_only(tmp_path, server, selected):
    schema = {**SCHEMA, "paths": {**SCHEMA["paths"], "/other": SCHEMA["paths"]["/items"]}}
    proc, out = run(tmp_path, server, local=True, schema=schema,
                    thresholds={"test_operations": selected})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    verdict = json.loads((out / "verdict.json").read_text())
    assert verdict["operations"] == {"schema": 2, "tested": 2, "passed": 2, "skipped": []}
    with sqlite3.connect(out / "evidence.sqlite") as db:
        assert db.execute("SELECT method, path FROM operation WHERE expected = 1 ORDER BY method, path").fetchall() == [("GET", "/other"), ("POST", "/items")]
        assert set(db.execute("SELECT DISTINCT p.method, p.path FROM observation o JOIN operation p ON p.id = o.operation_id")) == {("GET", "/other"), ("POST", "/items")}


@pytest.mark.parametrize("pattern,path,matches", [
    ("/v1/admins/**", "/v1/admins", True),
    ("/v1/admins/**", "/v1/admins/{id}/roles", True),
    ("/v1/admins/**", "/v1/admins-other", False),
    ("/v1/admins/*", "/v1/admins/{id}", True),
    ("/v1/admins/*", "/v1/admins/{id}/roles", False),
    ("/v1/**/roles", "/v1/roles", True),
    ("/v1/**/roles", "/v1/admins/{id}/roles", True),
    ("/item?", "/items", True),
    ("/item?", "/item/", False),
    ("/items/{id}", "/items/{id}", True),
    ("/items/{id}", "/items/123", False),
    ("/items/[id].json", "/items/iXjson", False),
    ("/items/[id].json", "/items/[id].json", True),
    ("/items/**", "/ITEMS/123", False),
])
def test_path_glob_matching(pattern, path, matches):
    import re

    assert bool(re.fullmatch(path_glob_regex(pattern), path)) is matches


def test_glob_selectors_expand_and_deduplicate():
    expected = {(method, path) for method in ("GET", "PUT", "POST")
                for path in ("/v1/admins", "/v1/admins/{id}", "/v1/admins/{id}/roles")}
    available = expected | {("DELETE", "/v1/admins"), ("GET", "/v1/users")}
    selectors = [{"method": method, "path": "/v1/admins/**"} for method in ("get", "PUT", "POST")]
    selectors.append({"method": "GET", "path": "/v1/admins/{id}"})
    assert select_operations(selectors, available) == expected


def test_relational_event_edge_cases(tmp_path):
    path = tmp_path / "evidence.sqlite"
    identity = {"build_id": "local-edge", "component_version": "1.0", "configuration_id": "test",
                "schema_location": "schema.yaml", "base_url": "http://example.test", "config_sha256": "a" * 64,
                "schema_sha256": "b" * 64, "started_at": "2026-01-01T00:00:00+00:00",
                "schemathesis_version": "1", "tracecov_version": "1", "config": {}}
    checks = [{"name": "status", "status": "success"}]
    observations = [{"case_id": "reused", "method": "GET", "path": "/items", "phase": "fuzzing",
                     "response_status": 200, "checks": checks},
                    {"case_id": "reused", "method": "GET", "path": "/items", "phase": "fuzzing",
                     "response_status": 201, "checks": checks},
                    {"case_id": "stray", "method": "PATCH", "path": "/stray", "phase": "fuzzing",
                     "response_status": 200, "checks": checks}]
    scenarios = [{"operation": "GET /items", "phase": "fuzzing", "status": "success", "skip_reason": None},
                 {"operation": "PATCH /stray", "phase": "fuzzing", "status": "success", "skip_reason": None},
                 {"operation": "unparseable", "phase": "examples", "status": "skip", "skip_reason": "no examples"}]
    write_database(path, identity, {("GET", "/items")}, observations, scenarios, {}, {"passed": False})
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM observation WHERE case_id = 'reused'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM check_result").fetchone()[0] == 3
        assert db.execute("SELECT expected, tested FROM operation WHERE method = 'PATCH'").fetchone() == (0, 1)
        assert db.execute("SELECT operation_id, skip_reason FROM scenario WHERE operation = 'unparseable'").fetchone() == (None, "no examples")
        assert db.execute("SELECT COUNT(*) FROM scenario WHERE operation_id IS NOT NULL").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO check_result (observation_id) VALUES (999)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM observation WHERE case_id = 'stray'")
        other_build = db.execute("INSERT INTO build (build_number) VALUES ('other')").lastrowid
        first_operation = db.execute("SELECT id FROM operation WHERE method = 'GET'").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO scenario (build_id, operation_id) VALUES (?, ?)", (other_build, first_operation))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO observation (build_id, operation_id) VALUES (?, ?)", (other_build, first_operation))


@pytest.mark.parametrize("operations,error", [
    ([], "nonempty list"),
    ([{"method": "GET", "path": "/missing"}], "missing from schema"),
    ([{"method": "GET", "path": "/missing/**"}], "selectors matched no operations"),
    ([{"method": "GET", "path": "/items"}, {"method": "PUT", "path": "/items/**"}], "selectors matched no operations"),
    ([{"method": "GET", "path": "/items"}, {"method": "get", "path": "/items"}], "Duplicate test operation"),
    ([{"method": "FAKE", "path": "/items"}], "Invalid test operation"),
])
def test_invalid_selected_operations_fail_with_evidence(tmp_path, server, operations, error):
    proc, out = run(tmp_path, server, thresholds={"test_operations": operations})
    assert proc.returncode == 2
    assert error in (out / "runner-error.json").read_text()
    assert (out / "evidence.sqlite").exists()


@pytest.mark.parametrize("path", ["/items", "/items/**"])
def test_selected_operations_respect_read_only(tmp_path, server, path):
    proc, out = run(tmp_path, server, thresholds={"read_only": True, "test_operations": [{"method": "POST", "path": path}]})
    assert proc.returncode == 2
    assert "excluded by read_only" in (out / "runner-error.json").read_text()


def test_build_inference_and_auth(tmp_path, server):
    proc, out = run(tmp_path, server, build_env={"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "71", "GITHUB_RUN_ATTEMPT": "2"})
    assert proc.returncode == 0, proc.stdout
    assert json.loads((out / "verdict.json").read_text())["build"]["build_id"] == "github-71-2"
    proc2, out2 = run(tmp_path / "second", server, build_env={"BITBUCKET_BUILD_NUMBER": "9"})
    assert proc2.returncode == 0, proc2.stdout
    assert json.loads((out2 / "verdict.json").read_text())["build"]["build_id"] == "bitbucket-9"


def test_missing_auth_fails_with_evidence(tmp_path, server):
    proc, out = run(tmp_path, server, token=None)
    assert proc.returncode != 0
    assert (out / "evidence.sqlite").exists()


def test_direct_use_requires_build_id(tmp_path, server):
    proc, out = run(tmp_path, server, build_env={})
    assert proc.returncode == 2 and out is None
    assert "requires API_MITIGATION_BUILD_ID" in (tmp_path / "out/runner-error.json").read_text()


def test_wrapper_contracts():
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    pipe = yaml.safe_load((ROOT / "pipe.yml").read_text())
    assert list(action["inputs"]) == ["config"]
    assert action["runs"]["args"] == ["--config", "${{ inputs.config }}"]
    assert [item["name"] for item in pipe["variables"]] == ["CONFIG"]
    assert pipe["image"] == "daviddkppw/api-mitigation-test:0.2.2"


@pytest.mark.parametrize("wrapper,build_env,expected", [
    ("action", {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "101"}, "github-101-1"),
    ("pipe", {"BITBUCKET_BUILD_NUMBER": "202"}, "bitbucket-202"),
])
def test_shell_wrapper_against_api(tmp_path, server, wrapper, build_env, expected):
    proc, out = run(tmp_path, server, wrapper=wrapper, build_env=build_env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((out / "verdict.json").read_text())["build"]["build_id"] == expected
