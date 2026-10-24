"""Build a portable HTML viewer from per-build SQLite evidence files."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import gzip
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import uuid
from urllib.parse import quote
from evidence import not_executed


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


def operation_tag(operation: dict) -> str | None:
    tags = decode_json(operation.get("tags_json"), [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                return tag.strip()
    return None


def entity_name(operation: dict) -> str:
    tag = operation_tag(operation)
    if tag:
        return tag
    for segment in operation["path"].split("/"):
        if not segment or segment.startswith("{") or segment.lower() == "api" or re.fullmatch(r"v\d+(?:\.\d+)*", segment, re.I):
            continue
        return re.sub(r"[-_]", " ", segment).title()
    return "Other operations"


def load_database(path: Path, output: Path, *, bundle=None, source_id="archive") -> list[dict]:
    """Read evidence in case order; a bundle sink keeps full evidence off the heap.

    Without a sink this retains the original in-memory interface for consumers
    inspecting a small database. Report generation always supplies a sink.
    """
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
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
            tags_by_path: dict[str, set[str]] = {}
            for operation in operation_rows:
                tag = operation_tag(operation)
                if tag:
                    tags_by_path.setdefault(operation["path"], set()).add(tag)
            scenario_rows = rows(db, "SELECT * FROM scenario WHERE build_id = ? ORDER BY id", (build_id,))
            operations = []
            by_id = {}
            for operation in operation_rows:
                coverage = decode_json(operation.get("coverage_json"), {}) or {}
                entity = entity_name(operation)
                path_tags = tags_by_path.get(operation["path"], set())
                if not operation["expected"] and not operation_tag(operation) and len(path_tags) == 1:
                    entity = next(iter(path_tags))
                item = {"method": operation["method"], "path": operation["path"],
                        "entity": entity, "summary": operation.get("summary"),
                        "expected": bool(operation["expected"]), "tested": bool(operation["tested"]),
                        "scenarios": [], "cases": [],
                        "response_coverage": [{"status": str(response["status"]), "hits": response.get("hits", 0)}
                                              for response in coverage.get("responses", [])]}
                operations.append(item)
                by_id[operation["id"]] = item
            unlinked = {"method": "", "path": "Unlinked evidence", "entity": "Unlinked evidence", "summary": None, "expected": False,
                        "tested": False, "scenarios": [], "cases": []}
            for scenario in scenario_rows:
                target = by_id.get(scenario["operation_id"], unlinked)
                target["scenarios"].append({"phase": scenario["phase"], "status": scenario["status"],
                                            "skip_reason": scenario["skip_reason"], "operation": scenario["operation"]})
            run_id = f"{source_id}-b{build_id}"
            totals = dict.fromkeys(("case_count", "check_count", "failure_count", "accepted_count", "success_count",
                                    "unexecuted_count", "unknown_execution_count", "verified_by_parent_count"), 0)
            # Restrict both sides of the indexed join by build and operation.
            # Do not repeat request/response text for every joined check row.
            for operation_id, target in [*by_id.items(), (None, unlinked)]:
                condition = ("o.operation_id IS ?" if operation_id is not None else
                             "(o.operation_id IS NULL OR o.operation_id NOT IN (SELECT id FROM operation WHERE build_id = ?))")
                parameters = (build_id, operation_id if operation_id is not None else build_id)
                observations = db.execute(f"SELECT o.* FROM observation o WHERE o.build_id = ? AND {condition} ORDER BY o.id", parameters)
                check_cursor = iter(db.execute(
                    f"SELECT c.* FROM observation o JOIN check_result c ON c.observation_id = o.id "
                    f"WHERE o.build_id = ? AND {condition} ORDER BY o.id, c.id", parameters))
                next_check = next(check_cursor, None)
                projection = OperationProjection(target, bundle) if bundle else None
                for observation_row in observations:
                    observation = dict(observation_row)
                    checks = []
                    while next_check is not None and next_check["observation_id"] == observation["id"]:
                        check = dict(next_check)
                        checks.append({"name": check["name"], "status": check["status"],
                                       "failure_type": check["failure_type"], "failure_message": check["failure_message"],
                                       "exception": decode_json(check["exception_json"])})
                        totals["check_count"] += 1
                        totals["failure_count"] += check["status"] == "failure"
                        totals["accepted_count"] += check["status"] == "failure" and check["exception_json"] is not None
                        totals["success_count"] += check["status"] == "success"
                        next_check = next(check_cursor, None)
                    case = {"case_id": observation["case_id"], "phase": observation["phase"],
                            "response_status": observation["response_status"], "checks": checks,
                            "parent_case_id": observation.get("parent_case_id"),
                            "verification": decode_json(observation.get("verification_json")),
                            "execution_status": observation.get("execution_status"),
                            "request": decode_json(observation.get("request_json")) if has_http else None,
                            "response": decode_json(observation.get("response_json")) if has_http else None}
                    if case["execution_status"] is None:
                        case["execution_status"] = ("not_executed" if has_http else "unknown") if not_executed(case) else "executed"
                    totals["case_count"] += 1
                    totals["unexecuted_count"] += not_executed(case)
                    totals["unknown_execution_count"] += case["execution_status"] == "unknown"
                    totals["verified_by_parent_count"] += case["verification"] is not None
                    if projection:
                        projection.add(case, f"{run_id}-c{observation['id']}", run_id)
                    else:
                        target["cases"].append(case)
                if projection:
                    projection.finish()
            if unlinked.get("case_count", len(unlinked.get("cases", []))) or unlinked["scenarios"]:
                operations.append(unlinked)
            verdict = decode_json(build.get("verdict_json"), {}) or {}
            run = {"id": run_id, "build_number": build["build_number"], "started_at": build["started_at"],
                           "component_version": build["component_version"],
                           "configuration_id": build["configuration_id"], "base_url": build["base_url"],
                           "schema_location": build.get("schema_location"), "config_sha256": build.get("config_sha256"),
                           "schemathesis_version": build.get("schemathesis_version"), "tracecov_version": build.get("tracecov_version"),
                           "schema_sha256": build["schema_sha256"], "passed": bool(build["passed"]),
                           "issues": verdict.get("issues", []), "operations": operations,
                           **totals, "generated_count": totals["case_count"],
                           "executed_count": totals["case_count"] - totals["unexecuted_count"] - totals["unknown_execution_count"],
                           "coverage_href": coverage_link(path, output), "http_available": has_http}
            if bundle:
                summarize_run(run)
            result.append(run)
        return result


CHUNK_BYTES = 1024 * 1024
INDEX_CASES = 1000
FACTS = ("case_count", "check_count", "active_checks", "active_failures", "unaccepted", "unknown_checks",
         "incomplete", "verified", "unsupported_cases")


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def js_truthy(value) -> bool:
    # Empty JSON containers are truthy in the existing browser classifier.
    return isinstance(value, (dict, list)) or bool(value)


def evidence_kind(facts: dict, tested: bool, scenarios: list) -> str:
    if facts["unaccepted"]:
        return "fail"
    if (facts["unknown_checks"] or facts["incomplete"] or
            any(s["status"] in ("error", "failure") for s in scenarios) and not facts["active_failures"]):
        return "neutral"
    if facts["active_failures"]:
        return "accepted"
    return "pass" if tested and (facts["active_checks"] or facts["verified"]) else "untested"


def merge_types(target: dict, source: dict) -> None:
    for name, counts in source.items():
        destination = target.setdefault(name, {"total": 0, "accepted": 0})
        for key in ("total", "accepted"):
            destination[key] += counts[key]


class BundleWriter:
    """Write immutable gzip payloads wrapped in file://-compatible scripts."""

    def __init__(self, directory: Path, href: str, prefix: str):
        self.directory, self.href, self.prefix = directory, href, prefix
        self.sequence = 0
        self.records = []
        self.record_bytes = 2
        self.detail_ref = None
        self.files: list[Path] = []

    def reserve(self) -> str:
        self.sequence += 1
        return f"{self.href}/{self.prefix}-{self.sequence}.js"

    def write(self, value, ref=None) -> str:
        encoded = compact_json(value).encode("utf-8")
        return self.write_bytes(encoded, ref)

    def write_bytes(self, encoded: bytes, ref=None) -> str:
        ref = ref or self.reserve()
        path = self.directory / ref.rsplit("/", 1)[1]
        payload = base64.b64encode(gzip.compress(encoded, mtime=0)).decode("ascii")
        path.write_text(f"window.reportChunk({compact_json(ref)},{len(encoded)},{compact_json(payload)});\n", encoding="utf-8")
        self.files.append(path)
        return ref

    def add_detail(self, case: dict) -> str:
        encoded = compact_json(case).encode("utf-8")
        if self.records and self.record_bytes + len(encoded) + 1 > CHUNK_BYTES:
            self.flush_details()
        if self.detail_ref is None:
            self.detail_ref = self.reserve()
        self.records.append(encoded)
        self.record_bytes += len(encoded) + 1
        return self.detail_ref

    def flush_details(self) -> None:
        if self.records:
            self.write_bytes(b"[" + b",".join(self.records) + b"]", self.detail_ref)
        self.records, self.record_bytes, self.detail_ref = [], 2, None

    def write_operations(self, operations: list[dict]) -> list[str]:
        refs, buffer, size = [], [], 2
        for operation in operations:
            encoded = compact_json(operation).encode("utf-8")
            if buffer and size + len(encoded) + 1 > CHUNK_BYTES:
                refs.append(self.write_bytes(b"[" + b",".join(buffer) + b"]"))
                buffer, size = [], 2
            buffer.append(encoded)
            size += len(encoded) + 1
        if buffer:
            refs.append(self.write_bytes(b"[" + b",".join(buffer) + b"]"))
        return refs


