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
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import urlopen

import yaml
from evidence import not_executed

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


def redact_evidence_tokens(files) -> None:
    tokens = {value for name, value in os.environ.items()
              if name.startswith("API_MITIGATION_") and name.endswith("_TOKEN") and value}
    if not tokens:
        return
    for file in files:
        if file.is_file():
            content = file.read_text(encoding="utf-8")
            for token in sorted(tokens, key=len, reverse=True):
                content = content.replace(token, "[REDACTED]")
            file.write_text(content, encoding="utf-8")


def read_config(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError("Config must be an object")
    required = {"schema", "base_url", "component_version", "configuration_id"}
    allowed = required | {"check_exceptions", "test_operations", "stateful_max_examples", "read_only", "check_statuses", "require_success_responses"} | {f"min_{d}_coverage" for d in DIMENSIONS}
    if missing := required - value.keys():
        raise ValueError(f"Missing config keys: {sorted(missing)}")
    if extra := value.keys() - allowed:
        raise ValueError(f"Unknown config keys: {sorted(extra)}")
    for name in required:
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    statuses = value.get("check_statuses", [])
    if not isinstance(statuses, list):
        raise ValueError("check_statuses must be a list")
    seen = set()
    for item in statuses:
        if not isinstance(item, dict) or set(item) != {"method", "path", "check", "expected_statuses", "reason"}:
            raise ValueError("Each check status override needs method, path, check, expected_statuses and reason")
        if (not isinstance(item["method"], str) or item["method"].upper() not in {m.upper() for m in HTTP_METHODS}
                or not isinstance(item["path"], str) or not item["path"].startswith("/")
                or not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError("Invalid check status operation or reason")
        if item["check"] not in ("positive_data_acceptance", "negative_data_rejection", "missing_required_header"):
            raise ValueError("Unsupported check status override")
        codes = item["expected_statuses"]
        if not isinstance(codes, list) or not codes or any(
                not isinstance(code, str) or not re.fullmatch(r"[1-5](?:[0-9]{2}|xx)", code) for code in codes):
            raise ValueError("expected_statuses must contain HTTP status strings or class wildcards")
        key = (item["method"].upper(), item["path"], item["check"])
        if key in seen:
            raise ValueError("Duplicate check status override")
        seen.add(key)
    if "read_only" in value and not isinstance(value["read_only"], bool):
        raise ValueError("read_only must be a boolean")
    if "require_success_responses" in value and not isinstance(value["require_success_responses"], bool):
        raise ValueError("require_success_responses must be a boolean")
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
        required_fields = {"failure_type", "reason", "owner", "expiry"}
        if not isinstance(item, dict) or set(item) not in (required_fields, required_fields | {"method", "path"}):
            raise ValueError("Each check exception needs failure_type, reason, owner, expiry; method and path must be provided together or both omitted")
        if any(not isinstance(v, str) or not v.strip() for v in item.values()):
            raise ValueError("Check exception fields must be nonempty strings")
        if "method" in item and (item["method"].upper() not in {m.upper() for m in HTTP_METHODS} or not item["path"].startswith("/")):
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
            operation_method, operation_path = value.get("method"), value.get("path")
            phase_meta = (value.get("meta") or {}).get("phase") or {}
            if (scenario.get("phase") == "coverage"
                    and (phase_meta.get("data") or {}).get("scenario") == "unspecified_http_method"):
                source = label.split(" ", 1)
                if len(source) == 2 and source[0].lower() in HTTP_METHODS and source[1] == operation_path:
                    operation_method = source[0]
            interaction = interactions.get(case_id) or {}
            result = interaction.get("response") or {}
            parent_id = case.get("parent_id")
            parent = cases.get(parent_id) or {}
            parent_value = parent.get("value") or {}
            parent_status = ((interactions.get(parent_id) or {}).get("response") or {}).get("status_code")
            auth_checks = [check for check in (checks.get(parent_id) or []) if check.get("name") == "ignored_auth"]
            verification = None
            if (not checks.get(case_id) and parent_id != case_id and parent and
                    case.get("transition") is None and
                    value.get("method") and value.get("path") and
                    (value.get("method"), value.get("path")) == (parent_value.get("method"), parent_value.get("path")) and
                    isinstance(parent_status, int) and 200 <= parent_status < 300 and
                    result.get("status_code") in (401, 403) and auth_checks and
                    all(check.get("status") == "success" for check in auth_checks)):
                verification = {"parent_case_id": parent_id, "check": "ignored_auth", "status": "success"}
            observations.append({
                "case_id": case_id, "method": operation_method, "path": operation_path,
                "phase": scenario.get("phase"), "response_status": result.get("status_code"),
                "request": sanitize_exchange(interaction, "request"),
                "response": sanitize_exchange(interaction, "response"),
                "checks": checks.get(case_id),
                "parent_case_id": parent_id, "verification": verification,
                "execution_status": "executed" if interaction or checks.get(case_id) else "not_executed",
            })
    return observations, scenarios


def exception_for(config: dict, method: str, path: str, failure_type: str) -> dict | None:
    today = date.today()
    for item in config.get("check_exceptions", []):
        operation_matches = "method" not in item or (item["method"].upper() == method and item["path"] == path)
        if operation_matches and item["failure_type"] == failure_type and date.fromisoformat(item["expiry"]) >= today:
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
    tested = {(o["method"], o["path"]) for o in observations if o["method"] and o["path"] and not not_executed(o)}
    successful_operations = set()
    blocked_operations = set()
    if len(tested) != report.get("operations", {}).get("tested"):
        issues.append("Tested operation count disagrees with event evidence")
    if not tested <= expected:
        issues.append("Event operations differ from schema")
    if config.get("require_success_responses", False):
        observed_successes = {(o["method"], o["path"]) for o in observations
                              if not not_executed(o) and isinstance(o.get("response_status"), int)
                              and 200 <= o["response_status"] < 300}
        for method, path in sorted(expected - observed_successes):
            issues.append(f"No successful response observed: {method} {path}")
    if not any(s["status"] == "success" for s in scenarios) and not any(s["status"] == "failure" for s in scenarios):
        issues.append("No completed test scenarios")
    for o in observations:
        if not_executed(o):
            continue
        operation = (o["method"], o["path"])
        if (not o["checks"] and not o.get("verification")) or o["response_status"] is None:
            blocked_operations.add(operation)
            issues.append(f"Missing check or response evidence for {o['case_id']}")
            continue
        if o.get("verification"):
            successful_operations.add(operation)
        for check in o["checks"] or []:
            status = check.get("status")
            if status not in ("success", "failure"):
                blocked_operations.add(operation)
                issues.append(f"Invalid check status in {o['case_id']}")
            if status == "success":
                successful_operations.add(operation)
            if status == "failure":
                failure = ((check.get("failure_info") or {}).get("failure") or {})
                kind = failure.get("type")
                if not kind:
                    issues.append(f"Missing failure type in {o['case_id']}")
                matched = exception_for(config, o["method"], o["path"], kind) if kind else None
                failures.append({"case_id": o["case_id"], "method": o["method"], "path": o["path"], "check": check.get("name"), "failure_type": kind, "message": failure.get("message"), "exception": matched})
                if not matched:
                    blocked_operations.add(operation)
                    issues.append(f"Unexplained {kind or 'unknown'} failure: {o['method']} {o['path']}")
    reported_types = {(f.get("type"), op) for f in report.get("failures", []) for op in f.get("operations", [])}
    observed_types = {(f["failure_type"], f"{f['method']} {f['path']}") for f in failures}
    observed_types.update(
        (check["failure_info"]["failure"].get("type"), "Stateful tests")
        for observation in observations if observation.get("phase") == "stateful"
        for check in observation.get("checks") or []
        if check.get("status") == "failure" and (check.get("failure_info") or {}).get("failure")
    )
    if reported_types - observed_types:
        issues.append("Verdict contains failures missing from event evidence")
        blocked_operations.update(tuple(op.split(" ", 1)) for _, op in reported_types - observed_types)
    blocked_operations.update(tuple(s["operation"].split(" ", 1)) for s in scenarios if s["status"] in ("error", "interrupted"))
    verified_by_parent = sum(not o["checks"] and o.get("verification") is not None for o in observations)
    unexecuted = sum(not_executed(o) for o in observations)
    if report.get("test_cases", {}).get("without_checks", 0) > verified_by_parent + unexecuted:
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
    checks = [check for o in observations for check in (o["checks"] or [])]
    failed_checks = sum(check.get("status") == "failure" for check in checks)
    failed_cases = sum(any(check.get("status") == "failure" for check in (o["checks"] or []))
                       for o in observations)
    accepted_checks = sum(
        check.get("status") == "failure" and exception_for(
            config, o["method"], o["path"],
            ((check.get("failure_info") or {}).get("failure") or {}).get("type")) is not None
        for o in observations for check in (o["checks"] or [])
    )
    verdict = {"passed": not issues, "issues": sorted(set(issues)), "coverage_percent": percentages,
               "cases": {"total": len(observations), "generated": len(observations),
                         "executed": len(observations) - unexecuted, "unexecuted": unexecuted,
                         "failed": failed_cases, "verified_by_parent": verified_by_parent},
               "checks": {"total": len(checks), "failed": failed_checks,
                          "accepted": accepted_checks, "unexplained": failed_checks - accepted_checks},
               "operations": {"schema": len(expected), "tested": len(tested),
                              "passed": len((successful_operations & expected) - blocked_operations),
                              "skipped": sorted(f"{m} {p}" for m, p in expected - tested)},
               "observed_cases": len(observations), "observed_failures": failures,
               "schemathesis": report}
    return verdict, failures


def console_summary(verdict: dict, build_dir: Path, coverage: dict | None = None,
                    config: dict | None = None, *, color: bool | None = None) -> str:
    if color is None:
        color = "NO_COLOR" not in os.environ
    def paint(value, shade):
        return f"\033[{shade}m{value}\033[0m" if color else str(value)

    operations = verdict["operations"]
    percentages = verdict["coverage_percent"]
    cases = verdict.get("cases") or {"total": verdict.get("observed_cases", 0)}
    checks = verdict.get("checks", {"total": 0, "failed": 0, "unexplained": 0, "accepted": 0})
    summary = (coverage or {}).get("summary") or {}
    config = config or {}
    operation_counts = []
    failed_operations = operations['schema'] - operations['passed']
    if failed_operations:
        operation_counts.append(paint(f"{failed_operations} failed", 31))
    operation_counts.extend([paint(f"{operations['passed']} passed", 32),
                             paint(f"{operations['schema']} discovered", 37)])
    check_counts = []
    if checks['failed']:
        unexplained = f" ({checks['unexplained']} unexplained)" if checks['unexplained'] else ""
        check_counts.append(paint(f"{checks['failed']} failed{unexplained}", 31))
    if checks['accepted']:
        check_counts.append(paint(f"{checks['accepted']} accepted failures", 33))
    check_counts.append(paint(f"{checks['total']} total", 37))
    case_counts = []
    if cases.get("failed"):
        case_counts.append(paint(f"{cases['failed']} failed", 31))
    if cases.get("verified_by_parent"):
        case_counts.append(paint(f"{cases['verified_by_parent']} verified by parent check", 32))
    if "generated" in cases:
        case_counts.extend([paint(f"{cases['generated']} generated", 37),
                            paint(f"{cases['executed']} executed", 37),
                            paint(f"{cases['unexecuted']} not executed", 37)])
    else:
        case_counts.append(paint(f"{cases['total']} total", 37))
    lines = [
        "Verdict:\t" + paint("PASS" if verdict["passed"] else "FAIL", 32 if verdict["passed"] else 31),
        f"Evidence:\t{build_dir}", "",
        "Operations:\t" + ", ".join(operation_counts),
        "Cases:\t\t" + ", ".join(case_counts),
        "Checks:\t\t" + ", ".join(check_counts),
        "Duration:\t" + paint(f"{verdict['duration_seconds']:.1f}s" if verdict.get("duration_seconds") is not None else "N/A", 37),
        "", "==================== Coverage Summary ====================",
    ]
    for dim in DIMENSIONS:
        name = "operations" if dim == "operation" else dim
        entry = summary.get(name) or {}
        pct = percentages.get(dim)
        minimum = config.get(f"min_{dim}_coverage")
        numeric = isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100
        shade = (32 if pct >= minimum else 31) if numeric and minimum is not None else (31 if minimum is not None else 33)
        percent = f"{pct:.2f}".rstrip("0").rstrip(".") + "%" if numeric else "N/A"
        counts = f"({entry['covered']}/{entry['total']})" if entry.get("covered") is not None and entry.get("total") is not None else "(N/A)"
        lines.append(f"{name.title()}:\t\t{paint(percent.ljust(6), shade)}\t{counts}")
    lines.append("==========================================================")
    return "\n".join(lines)


def write_database(path: Path, identity: dict, expected: set[tuple[str, str]], observations: list[dict], scenarios: list[dict], coverage: dict, verdict: dict, schema: dict | None = None) -> None:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript("""
            CREATE TABLE build (id INTEGER PRIMARY KEY, build_number TEXT NOT NULL UNIQUE, component_version TEXT, configuration_id TEXT, schema_location TEXT, base_url TEXT, config_sha256 TEXT, schema_sha256 TEXT, started_at TEXT, schemathesis_version TEXT, tracecov_version TEXT, passed INTEGER, verdict_json TEXT);
            CREATE TABLE operation (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, method TEXT NOT NULL, path TEXT NOT NULL, expected INTEGER NOT NULL, tested INTEGER NOT NULL, coverage_json TEXT, tags_json TEXT, summary TEXT, UNIQUE(build_id, method, path), UNIQUE(build_id, id));
            CREATE TABLE scenario (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, operation_id INTEGER, operation TEXT, phase TEXT, status TEXT, skip_reason TEXT, FOREIGN KEY(build_id, operation_id) REFERENCES operation(build_id, id) ON DELETE RESTRICT);
            CREATE TABLE observation (id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES build(id) ON DELETE RESTRICT, operation_id INTEGER, case_id TEXT, method TEXT, path TEXT, phase TEXT, response_status INTEGER, request_json TEXT, response_json TEXT, parent_case_id TEXT, verification_json TEXT, execution_status TEXT, FOREIGN KEY(build_id, operation_id) REFERENCES operation(build_id, id) ON DELETE RESTRICT);
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
        tested_ops = {(o["method"], o["path"]) for o in observations if o["method"] and o["path"] and not not_executed(o)}
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
                (build_id, method, opath, int((method, opath) in expected), int((method, opath) in tested_ops), json.dumps(covops.get((method, opath))), json.dumps(tags), metadata.get("summary") if isinstance(metadata.get("summary"), str) else None),
            ).lastrowid
        for s in scenarios:
            parts = (s["operation"] or "").split(" ", 1)
            op_key = tuple(parts) if len(parts) == 2 else None
            db.execute("INSERT INTO scenario (build_id, operation_id, operation, phase, status, skip_reason) VALUES (?,?,?,?,?,?)",
                       (build_id, operation_ids.get(op_key), s["operation"], s["phase"], s["status"], s["skip_reason"]))
        for o in observations:
            observation_id = db.execute(
                "INSERT INTO observation (build_id, operation_id, case_id, method, path, phase, response_status, request_json, response_json, parent_case_id, verification_json, execution_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (build_id, operation_ids.get((o["method"], o["path"])), o["case_id"], o["method"], o["path"], o["phase"], o["response_status"],
                 json.dumps(o.get("request")) if o.get("request") is not None else None,
                 json.dumps(o.get("response")) if o.get("response") is not None else None,
                 o.get("parent_case_id"), json.dumps(o["verification"]) if o.get("verification") else None,
                 "not_executed" if not_executed(o) else "executed"),
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
    started = time.perf_counter()
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
        for item in config.get("check_statuses", []):
            operation = (item["method"].upper(), item["path"])
            if operation not in expected:
                raise ValueError(f"Check status override references an unselected operation: {operation}")
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
        if "test_operations" in config or config.get("stateful_max_examples") is not None or config.get("check_statuses"):
            schemathesis_config = build_dir / "schemathesis.toml"
            lines = []
            if "test_operations" in config:
                lines.extend(["[phases.coverage]", "unexpected-methods = []"])
            if config.get("stateful_max_examples") is not None:
                lines.extend(["[phases.stateful.generation]", f"max-examples = {config['stateful_max_examples']}"])
            overrides = {}
            for item in config.get("check_statuses", []):
                overrides.setdefault((item["method"].upper(), item["path"]), []).append(item)
            for (method, path), items in overrides.items():
                lines.extend(["[[operations]]", f"include-name = {json.dumps(f'{method} {path}')}"])
                for item in items:
                    lines.extend([f"[operations.checks.{item['check']}]",
                                  f"expected-statuses = {json.dumps(item['expected_statuses'])}"])
            schemathesis_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
            cmd = [executable, "--config-file", str(schemathesis_config), *cmd[1:]]
        with (build_dir / "console.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(cmd, env=env, cwd=Path(__file__).parent, stdout=log, stderr=subprocess.STDOUT, check=False)
        redact_evidence_tokens((report_path, events_path, junit_path, coverage_path, html_path, build_dir / "console.log"))
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
        verdict["duration_seconds"] = time.perf_counter() - started
        (build_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        write_database(build_dir / "evidence.sqlite", identity, expected, observations, scenarios, coverage, verdict, schema)
        refresh_report(output)
        print(console_summary(verdict, build_dir, coverage, config))
        return 0 if verdict["passed"] else 1
    except Exception as exc:
        error = {"error": str(exc), "at": datetime.now(timezone.utc).isoformat()}
        target = build_dir or output
        (target / "runner-error.json").write_text(json.dumps(error, indent=2), encoding="utf-8")
        if build_dir is not None:
            verdict = {"passed": False, "issues": [str(exc)], "coverage_percent": {}, "operations": {"schema": len(expected), "tested": 0, "passed": 0, "skipped": sorted(f"{m} {p}" for m, p in expected)}, "observed_cases": 0, "observed_failures": []}
            verdict["duration_seconds"] = time.perf_counter() - started
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
            print(console_summary(verdict, build_dir, config=config))
        print(f"API mitigation test failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
