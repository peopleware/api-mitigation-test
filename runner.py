"""One-target API test runner and evidence writer."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import urlparse
from urllib.request import urlopen

import yaml

DIMENSIONS = ("operation", "parameters", "keywords", "examples", "responses")
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_config(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError("Config must be an object")
    required = {"schema", "base_url", "component_version", "configuration_id"}
    allowed = required | {"check_exceptions"} | {f"min_{d}_coverage" for d in DIMENSIONS}
    if missing := required - value.keys():
        raise ValueError(f"Missing config keys: {sorted(missing)}")
    if extra := value.keys() - allowed:
        raise ValueError(f"Unknown config keys: {sorted(extra)}")
    for name in required:
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    if urlparse(value["base_url"]).scheme not in ("http", "https"):
        raise ValueError("base_url must be an HTTP(S) URL")
    for dim in DIMENSIONS:
        key = f"min_{dim}_coverage"
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float)) or not 0 <= value[key] <= 100):
            raise ValueError(f"{key} must be a number from 0 to 100")
    exceptions = value.get("check_exceptions", [])
    if not isinstance(exceptions, list):
        raise ValueError("check_exceptions must be a list")
    for item in exceptions:
        if not isinstance(item, dict) or set(item) != {"method", "path", "failure_type", "reason", "owner", "expiry"}:
            raise ValueError("Each check exception needs method, path, failure_type, reason, owner, expiry")
        if any(not isinstance(v, str) or not v.strip() for v in item.values()):
            raise ValueError("Check exception fields must be nonempty strings")
        if item["method"].upper() not in {m.upper() for m in HTTP_METHODS} or not item["path"].startswith("/"):
            raise ValueError("Invalid exception method or path")
        date.fromisoformat(item["expiry"])
    return value, raw


def load_schema(config: dict, config_path: Path) -> tuple[str, bytes, dict]:
    location = config["schema"]
    if urlparse(location).scheme in ("http", "https"):
        with urlopen(location, timeout=20) as response:
            raw = response.read(10_000_001)
    else:
        location = str((config_path.parent / location).resolve())
        raw = Path(location).read_bytes()
    if len(raw) > 10_000_000:
        raise ValueError("Schema exceeds 10 MB")
    schema = json.loads(raw) if location.lower().endswith(".json") else yaml.safe_load(raw)
    if not isinstance(schema, dict) or not isinstance(schema.get("paths"), dict):
        raise ValueError("Schema must be an OpenAPI object with paths")
    return location, raw, schema


def schema_operations(schema: dict) -> set[tuple[str, str]]:
    return {(method.upper(), path) for path, item in schema["paths"].items() if isinstance(item, dict) for method in item if method.lower() in HTTP_METHODS}


def build_id() -> str:
    if os.getenv("GITHUB_ACTIONS") == "true":
        return f"github-{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    if os.getenv("BITBUCKET_BUILD_NUMBER"):
        return f"bitbucket-{os.environ['BITBUCKET_BUILD_NUMBER']}"
    value = os.getenv("API_MITIGATION_BUILD_ID")
    if not value:
        raise ValueError("Direct Docker use requires API_MITIGATION_BUILD_ID")
    return value


def parse_events(path: Path) -> tuple[list[dict], list[dict]]:
    observations, scenarios = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if "ScenarioFinished" not in event:
            continue
        scenario = event["ScenarioFinished"]
        recorder = scenario.get("recorder") or {}
        label = recorder.get("label", "")
        scenarios.append({"operation": label, "phase": scenario.get("phase"), "status": scenario.get("status"), "skip_reason": scenario.get("skip_reason")})
        cases = recorder.get("cases") or {}
        checks = recorder.get("checks") or {}
        interactions = recorder.get("interactions") or {}
        for case_id, case in cases.items():
            value = case.get("value") or {}
            interaction = interactions.get(case_id) or {}
            result = interaction.get("response") or {}
            observations.append({
                "case_id": case_id, "method": value.get("method"), "path": value.get("path"),
                "phase": scenario.get("phase"), "response_status": result.get("status_code"),
                "checks": checks.get(case_id),
            })
    return observations, scenarios


def exception_for(config: dict, method: str, path: str, failure_type: str) -> dict | None:
    today = date.today()
    for item in config.get("check_exceptions", []):
        if item["method"].upper() == method and item["path"] == path and item["failure_type"] == failure_type and date.fromisoformat(item["expiry"]) >= today:
            return item
    return None


def assess(config: dict, report: dict, coverage: dict, observations: list[dict], scenarios: list[dict], expected: set[tuple[str, str]], process_code: int) -> tuple[dict, list[dict]]:
    issues = []
    failures = []
    if report.get("complete") is not True or report.get("stop_reason") != "completed":
        issues.append("Schemathesis run is incomplete")
    if report.get("errors"):
        issues.append("Schemathesis reported execution errors")
    if not observations or report.get("operations", {}).get("tested", 0) == 0:
        issues.append("Zero tested operations")
    tested = {(o["method"], o["path"]) for o in observations if o["method"] and o["path"]}
    if len(tested) != report.get("operations", {}).get("tested"):
        issues.append("Tested operation count disagrees with event evidence")
    if not tested <= expected:
        issues.append("Event operations differ from schema")
    if not any(s["status"] == "success" for s in scenarios) and not any(s["status"] == "failure" for s in scenarios):
        issues.append("No completed test scenarios")
    for o in observations:
        if not o["checks"] or o["response_status"] is None:
            issues.append(f"Missing check or response evidence for {o['case_id']}")
            continue
        for check in o["checks"]:
            status = check.get("status")
            if status not in ("success", "failure"):
                issues.append(f"Invalid check status in {o['case_id']}")
            if status == "failure":
                failure = ((check.get("failure_info") or {}).get("failure") or {})
                kind = failure.get("type")
                if not kind:
                    issues.append(f"Missing failure type in {o['case_id']}")
                matched = exception_for(config, o["method"], o["path"], kind) if kind else None
                failures.append({"case_id": o["case_id"], "method": o["method"], "path": o["path"], "check": check.get("name"), "failure_type": kind, "message": failure.get("message"), "exception": matched})
                if not matched:
                    issues.append(f"Unexplained {kind or 'unknown'} failure: {o['method']} {o['path']}")
    reported_types = {(f.get("type"), op) for f in report.get("failures", []) for op in f.get("operations", [])}
    observed_types = {(f["failure_type"], f"{f['method']} {f['path']}") for f in failures}
    if reported_types - observed_types:
        issues.append("Verdict contains failures missing from event evidence")
    if report.get("test_cases", {}).get("without_checks", 0):
        issues.append("Schemathesis reported test cases without checks")
    summary = coverage.get("summary") or {}
    percentages = {}
    for dim in DIMENSIONS:
        name = "operations" if dim == "operation" else dim
        entry = summary.get(name) or {}
        pct = entry.get("percent")
        percentages[dim] = pct
        if pct is not None and (not isinstance(pct, (int, float)) or not 0 <= pct <= 100):
            issues.append(f"Invalid {dim} coverage")
        minimum = config.get(f"min_{dim}_coverage")
        if minimum is not None:
            if pct is None:
                issues.append(f"Missing {dim} coverage for configured threshold")
            elif pct < minimum:
                issues.append(f"{dim} coverage {pct}% below {minimum}%")
    if coverage.get("tracecov_version") is None or not isinstance(coverage.get("operations"), list):
        issues.append("Invalid TraceCov coverage report")
    if process_code not in (0, 1):
        issues.append(f"Schemathesis exited with code {process_code}")
    verdict = {"passed": not issues, "issues": sorted(set(issues)), "coverage_percent": percentages,
               "operations": {"schema": len(expected), "tested": len(tested), "skipped": sorted(f"{m} {p}" for m, p in expected - tested)},
               "observed_cases": len(observations), "observed_failures": failures,
               "schemathesis": report}
    return verdict, failures


def write_database(path: Path, identity: dict, expected: set[tuple[str, str]], observations: list[dict], scenarios: list[dict], coverage: dict, verdict: dict) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE build (build_id TEXT PRIMARY KEY, component_version TEXT, configuration_id TEXT, schema_location TEXT, base_url TEXT, config_sha256 TEXT, schema_sha256 TEXT, started_at TEXT, schemathesis_version TEXT, tracecov_version TEXT, passed INTEGER, verdict_json TEXT);
            CREATE TABLE operation (method TEXT, path TEXT, tested INTEGER, coverage_json TEXT, PRIMARY KEY(method,path));
            CREATE TABLE scenario (operation TEXT, phase TEXT, status TEXT, skip_reason TEXT);
            CREATE TABLE observation (case_id TEXT, method TEXT, path TEXT, phase TEXT, response_status INTEGER, PRIMARY KEY(case_id,phase));
            CREATE TABLE check_result (case_id TEXT, phase TEXT, name TEXT, status TEXT, failure_type TEXT, failure_message TEXT, exception_json TEXT);
            CREATE TABLE coverage (dimension TEXT PRIMARY KEY, percent REAL, covered INTEGER, total INTEGER, detail_json TEXT);
        """)
        db.execute("INSERT INTO build VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", tuple(identity[k] for k in ("build_id", "component_version", "configuration_id", "schema_location", "base_url", "config_sha256", "schema_sha256", "started_at", "schemathesis_version", "tracecov_version")) + (int(verdict["passed"]), json.dumps(verdict)))
        ops = {(o["method"], o["path"]) for o in observations}
        covops = {(o["method"], o["path"]): o for o in coverage.get("operations", [])}
        for method, opath in sorted(expected):
            db.execute("INSERT INTO operation VALUES (?,?,?,?)", (method, opath, int((method, opath) in ops), json.dumps(covops.get((method, opath)))))
        for s in scenarios:
            db.execute("INSERT INTO scenario VALUES (?,?,?,?)", (s["operation"], s["phase"], s["status"], s["skip_reason"]))
        for o in observations:
            db.execute("INSERT OR REPLACE INTO observation VALUES (?,?,?,?,?)", (o["case_id"], o["method"], o["path"], o["phase"], o["response_status"]))
            for check in o["checks"] or []:
                failure = ((check.get("failure_info") or {}).get("failure") or {})
                matched = exception_for(identity["config"], o["method"], o["path"], failure.get("type", "")) if failure else None
                db.execute("INSERT INTO check_result VALUES (?,?,?,?,?,?,?)", (o["case_id"], o["phase"], check.get("name"), check.get("status"), failure.get("type"), failure.get("message"), json.dumps(matched) if matched else None))
        for dim in DIMENSIONS:
            detail = (coverage.get("summary") or {}).get("operations" if dim == "operation" else dim) or {}
            db.execute("INSERT INTO coverage VALUES (?,?,?,?,?)", (dim, detail.get("percent"), detail.get("covered"), detail.get("total"), json.dumps(detail)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    output = Path(os.environ.get("API_MITIGATION_OUTPUT_DIR", "artifacts")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_dir = None
    bid = None
    config = {}
    expected = set()
    config_raw = None
    schema_raw = None
    schema_location = None
    try:
        bid = build_id()
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", bid)[:80]
        candidate = output / f"{safe_id}-{digest(bid.encode())[:8]}"
        candidate.mkdir(parents=True, exist_ok=False)
        build_dir = candidate
        config_path = Path(args.config).resolve()
        config, config_raw = read_config(config_path)
        schema_location, schema_raw, schema = load_schema(config, config_path)
        expected = schema_operations(schema)
        report_path, events_path, junit_path = (build_dir / name for name in ("schemathesis.json", "events.ndjson", "junit.xml"))
        coverage_path, html_path = build_dir / "coverage.json", build_dir / "coverage.html"
        env = os.environ.copy()
        env.update({"SCHEMATHESIS_HOOKS": "hooks", "PYTHONUTF8": "1", "PYTHONPATH": str(Path(__file__).parent)})
        executable = shutil.which("schemathesis") or str(Path(sys.executable).with_name("schemathesis.exe" if os.name == "nt" else "schemathesis"))
        cmd = [executable, "run", schema_location, "--url", config["base_url"],
               "--report-json-path", str(report_path), "--report-ndjson-path", str(events_path),
               "--report-junit-path", str(junit_path), "--coverage-format", "html,json",
               "--coverage-report-html-path", str(html_path), "--coverage-report-json-path", str(coverage_path), "--no-color"]
        with (build_dir / "console.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(cmd, env=env, cwd=Path(__file__).parent, stdout=log, stderr=subprocess.STDOUT, check=False)
        token = os.environ.get("API_MITIGATION_BEARER_TOKEN")
        if token:
            for file in (report_path, events_path, junit_path, coverage_path, html_path, build_dir / "console.log"):
                if file.is_file():
                    content = file.read_text(encoding="utf-8")
                    file.write_text(content.replace(token, "[REDACTED]"), encoding="utf-8")
        evidence_files = (report_path, events_path, junit_path, coverage_path, html_path)
        missing_evidence = [f"Missing evidence file: {file.name}" for file in evidence_files if not file.is_file() or file.stat().st_size == 0]
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else {}
        observations, scenarios = parse_events(events_path) if events_path.is_file() else ([], [])
        verdict, _ = assess(config, report, coverage, observations, scenarios, expected, process.returncode)
        try:
            _, schema_after, _ = load_schema(config, config_path)
            if schema_after != schema_raw:
                verdict["issues"].append("Schema changed during the test run")
                verdict["passed"] = False
        except Exception as exc:
            verdict["issues"].append(f"Could not recheck schema identity: {exc}")
            verdict["passed"] = False
        if missing_evidence:
            verdict["issues"] = sorted(set(verdict["issues"] + missing_evidence))
            verdict["passed"] = False
        identity = {"build_id": bid, "component_version": config["component_version"], "configuration_id": config["configuration_id"],
                    "schema_location": schema_location, "base_url": config["base_url"], "config_sha256": digest(config_raw),
                    "schema_sha256": digest(schema_raw), "started_at": datetime.now(timezone.utc).isoformat(),
                    "schemathesis_version": importlib.metadata.version("schemathesis"), "tracecov_version": importlib.metadata.version("tracecov"), "config": config}
        verdict.update({"build": {k: v for k, v in identity.items() if k != "config"}})
        (build_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        write_database(build_dir / "evidence.sqlite", identity, expected, observations, scenarios, coverage, verdict)
        print(f"Evidence: {build_dir}")
        print("PASS" if verdict["passed"] else "FAIL: " + "; ".join(verdict["issues"]))
        return 0 if verdict["passed"] else 1
    except Exception as exc:
        error = {"error": str(exc), "at": datetime.now(timezone.utc).isoformat()}
        target = build_dir or output
        (target / "runner-error.json").write_text(json.dumps(error, indent=2), encoding="utf-8")
        if build_dir is not None:
            verdict = {"passed": False, "issues": [str(exc)], "coverage_percent": {}, "operations": {"schema": len(expected), "tested": 0, "skipped": sorted(f"{m} {p}" for m, p in expected)}, "observed_cases": 0, "observed_failures": []}
            identity = {"build_id": bid, "component_version": config.get("component_version"), "configuration_id": config.get("configuration_id"),
                        "schema_location": schema_location or config.get("schema"), "base_url": config.get("base_url"),
                        "config_sha256": digest(config_raw) if config_raw is not None else None,
                        "schema_sha256": digest(schema_raw) if schema_raw is not None else None,
                        "started_at": error["at"], "schemathesis_version": importlib.metadata.version("schemathesis"),
                        "tracecov_version": importlib.metadata.version("tracecov"), "config": config}
            (build_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
            try:
                if not (build_dir / "evidence.sqlite").exists():
                    write_database(build_dir / "evidence.sqlite", identity, expected, [], [], {}, verdict)
            except Exception:
                pass
        print(f"API mitigation test failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