class OperationProjection:
    def __init__(self, operation: dict, bundle: BundleWriter):
        self.operation, self.bundle = operation, bundle
        operation.pop("cases")
        operation.update({key: 0 for key in FACTS})
        operation.update(failure_types={}, method_probe=False)
        self.phases = {}
        for scenario in operation["scenarios"]:
            self.phase(scenario["phase"])["scenarios"].append(scenario)

    def phase(self, name):
        name = name or "Unknown"
        if name not in self.phases:
            self.phases[name] = {"name": name, **dict.fromkeys(FACTS, 0), "scenarios": [],
                                 "failure_types": {}, "indexes": [], "buffer": [], "buffer_bytes": 2}
        return self.phases[name]

    def add(self, case: dict, uid: str, run_id: str):
        facts = dict.fromkeys(FACTS, 0)
        facts.update(case_count=1, check_count=len(case["checks"]),
                     unsupported_cases=int(any(c["name"] == "unsupported_method" for c in case["checks"])))
        types = {}
        for check in case["checks"]:
            if check["status"] == "failure":
                counts = types.setdefault(check["failure_type"] or "Unknown failure type", {"total": 0, "accepted": 0})
                counts["total"] += 1
                counts["accepted"] += js_truthy(check["exception"])
        if not not_executed(case):
            facts.update(active_checks=len(case["checks"]),
                         active_failures=sum(c["status"] == "failure" for c in case["checks"]),
                         unaccepted=sum(c["status"] == "failure" and not js_truthy(c["exception"]) for c in case["checks"]),
                         unknown_checks=sum(c["status"] not in ("success", "failure") for c in case["checks"]),
                         incomplete=int((not case["checks"] and not js_truthy(case["verification"])) or case["response_status"] is None),
                         verified=int(js_truthy(case["verification"])))
        phase = self.phase(case["phase"])
        for target in (phase, self.operation):
            for key in FACTS:
                target[key] += facts[key]
            merge_types(target["failure_types"], types)
        request = case["request"]
        if case["response_status"] == 405 and request and request.get("method") and request["method"] != self.operation["method"]:
            self.operation["method_probe"] = True
        case["uid"] = uid
        detail_ref = self.bundle.add_detail(case)
        summary = {"uid": uid, "source_run": run_id, "case_id": case["case_id"], "response_status": case["response_status"],
                   "execution_status": case["execution_status"], "check_count": len(case["checks"]),
                   "kind": evidence_kind(facts, True, []), "failure_types": list(types), "detail_ref": detail_ref}
        size = len(compact_json(summary).encode("utf-8")) + 1
        if phase["buffer"] and (len(phase["buffer"]) >= INDEX_CASES or phase["buffer_bytes"] + size > CHUNK_BYTES):
            self.flush_index(phase)
        phase["buffer"].append(summary)
        phase["buffer_bytes"] += size

    def flush_index(self, phase):
        cases = phase["buffer"]
        if not cases:
            return
        kinds, types = {}, {}
        for case in cases:
            kind = case["kind"]
            kinds[kind] = kinds.get(kind, 0) + 1
            for name in case["failure_types"]:
                counts = types.setdefault(name, {})
                counts["all"] = counts.get("all", 0) + 1
                counts[kind] = counts.get(kind, 0) + 1
        phase["indexes"].append({"ref": self.bundle.write(cases), "count": len(cases), "kinds": kinds, "types": types})
        phase["buffer"], phase["buffer_bytes"] = [], 2

    def finish(self):
        for phase in self.phases.values():
            self.flush_index(phase)
            phase.pop("buffer")
            phase.pop("buffer_bytes")
            phase["kind"] = evidence_kind(phase, phase["case_count"] > 0, phase["scenarios"])
        self.operation["phases"] = list(self.phases.values())
        self.operation["kind"] = evidence_kind(self.operation, self.operation["tested"], self.operation["scenarios"])
        self.operation["hidden_probe"] = (not self.operation["expected"] and self.operation["kind"] == "pass" and
                                          0 < self.operation["case_count"] == self.operation["unsupported_cases"])


