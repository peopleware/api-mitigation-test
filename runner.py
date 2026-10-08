"""One-target API test runner and evidence writer."""
from __future__ import annotations

import argparse
import base64
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
import sysconfig
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import urlopen

import yaml

DIMENSIONS = ("operation", "parameters", "keywords", "examples", "responses")
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_REPORT_BODY_BYTES = 65_536
SENSITIVE_MARKERS = ("auth", "password", "passwd", "secret", "token", "api_key", "apikey", "cookie", "session", "credential", "private_key", "access_key", "csrf")


def sensitive_key(key: str) -> bool:
    return any(marker in key.lower().replace("-", "_") for marker in SENSITIVE_MARKERS)


def sanitize_value(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if sensitive_key(str(key)) else sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    return value


def sanitize_headers(headers) -> dict:
    if not isinstance(headers, dict):
        return {}
    return {str(key): "[REDACTED]" if sensitive_key(str(key)) else value for key, value in headers.items()}


def sanitize_uri(uri: str) -> str:
    if not uri:
        return ""
    try:
        parts = urlsplit(uri)
        host = parts.netloc.split("@", 1)[-1]
        netloc = f"[REDACTED]@{host}" if "@" in parts.netloc else host
        query = urlencode([(key, "[REDACTED]" if sensitive_key(key) else value)
                           for key, value in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except ValueError:
        return "[omitted: invalid URL]"


def report_body(encoded, headers: dict) -> dict:
    if encoded is None:
        return {"status": "absent"}
    content_type = next((str(value[0] if isinstance(value, list) and value else value)
                         for key, value in headers.items() if key.lower() == "content-type"), "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        return {"status": "omitted: non-JSON body"}
    try:
        if isinstance(encoded, dict) and "$base64" in encoded:
            raw = base64.b64decode(encoded["$base64"], validate=True)
        elif isinstance(encoded, str):
            raw = encoded.encode("utf-8")
        else:
            return {"status": "omitted: unsupported body"}
        if len(raw) > 1_048_576:
            return {"status": "omitted: body exceeds 1 MiB processing limit"}
        sanitized = sanitize_value(json.loads(raw.decode("utf-8")))
        rendered = json.dumps(sanitized, indent=2, ensure_ascii=False)
        data = rendered.encode("utf-8")
        if len(data) > MAX_REPORT_BODY_BYTES:
            prefix = data[:MAX_REPORT_BODY_BYTES].decode("utf-8", errors="ignore")
            return {"status": "truncated", "text": prefix, "size": len(data)}
        return {"status": "available", "text": rendered}
    except (ValueError, UnicodeDecodeError, TypeError):
        return {"status": "omitted: invalid or truncated JSON body"}


def sanitize_exchange(interaction: dict, direction: str) -> dict | None:
    item = interaction.get(direction)
    if not isinstance(item, dict):
        return None
    headers = item.get("headers") or {}
    detail = {"headers": sanitize_headers(headers),
              "body": report_body(item.get("body" if direction == "request" else "content"), headers)}
    if direction == "request":
        detail.update({"method": item.get("method"), "uri": sanitize_uri(item.get("uri") or "")})
    else:
        detail.update({"status": item.get("status_code"), "elapsed": item.get("elapsed")})
    return detail


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_config(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError("Config must be an object")
    required = {"schema", "base_url", "component_version", "configuration_id"}
    allowed = required | {"check_exceptions", "test_operations", "stateful_max_examples", "read_only"} | {f"min_{d}_coverage" for d in DIMENSIONS}
    if missing := required - value.keys():
        raise ValueError(f"Missing config keys: {sorted(missing)}")
    if extra := value.keys() - allowed:
        raise ValueError(f"Unknown config keys: {sorted(extra)}")
    for name in required:
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    if "read_only" in value and not isinstance(value["read_only"], bool):
        raise ValueError("read_only must be a boolean")
    if urlparse(value["base_url"]).scheme not in ("http", "https"):
        raise ValueError("base_url must be an HTTP(S) URL")
    for dim in DIMENSIONS:
        key = f"min_{dim}_coverage"
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float)) or not 0 <= value[key] <= 100):
            raise ValueError(f"{key} must be a number from 0 to 100")
    stateful_max_examples = value.get("stateful_max_examples")
    if stateful_max_examples is not None and (isinstance(stateful_max_examples, bool) or not isinstance(stateful_max_examples, int) or stateful_max_examples < 1):
        raise ValueError("stateful_max_examples must be a positive integer")
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
    if "test_operations" in value:
        operations = value["test_operations"]
        if not isinstance(operations, list) or not operations:
            raise ValueError("test_operations must be a nonempty list")
        selected = set()
        for item in operations:
            if not isinstance(item, dict) or set(item) != {"method", "path"}:
                raise ValueError("Each test operation needs method and path")
            method, path = item["method"], item["path"]
            if not isinstance(method, str) or method.upper() not in {m.upper() for m in HTTP_METHODS} or not isinstance(path, str) or not path.startswith("/"):
                raise ValueError("Invalid test operation method or path")
            operation = (method.upper(), path)
            if operation in selected:
                raise ValueError(f"Duplicate test operation: {method.upper()} {path}")
            selected.add(operation)
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


def path_glob_regex(pattern: str) -> str:
    """Translate a case-sensitive path glob; all non-wildcard characters are literal."""
    parts = []
    index = 0
    while index < len(pattern):
        if pattern[index:] == "/**":
            parts.append("(?:/.*)?")
            break
        if pattern[index:index + 3] == "**/":
            parts.append("(?:.*/)?")
            index += 3
        elif pattern[index:index + 2] == "**":
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return "".join(parts)


def select_operations(selectors: list[dict], available: set[tuple[str, str]]) -> set[tuple[str, str]]:
    selected = set()
    unmatched = []
    for item in selectors:
        method, path = item["method"].upper(), item["path"]
        pattern = re.compile(path_glob_regex(path))
        matches = {(m, p) for m, p in available if m == method and pattern.fullmatch(p)}
        if not matches:
            unmatched.append(f"{method} {path}")
        selected.update(matches)
    if unmatched:
        raise ValueError(f"test_operations reference operations missing from schema (selectors matched no operations): {sorted(unmatched)}")
    return selected


def build_id() -> str:
    if os.getenv("GITHUB_ACTIONS") == "true":
        return f"github-{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    if os.getenv("BITBUCKET_BUILD_NUMBER"):
        return f"bitbucket-{os.environ['BITBUCKET_BUILD_NUMBER']}"
    value = os.getenv("API_MITIGATION_BUILD_ID")
    if not value:
        raise ValueError("Direct Docker use requires API_MITIGATION_BUILD_ID")
    return value


def schemathesis_executable() -> str:
    name = "schemathesis.exe" if os.name == "nt" else "schemathesis"
    user_scheme = "nt_user" if os.name == "nt" else "posix_user"
    candidates = (Path(sysconfig.get_path("scripts")) / name,
                  Path(sysconfig.get_path("scripts", scheme=user_scheme)) / name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    if executable := shutil.which("schemathesis"):
        return executable
    raise FileNotFoundError(f"Schemathesis CLI not found for {sys.executable}; install requirements.txt in this Python environment")


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
                "request": sanitize_exchange(interaction, "request"),
                "response": sanitize_exchange(interaction, "response"),
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


def write_database(path: Path, identity: dict, expected: set[tuple[str, str]], observations: list[dict], scenarios: list[dict], coverage: dict, verdict: dict, schema: dict | None = None) -> None:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript("""
            CREATE TABLE build (id INTEGER PRIMARY KEY, build_number TEXT NOT NULL UNIQUE, component_version TEXT, configuration_id TEXT, schema_location TEXT, base_url TEXT, config_sha256 TEXT, schema_sha256 TEXT, started_at TEXT, schemathesis_version TEXT, tracecov_version TEXT, passed INTEGER, verdict_json TEXT);
            CREATE TABLE operation (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, method TEXT NOT NULL, path TEXT NOT NULL, expected INTEGER NOT NULL, tested INTEGER NOT NULL, coverage_json TEXT, tags_json TEXT, summary TEXT, UNIQUE(build_id, method, path), UNIQUE(build_id, id));
            CREATE TABLE scenario (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, operation_id INTEGER, operation TEXT, phase TEXT, status TEXT, skip_reason TEXT, FOREIGN KEY(build_id, operation_id) REFERENCES operation(build_id, id) ON DELETE RESTRICT);
            CREATE TABLE observation (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, operation_id INTEGER, case_id TEXT, method TEXT, path TEXT, phase TEXT, response_status INTEGER, request_json TEXT, response_json TEXT, FOREIGN KEY(build_id, operation_id) REFERENCES operation(build_id, id) ON DELETE RESTRICT);
            CREATE TABLE check_result (id INTEGER PRIMARY KEY, observation_id INTEGER NOT NULL REFERENCES observation(id) ON DELETE RESTRICT, name TEXT, status TEXT, failure_type TEXT, failure_message TEXT, exception_json TEXT);
            CREATE TABLE coverage (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, dimension TEXT NOT NULL, percent REAL, covered INTEGER, total INTEGER, detail_json TEXT, UNIQUE(build_id, dimension));
            CREATE INDEX scenario_operation ON scenario(build_id, operation_id);
            CREATE INDEX observation_operation ON observation(build_id, operation_id);
            CREATE INDEX check_result_observation ON check_result(observation_id);
        """)
        build_id = db.execute(
            "INSERT INTO build (build_number, component_version, configuration_id, schema_location, base_url, config_sha256, schema_sha256, started_at, schemathesis_version, tracecov_version, passed, verdict_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(identity[k] for k in ("build_id", "component_version", "configuration_id", "schema_location", "base_url", "config_sha256", "schema_sha256", "started_at", "schemathesis_version", "tracecov_version")) + (int(verdict["passed"]), json.dumps(verdict)),
        ).lastrowid
        observed_ops = {(o["method"], o["path"]) for o in observations if o["method"] and o["path"]}
        scenario_ops = set()
        for scenario in scenarios:
            parts = (scenario["operation"] or "").split(" ", 1)
            if len(parts) == 2 and parts[0] in {m.upper() for m in HTTP_METHODS} and parts[1].startswith("/"):
                scenario_ops.add((parts[0], parts[1]))
        covops = {(o["method"], o["path"]): o for o in coverage.get("operations", [])}
        operation_ids = {}
        for method, opath in sorted(expected | observed_ops | scenario_ops):
            path_item = (schema or {}).get("paths", {}).get(opath)
            metadata = path_item.get(method.lower()) if isinstance(path_item, dict) else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            tags = metadata.get("tags") or []
            tags = [tag for tag in tags if isinstance(tag, str) and tag.strip()] if isinstance(tags, list) else []
            operation_ids[(method, opath)] = db.execute(
                "INSERT INTO operation (build_id, method, path, expected, tested, coverage_json, tags_json, summary) VALUES (?,?,?,?,?,?,?,?)",
                (build_id, method, opath, int((method, opath) in expected), int((method, opath) in observed_ops), json.dumps(covops.get((method, opath))), json.dumps(tags), metadata.get("summary") if isinstance(metadata.get("summary"), str) else None),
            ).lastrowid
        for s in scenarios:
            parts = (s["operation"] or "").split(" ", 1)
            op_key = tuple(parts) if len(parts) == 2 else None
            db.execute("INSERT INTO scenario (build_id, operation_id, operation, phase, status, skip_reason) VALUES (?,?,?,?,?,?)",
                       (build_id, operation_ids.get(op_key), s["operation"], s["phase"], s["status"], s["skip_reason"]))
        for o in observations:
            observation_id = db.execute(
                "INSERT INTO observation (build_id, operation_id, case_id, method, path, phase, response_status, request_json, response_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (build_id, operation_ids.get((o["method"], o["path"])), o["case_id"], o["method"], o["path"], o["phase"], o["response_status"],
                 json.dumps(o.get("request")) if o.get("request") is not None else None,
                 json.dumps(o.get("response")) if o.get("response") is not None else None),
            ).lastrowid
            for check in o["checks"] or []:
                failure = ((check.get("failure_info") or {}).get("failure") or {})
                matched = exception_for(identity["config"], o["method"], o["path"], failure.get("type", "")) if failure else None
                db.execute("INSERT INTO check_result (observation_id, name, status, failure_type, failure_message, exception_json) VALUES (?,?,?,?,?,?)",
                           (observation_id, check.get("name"), check.get("status"), failure.get("type"), failure.get("message"), json.dumps(matched) if matched else None))
        for dim in DIMENSIONS:
            detail = (coverage.get("summary") or {}).get("operations" if dim == "operation" else dim) or {}
            db.execute("INSERT INTO coverage (build_id, dimension, percent, covered, total, detail_json) VALUES (?,?,?,?,?,?)",
                       (build_id, dim, detail.get("percent"), detail.get("covered"), detail.get("total"), json.dumps(detail)))


def refresh_report(output: Path) -> None:
    try:
        from report import generate_report
        generate_report(output, output / "report.html")
    except Exception as exc:
        print(f"Warning: HTML report generation failed: {exc}", file=sys.stderr)


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
    schema = {}
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
        if "test_operations" in config:
            selected = select_operations(config["test_operations"], expected)
            if config.get("read_only", False) and any(method not in READ_ONLY_METHODS for method, _ in selected):
                raise ValueError("test_operations contains methods excluded by read_only")
            expected = selected
        if config.get("read_only", False):
            expected = {(method, path) for method, path in expected if method in READ_ONLY_METHODS}
        report_path, events_path, junit_path = (build_dir / name for name in ("schemathesis.json", "events.ndjson", "junit.xml"))
        coverage_path, html_path = build_dir / "coverage.json", build_dir / "coverage.html"
        env = os.environ.copy()
        env.update({"SCHEMATHESIS_HOOKS": "hooks", "PYTHONUTF8": "1", "PYTHONPATH": str(Path(__file__).parent)})
        executable = schemathesis_executable()
        cmd = [executable, "run", schema_location, "--url", config["base_url"],
               "--report-json-path", str(report_path), "--report-ndjson-path", str(events_path),
               "--report-junit-path", str(junit_path), "--coverage-format", "html,json",
               "--coverage-report-html-path", str(html_path), "--coverage-report-json-path", str(coverage_path), "--no-color"]
        if config.get("read_only", False):
            cmd.extend(["--exclude-method", "POST", "--exclude-method", "PUT", "--exclude-method", "PATCH", "--exclude-method", "DELETE", "--exclude-method", "TRACE"])
        if "test_operations" in config:
            for method, path in sorted(expected):
                cmd.extend(["--include-name", f"{method} {path}"])
        if "test_operations" in config or config.get("stateful_max_examples") is not None:
            schemathesis_config = build_dir / "schemathesis.toml"
            lines = []
            if "test_operations" in config:
                lines.extend(["[phases.coverage]", "unexpected-methods = []"])
            if config.get("stateful_max_examples") is not None:
                lines.extend(["[phases.stateful.generation]", f"max-examples = {config['stateful_max_examples']}"])
            schemathesis_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
            cmd = [executable, "--config-file", str(schemathesis_config), *cmd[1:]]
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
        verdict.update({"build": {k: v for k, v in identity.items() if k != "config"}, "read_only": config.get("read_only", False)})
        (build_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        write_database(build_dir / "evidence.sqlite", identity, expected, observations, scenarios, coverage, verdict, schema)
        refresh_report(output)
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
                    write_database(build_dir / "evidence.sqlite", identity, expected, [], [], {}, verdict, schema)
                refresh_report(output)
            except Exception:
                pass
        print(f"API mitigation test failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
