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

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner.py"
EXCEPTION = {"method": "POST", "path": "/items", "failure_type": "AcceptedNegativeData",
             "reason": "Fixture accepts missing body", "owner": "test team", "expiry": "2099-01-01"}


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
        build = db.execute("SELECT build_id, component_version, configuration_id, config_sha256, schema_sha256, passed FROM build").fetchone()
        assert build[:3] == ("local-123", "1.2.3", "integration")
        assert len(build[3]) == len(build[4]) == 64 and build[5] == 1
        assert db.execute("SELECT COUNT(*) FROM operation WHERE tested = 1").fetchone()[0] == verdict["operations"]["tested"]
        assert db.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == verdict["observed_cases"]
        assert db.execute("SELECT COUNT(*) FROM check_result WHERE status = 'failure' AND exception_json IS NOT NULL").fetchone()[0] >= 1
        assert db.execute("SELECT percent FROM coverage WHERE dimension = 'operation'").fetchone()[0] == coverage["summary"]["operations"]["percent"]
    assert verdict["observed_failures"][0]["failure_type"] == "AcceptedNegativeData"
    assert (out / "junit.xml").stat().st_size and (out / "coverage.html").stat().st_size
    assert all(b"test-secret" not in path.read_bytes() for path in out.iterdir() if path.is_file())


def test_unexplained_and_expired_exception_fail(tmp_path, server):
    proc, out = run(tmp_path, server, exceptions=[])
    assert proc.returncode == 1
    assert "Unexplained" in json.loads((out / "verdict.json").read_text())["issues"][0]
    assert (out / "evidence.sqlite").exists()
    expired = {**EXCEPTION, "expiry": "2020-01-01"}
    proc2, out2 = run(tmp_path / "expired", server, exceptions=[expired])
    assert proc2.returncode == 1
    assert any("Unexplained" in x for x in json.loads((out2 / "verdict.json").read_text())["issues"])


def test_thresholds_independent_and_omitted(tmp_path, server):
    proc, out = run(tmp_path, server, thresholds={"min_parameters_coverage": 100})
    assert proc.returncode == 1
    assert any("parameters coverage" in issue for issue in json.loads((out / "verdict.json").read_text())["issues"])
    assert (out / "evidence.sqlite").exists()


def test_zero_operations(tmp_path, server):
    proc, out = run(tmp_path, server, local=True, schema={**SCHEMA, "paths": {}})
    assert proc.returncode != 0
    assert out is not None
    assert (out / "evidence.sqlite").exists()
    assert "Zero tested operations" in json.loads((out / "verdict.json").read_text())["issues"]


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
    assert pipe["image"] == "ppwcode/api-mitigation-test:1.0.0"


@pytest.mark.parametrize("wrapper,build_env,expected", [
    ("action", {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "101"}, "github-101-1"),
    ("pipe", {"BITBUCKET_BUILD_NUMBER": "202"}, "bitbucket-202"),
])
def test_shell_wrapper_against_api(tmp_path, server, wrapper, build_env, expected):
    proc, out = run(tmp_path, server, wrapper=wrapper, build_env=build_env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((out / "verdict.json").read_text())["build"]["build_id"] == expected