def summarize_run(run):
    operations = run["operations"]
    run["operation_counts"] = {"observed": sum(op["tested"] for op in operations),
                               "passed": sum(op["tested"] and op["kind"] in ("pass", "accepted") for op in operations),
                               "failed": sum(op["tested"] and op["kind"] == "fail" for op in operations)}
    run["failure_types"] = {}
    for op in operations:
        merge_types(run["failure_types"], op["failure_types"])


def combined_run(runs: list[dict]) -> dict:
    combined = {**runs[0], "id": "combined", "build_number": "All scenarios", "configuration_id": f"{len(runs)} scenarios combined",
                "coverage_href": None, "combined": True, "passed": all(r["passed"] for r in runs),
                "http_available": all(r["http_available"] for r in runs), "issues": [], "operations": []}
    for key in ("case_count", "check_count", "generated_count", "executed_count", "unexecuted_count",
                "unknown_execution_count", "verified_by_parent_count", "failure_count", "accepted_count", "success_count"):
        combined[key] = sum(r[key] for r in runs)
    operations = {}
    for run in runs:
        source = {"build_number": run["build_number"], "configuration_id": run["configuration_id"]}
        for op in run["operations"]:
            key = (op["method"], op["path"])
            if key not in operations:
                operations[key] = {**op, **dict.fromkeys(FACTS, 0), "expected": False, "tested": False,
                                   "scenarios": [], "phases": [], "failure_types": {}, "response_coverage": [], "method_probe": False}
            target = operations[key]
            for name in FACTS:
                target[name] += op[name]
            target["expected"] |= op["expected"]
            target["tested"] |= op["tested"]
            target["method_probe"] |= op["method_probe"]
            merge_types(target["failure_types"], op["failure_types"])
            scenarios = [{**s, "source": source} for s in op["scenarios"]]
            target["scenarios"].extend(scenarios)
            for phase in op["phases"]:
                match = next((p for p in target["phases"] if p["name"] == phase["name"]), None)
                if match is None:
                    match = {"name": phase["name"], **dict.fromkeys(FACTS, 0), "scenarios": [], "failure_types": {}, "indexes": []}
                    target["phases"].append(match)
                for name in FACTS:
                    match[name] += phase[name]
                merge_types(match["failure_types"], phase["failure_types"])
                match["indexes"].extend(phase["indexes"])
                match["scenarios"].extend({**s, "source": source} for s in phase["scenarios"])
            for response in op["response_coverage"]:
                match = next((r for r in target["response_coverage"] if r["status"] == response["status"]), None)
                if match is None:
                    target["response_coverage"].append(dict(response))
                else:
                    match["hits"] += response["hits"]
    for op in operations.values():
        op["kind"] = evidence_kind(op, op["tested"], op["scenarios"])
        op["hidden_probe"] = not op["expected"] and op["kind"] == "pass" and 0 < op["case_count"] == op["unsupported_cases"]
        for phase in op["phases"]:
            phase["kind"] = evidence_kind(phase, phase["case_count"] > 0, phase["scenarios"])
    combined["operations"] = list(operations.values())
    summarize_run(combined)
    return combined


