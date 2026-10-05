"""Build a portable HTML viewer from per-build SQLite evidence files."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import quote


def decode_json(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def rows(db: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
    return [dict(row) for row in db.execute(query, parameters)]


def coverage_link(database: Path, output: Path) -> str | None:
    coverage = database.with_name("coverage.html")
    if not coverage.is_file():
        return None
    try:
        relative = os.path.relpath(coverage, output.parent).replace("\\", "/")
    except ValueError:
        return None
    return quote(relative, safe="/.")


def entity_name(operation: dict) -> str:
    tags = decode_json(operation.get("tags_json"), [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                return tag.strip()
    for segment in operation["path"].split("/"):
        if not segment or segment.startswith("{") or segment.lower() == "api" or re.fullmatch(r"v\d+(?:\.\d+)*", segment, re.I):
            continue
        return re.sub(r"[-_]", " ", segment).title()
    return "Other operations"


def load_database(path: Path, output: Path) -> list[dict]:
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only = ON")
        columns = {row[1] for row in db.execute("PRAGMA table_info(observation)")}
        if not {"id", "operation_id", "phase", "case_id"} <= columns:
            raise ValueError("unsupported observation table")
        has_http = {"request_json", "response_json"} <= columns
        result = []
        for build in rows(db, "SELECT * FROM build"):
            build_id = build["id"]
            operation_rows = rows(db, "SELECT * FROM operation WHERE build_id = ? ORDER BY method, path", (build_id,))
            scenario_rows = rows(db, "SELECT * FROM scenario WHERE build_id = ? ORDER BY id", (build_id,))
            observation_rows = rows(db, "SELECT * FROM observation WHERE build_id = ? ORDER BY id", (build_id,))
            check_rows = rows(db, "SELECT c.* FROM check_result c JOIN observation o ON o.id = c.observation_id WHERE o.build_id = ? ORDER BY c.id", (build_id,))
            checks_by_observation: dict[int, list[dict]] = {}
            for check in check_rows:
                checks_by_observation.setdefault(check["observation_id"], []).append({
                    "name": check["name"], "status": check["status"],
                    "failure_type": check["failure_type"], "failure_message": check["failure_message"],
                    "exception": decode_json(check["exception_json"]),
                })
            operations = []
            by_id = {}
            for operation in operation_rows:
                item = {"method": operation["method"], "path": operation["path"],
                        "entity": entity_name(operation), "summary": operation.get("summary"),
                        "expected": bool(operation["expected"]), "tested": bool(operation["tested"]),
                        "scenarios": [], "cases": []}
                operations.append(item)
                by_id[operation["id"]] = item
            unlinked = {"method": "", "path": "Unlinked evidence", "entity": "Unlinked evidence", "summary": None, "expected": False,
                        "tested": False, "scenarios": [], "cases": []}
            for scenario in scenario_rows:
                target = by_id.get(scenario["operation_id"], unlinked)
                target["scenarios"].append({"phase": scenario["phase"], "status": scenario["status"],
                                            "skip_reason": scenario["skip_reason"], "operation": scenario["operation"]})
            for observation in observation_rows:
                target = by_id.get(observation["operation_id"], unlinked)
                target["cases"].append({"case_id": observation["case_id"], "phase": observation["phase"],
                                        "response_status": observation["response_status"],
                                        "checks": checks_by_observation.get(observation["id"], []),
                                        "request": decode_json(observation.get("request_json")) if has_http else None,
                                        "response": decode_json(observation.get("response_json")) if has_http else None})
            if unlinked["cases"] or unlinked["scenarios"]:
                operations.append(unlinked)
            verdict = decode_json(build.get("verdict_json"), {}) or {}
            check_count = len(check_rows)
            failure_count = sum(check["status"] == "failure" for check in check_rows)
            accepted_count = sum(check["status"] == "failure" and check["exception_json"] is not None for check in check_rows)
            result.append({"build_number": build["build_number"], "started_at": build["started_at"],
                           "component_version": build["component_version"],
                           "configuration_id": build["configuration_id"], "base_url": build["base_url"],
                           "schema_location": build.get("schema_location"), "config_sha256": build.get("config_sha256"),
                           "schemathesis_version": build.get("schemathesis_version"), "tracecov_version": build.get("tracecov_version"),
                           "schema_sha256": build["schema_sha256"], "passed": bool(build["passed"]),
                           "issues": verdict.get("issues", []), "operations": operations,
                           "case_count": len(observation_rows), "check_count": check_count,
                           "failure_count": failure_count, "accepted_count": accepted_count,
                           "success_count": sum(check["status"] == "success" for check in check_rows),
                           "coverage_href": coverage_link(path, output), "http_available": has_http})
        return result


def timestamp(value) -> float:
    if not value:
        return 0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


ASSETS = Path(__file__).with_name("report_assets")


def generate_report(input_dir: Path, output: Path) -> tuple[int, list[str]]:
    input_dir = Path(input_dir)
    output = Path(output)
    databases = sorted(input_dir.rglob("evidence.sqlite"))
    if not databases:
        raise ValueError(f"No evidence.sqlite files found under {input_dir}")
    runs: list[dict] = []
    warnings: list[str] = []
    for database in databases:
        try:
            runs.extend(load_database(database, output))
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            warnings.append(f"Skipped {database}: {exc}")
    if not runs:
        raise ValueError("No readable evidence databases found")
    runs.sort(key=lambda run: timestamp(run["started_at"]), reverse=True)
    payload = json.dumps({"runs": runs, "warnings": warnings}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    html = (ASSETS / "report.html").read_text(encoding="utf-8")
    html = (html.replace("__CSS__", (ASSETS / "style.css").read_text(encoding="utf-8"))
            .replace("__JS__", (ASSETS / "app.js").read_text(encoding="utf-8"))
            .replace("__GENERATED__", generated).replace("__RUN_COUNT__", str(len(runs)))
            .replace("__DATA__", payload))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, prefix=".report-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(html)
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return len(runs), warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline HTML report from archived evidence.sqlite files")
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory recursively containing evidence.sqlite files")
    parser.add_argument("--output", type=Path, required=True, help="HTML file to create")
    args = parser.parse_args()
    try:
        count, warnings = generate_report(args.input_dir, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Report generation failed: {exc}\n")
    for warning in warnings:
        print(f"Warning: {warning}")
    print(f"Report: {args.output} ({count} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