def timestamp(value) -> float:
    if not value:
        return 0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


ASSETS = Path(__file__).with_name("report_assets")


def remove_generation(path: Path, data_root: Path) -> None:
    """Never follow a replaced generation directory outside this report's root."""
    if path.is_symlink() or path.is_junction() or path.resolve().parent != data_root.resolve():
        raise ValueError(f"Refusing to remove report data outside {data_root}")
    shutil.rmtree(path)


def publish_path(source: Path, destination: Path) -> None:
    # Windows scanners can briefly hold newly written scripts without delete
    # sharing. Retry only transient access errors; the old HTML stays in place.
    for delay in (0, 0.05, 0.1, 0.2, 0.4, 0.8, 2):
        if delay:
            time.sleep(delay)
        try:
            source.replace(destination)
            return
        except PermissionError:
            if os.name != "nt" or delay == 2:
                raise


def generate_report(input_dir: Path, output: Path, *, combine_runs: bool = False) -> tuple[int, list[str]]:
    input_dir = Path(input_dir)
    output = Path(output)
    databases = sorted(input_dir.rglob("evidence.sqlite"))
    if not databases:
        raise ValueError(f"No evidence.sqlite files found under {input_dir}")
    output.parent.mkdir(parents=True, exist_ok=True)
    data_root = output.with_name(output.stem + ".data")
    if data_root.is_symlink() or data_root.is_junction():
        raise ValueError(f"Report data directory must not be a link: {data_root}")
    generation = uuid.uuid4().hex
    destination = data_root / generation
    href = quote(data_root.name + "/" + generation, safe="/")
    runs: list[dict] = []
    warnings: list[str] = []
    published = False
    temporary = None
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".report-stage-") as staging:
        stage = Path(staging)
        for index, database in enumerate(databases):
            writer = BundleWriter(stage, href, f"r{index}")
            try:
                exported = load_database(database, output, bundle=writer, source_id=f"r{index}")
                writer.flush_details()
                runs.extend(exported)
            except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
                for path in writer.files:
                    path.unlink(missing_ok=True)
                warnings.append(f"Skipped {database}: {exc}")
        if not runs:
            raise ValueError("No readable evidence databases found")
        if combine_runs:
            if warnings:
                raise ValueError("Cannot combine incomplete evidence: " + "; ".join(warnings))
            if len({(run["base_url"], run["component_version"]) for run in runs}) != 1:
                raise ValueError("Combined scenarios must target the same API and component version")
        runs.sort(key=lambda run: timestamp(run["started_at"]), reverse=True)
        combined = combined_run(runs) if combine_runs else None
        writer = BundleWriter(stage, href, "summary")
        for run in [*runs, *([combined] if combined else [])]:
            run["operations_ref"] = writer.write_operations(run.pop("operations"))
        payload = compact_json({"format_version": 1, "runs": runs, "warnings": warnings,
                                "combine_runs": combine_runs, "combined": combined})
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        html = (ASSETS / "report.html").read_text(encoding="utf-8")
        brand_mark = quote((ASSETS / "brand-mark.svg").read_text(encoding="utf-8"), safe="")
        chartjs = ("/*\n" + (ASSETS / "vendor/chartjs-LICENSE.md").read_text(encoding="utf-8") + "\n" +
                   (ASSETS / "vendor/kurkle-color-LICENSE.md").read_text(encoding="utf-8") + "\n*/\n" +
                   (ASSETS / "vendor/chart.umd.min.js").read_text(encoding="utf-8"))
        html = (html.replace("__CSS__", (ASSETS / "style.css").read_text(encoding="utf-8"))
                .replace('src="brand-mark.svg"', f'src="data:image/svg+xml,{brand_mark}"')
                .replace("__CHARTJS__", chartjs)
                .replace("__JS__", (ASSETS / "app.js").read_text(encoding="utf-8"))
                .replace("__GENERATED__", generated).replace("__RUN_COUNT__", str(len(runs)))
                .replace("__DATA__", payload))
        # An owned generation is immutable; only the HTML switches readers to it.
        (stage / ".report-bundle").write_text("api-mitigation-report-v1", encoding="ascii")
        data_root.mkdir(exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, prefix=".report-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(html)
            # Copy into a new, unpublished generation instead of renaming a
            # populated directory: Windows can deny that rename even when the
            # individual files and destination are writable. The HTML is the
            # only publication pointer and is still replaced atomically last.
            destination.mkdir()
            for path in stage.iterdir():
                shutil.copyfile(path, destination / path.name)
            publish_path(temporary, output)
            published = True
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if not published and destination.is_dir():
                remove_generation(destination, data_root)
        # Prune only directories created by this exporter, never unrelated files.
        for previous in data_root.iterdir():
            if (previous != destination and not previous.is_symlink() and not previous.is_junction() and previous.is_dir() and
                    re.fullmatch(r"[0-9a-f]{32}", previous.name) and (previous / ".report-bundle").is_file()):
                try:
                    if (previous / ".report-bundle").read_text(encoding="ascii") == "api-mitigation-report-v1":
                        remove_generation(previous, data_root)
                except (OSError, UnicodeError, ValueError) as exc:
                    warnings.append(f"Could not remove obsolete report data {previous}: {exc}")
    return len(runs), warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline HTML report from archived evidence.sqlite files")
    parser.add_argument("--input-dir", type=Path, default=Path(os.environ.get("API_MITIGATION_OUTPUT_DIR", "artifacts")),
                        help="Directory recursively containing evidence.sqlite files (default: API_MITIGATION_OUTPUT_DIR or artifacts)")
    parser.add_argument("--output", type=Path, help="HTML file to create (default: <input-dir>/report.html)")
    parser.add_argument("--combine-runs", action="store_true",
                        help="Open with all scenarios in this invocation combined; use a single cohort directory")
    args = parser.parse_args()
    if args.output is None:
        args.output = args.input_dir / "report.html"
    try:
        count, warnings = generate_report(args.input_dir, args.output, combine_runs=args.combine_runs)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Report generation failed: {exc}\n")
    for warning in warnings:
        print(f"Warning: {warning}")
    print(f"Report: {args.output} ({count} runs; keep {args.output.stem}.data/ beside the HTML)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
