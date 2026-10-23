"""Configuration regressions; no API requests or Schemathesis subprocesses are executed."""
import json
from pathlib import Path
import sys
import tomllib

import pytest
import schemathesis
from schemathesis.config import SchemathesisConfig

import runner
from runner import prepare_schemathesis_config, read_config


PATH = "/v1/funds/{fundId}/premiums/CHILD_CARE"
SCHEMA = {"openapi": "3.0.3", "info": {"title": "Fund API", "version": "1"}, "paths": {
    path: {"get": {"responses": {"200": {"description": "OK"}}}}
    for path in (PATH, "/other")
}}
STATUS = {"method": "get", "path": PATH, "check": "positive_data_acceptance",
          "expected_statuses": ["200"], "reason": "Seeded fund"}
CUSTOM = r'''
[dictionaries.funds]
values = [1, 5, 11]
[parameters]
fundId = { dictionary = "funds" }
"body.fundId" = 1
[phases.fuzzing.generation]
max-examples = 20
[phases.stateful.generation]
max-examples = 30
[phases.coverage]
enabled = false
unexpected-methods = ["DELETE"]
[[operations]]
include-path-regex = '/premiums/CHILD_CARE$'
parameters.fundId = 5
parameters."body.fundId" = 5
[operations.phases.fuzzing.generation]
max-examples = 7
[operations.phases.stateful.generation]
max-examples = 40
[operations.phases.coverage]
unexpected-methods = ["POST"]
[operations.phases.fuzzing.checks.positive_data_acceptance]
expected-statuses = ["201"]
[operations.checks.negative_data_rejection]
expected-statuses = ["400"]
enabled = false
[[operations]]
include-path-regex = '^/v1/funds/\{fundId\}/'
parameters.fundId = 1
'''


@pytest.fixture
def layout(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    (app / ".git").mkdir()
    monkeypatch.setattr(runner, "__file__", str(app / "runner.py"))
    project = tmp_path / "project with spaces"
    project.mkdir()
    config_path = project / "api-mitigation.yml"
    build = tmp_path / "artifacts" / "build"
    build.mkdir(parents=True)
    return app, config_path, build


@pytest.mark.parametrize("source_kind", ["discovered", "relative", "absolute"])
@pytest.mark.parametrize("settings", [
    {"test_operations": [{"method": "GET", "path": PATH}]},
    {"stateful_max_examples": 9},
    {"check_statuses": [STATUS]},
    {"test_operations": [{"method": "GET", "path": PATH}], "stateful_max_examples": 9,
     "check_statuses": [STATUS]},
])
def test_custom_settings_survive_runner_overlays(layout, source_kind, settings):
    app, config_path, build = layout
    source = (app if source_kind == "discovered" else config_path.parent) / "schemathesis.toml"
    source.write_text(CUSTOM, encoding="utf-8")
    config = dict(settings)
    if source_kind != "discovered":
        config["schemathesis_config"] = source.name if source_kind == "relative" else str(source)
        (app / "schemathesis.toml").write_text("invalid discovery file", encoding="utf-8")
    effective = prepare_schemathesis_config(config, config_path, build)
    assert effective == build / "schemathesis.toml"
    assert source.read_text(encoding="utf-8") == CUSTOM
    data = tomllib.loads(effective.read_text(encoding="utf-8"))
    assert data["dictionaries"]["funds"]["values"] == [1, 5, 11]
    assert data["parameters"] == {"fundId": {"dictionary": "funds"}, "body.fundId": 1}
    # Exercise the pinned Schemathesis resolver, including first-match parameter and check precedence.
    schema = schemathesis.openapi.from_dict(SCHEMA, config=SchemathesisConfig.from_path(effective))
    operation = schema[PATH]["get"]
    project = schema.config
    assert project.operations.get_for_operation(operation).parameters == {"fundId": 5, "body.fundId": 5}
    assert project.generation_for(operation=operation, phase="fuzzing").max_examples == 7
    assert project.generation_for(operation=None, phase="fuzzing").max_examples == 20
    assert project.generation_for(operation=operation, phase="stateful").max_examples == settings.get("stateful_max_examples", 40)
    assert project.generation_for(operation=None, phase="stateful").max_examples == settings.get("stateful_max_examples", 30)
    coverage = project.phases_for(operation=operation).coverage
    assert coverage.enabled is False
    assert coverage.unexpected_methods == (set() if "test_operations" in settings else {"post"})
    checks = project.checks_config_for(operation=operation, phase="fuzzing")
    assert checks.positive_data_acceptance.expected_statuses == (["200"] if "check_statuses" in settings else ["201"])
    assert checks.negative_data_rejection.expected_statuses == ["400"]
    assert checks.negative_data_rejection.enabled is False
    other = schema["/other"]["get"]
    assert project.checks_config_for(operation=other).positive_data_acceptance.expected_statuses != ["200"]


def test_named_project_settings_and_operation_order_survive(layout):
    app, config_path, build = layout
    source = app / "schemathesis.toml"
    source.write_text(CUSTOM + '''
[[project]]
title = "Fund API"
[project.parameters]
employeeId = 231
[project.phases.stateful.generation]
max-examples = 80
[[project.operations]]
include-path = "/other"
parameters.fundId = 11
[project.operations.phases.coverage]
unexpected-methods = ["DELETE"]
[project.operations.phases.stateful.generation]
max-examples = 90
[project.operations.checks.positive_data_acceptance]
expected-statuses = ["202"]
''', encoding="utf-8")
    effective = prepare_schemathesis_config({"test_operations": [{"method": "GET", "path": "/other"}],
                                            "stateful_max_examples": 9,
                                            "check_statuses": [{**STATUS, "path": "/other"}]}, config_path, build)
    schema = schemathesis.openapi.from_dict(SCHEMA, config=SchemathesisConfig.from_path(effective))
    operation = schema["/other"]["get"]
    assert schema.config.parameters["employeeId"] == 231
    assert schema.config.operations.get_for_operation(operation).parameters == {"fundId": 11}
    assert schema.config.generation_for(operation=operation, phase="stateful").max_examples == 9
    assert schema.config.phases_for(operation=operation).coverage.unexpected_methods == set()
    assert schema.config.checks_config_for(operation=operation).positive_data_acceptance.expected_statuses == ["200"]


def test_file_dictionary_keeps_its_source_directory(layout):
    _, config_path, build = layout
    source = config_path.parent / "custom.toml"
    dictionary = source.with_name("funds.dict")
    dictionary.write_text('"1"\n"5"\n', encoding="utf-8")
    source.write_text('[dictionaries.funds]\nfrom-file = "funds.dict"\n[parameters]\nfundId = { dictionary = "funds" }\n', encoding="utf-8")
    effective = prepare_schemathesis_config({"schemathesis_config": source.name, "stateful_max_examples": 9}, config_path, build)
    loaded = SchemathesisConfig.from_path(effective)
    assert loaded.dictionaries["funds"].source_path == str(dictionary)
    assert [entry.value for entry in loaded.dictionaries["funds"].entries] == ["1", "5"]


def test_multiple_status_overrides_keep_unmentioned_check_options(layout):
    app, config_path, build = layout
    (app / "schemathesis.toml").write_text(CUSTOM, encoding="utf-8")
    effective = prepare_schemathesis_config({"check_statuses": [STATUS, {
        **STATUS, "check": "negative_data_rejection", "expected_statuses": ["422"],
    }]}, config_path, build)
    schema = schemathesis.openapi.from_dict(SCHEMA, config=SchemathesisConfig.from_path(effective))
    checks = schema.config.checks_config_for(operation=schema[PATH]["get"], phase="fuzzing")
    assert checks.positive_data_acceptance.expected_statuses == ["200"]
    assert checks.negative_data_rejection.expected_statuses == ["422"]
    assert checks.negative_data_rejection.enabled is False


@pytest.mark.parametrize("explicit", [False, True])
def test_custom_without_generated_settings_uses_original_file(layout, explicit):
    app, config_path, build = layout
    source = (config_path.parent if explicit else app) / "schemathesis.toml"
    source.write_text(CUSTOM, encoding="utf-8")
    config = {"schemathesis_config": source.name} if explicit else {}
    assert prepare_schemathesis_config(config, config_path, build) == source
    assert not (build / "schemathesis.toml").exists()
    assert source.read_text(encoding="utf-8") == CUSTOM


@pytest.mark.parametrize("settings,expected", [
    ({}, None),
    ({"check_statuses": []}, None),
    ({"test_operations": [{"method": "GET", "path": PATH}]}, {"phases": {"coverage": {"unexpected-methods": []}}}),
    ({"stateful_max_examples": 9}, {"phases": {"stateful": {"generation": {"max-examples": 9}}}}),
    ({"check_statuses": [STATUS]}, {"operations": [{"include-name": f"GET {PATH}",
      "checks": {"positive_data_acceptance": {"expected-statuses": ["200"]}}}]}),
])
def test_no_custom_file_preserves_default_behavior(layout, settings, expected):
    _, config_path, build = layout
    effective = prepare_schemathesis_config(settings, config_path, build)
    if expected is None:
        assert effective is None
        assert not (build / "schemathesis.toml").exists()
    else:
        assert tomllib.loads(effective.read_text(encoding="utf-8")) == expected
        SchemathesisConfig.from_path(effective)


def test_discovery_uses_subprocess_directory_and_parent_boundary(layout):
    app, config_path, build = layout
    # A file beside the runner YAML was never discovered by the /app subprocess.
    (config_path.parent / "schemathesis.toml").write_text("invalid", encoding="utf-8")
    (app.parent / "schemathesis.toml").write_text("invalid", encoding="utf-8")
    assert prepare_schemathesis_config({}, config_path, build) is None
    (app / ".git").rmdir()
    parent = app.parent / "schemathesis.toml"
    parent.write_text(CUSTOM, encoding="utf-8")
    assert prepare_schemathesis_config({}, config_path, build) == parent


@pytest.mark.parametrize("value", [None, True, 7, [], {}, "", "  "])
def test_invalid_custom_config_option(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema": "schema.yaml", "base_url": "http://example.test",
                               "component_version": "1", "configuration_id": "test",
                               "schemathesis_config": value}), encoding="utf-8")
    with pytest.raises(ValueError, match="schemathesis_config must be a nonempty file path"):
        read_config(path)


def test_unreadable_custom_file_is_a_configuration_error(layout, monkeypatch):
    app, config_path, build = layout
    source = app / "schemathesis.toml"
    source.touch()
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == source:
            raise PermissionError("test read-only mount permission failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(ValueError, match="test read-only mount permission failure"):
        prepare_schemathesis_config({"stateful_max_examples": 9}, config_path, build)


@pytest.mark.parametrize("contents", [None, "directory", "[parameters", "phases = 3", "unknown-key = true",
                                     '[parameters]\nfundId = { dictionary = "missing" }'])
@pytest.mark.parametrize("explicit", [False, True])
def test_invalid_custom_config_fails_before_schema_or_subprocess(layout, monkeypatch, contents, explicit):
    app, config_path, build = layout
    source = (config_path.parent if explicit else app) / "schemathesis.toml"
    if contents == "directory":
        source.mkdir()
    elif contents is not None:
        source.write_text(contents, encoding="utf-8")
    # Missing discovery files (and directories) retain the original no-custom behavior.
    if not explicit and contents in (None, "directory"):
        assert prepare_schemathesis_config({}, config_path, build) is None
        return
    config = {"schema": "http://example.test/openapi.json", "base_url": "http://example.test",
              "component_version": "1", "configuration_id": "test", "stateful_max_examples": 9}
    if explicit:
        config["schemathesis_config"] = source.name
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("API_MITIGATION_OUTPUT_DIR", str(build.parent))
    monkeypatch.setenv("API_MITIGATION_BUILD_ID", "invalid-custom")
    monkeypatch.setattr(sys, "argv", ["runner.py", "--config", str(config_path)])
    monkeypatch.setattr(runner, "write_database", lambda *args: None)
    monkeypatch.setattr(runner, "refresh_report", lambda *args: True)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid configuration must fail before loading a schema or executing tests")

    monkeypatch.setattr(runner, "load_schema", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    assert runner.main() == 2
    error_path, = build.parent.glob("*/runner-error.json")
    error = json.loads(error_path.read_text(encoding="utf-8"))
    assert "Invalid Schemathesis configuration" in error["error"]
    assert str(source) in error["error"]


def test_main_passes_merged_file_and_operation_selection_to_cli(layout, monkeypatch):
    app, config_path, build = layout
    source = app / "schemathesis.toml"
    source.write_text(CUSTOM + '\n[headers]\nAuthorization = "Bearer test-secret-token"\n', encoding="utf-8")
    schema_path = config_path.with_name("schema.json")
    schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    config_path.write_text(json.dumps({"schema": "schema.json", "base_url": "http://example.test",
                                      "component_version": "1", "configuration_id": "test",
                                      "test_operations": [{"method": "GET", "path": PATH}],
                                      "stateful_max_examples": 9, "check_statuses": [STATUS]}), encoding="utf-8")
    monkeypatch.setenv("API_MITIGATION_OUTPUT_DIR", str(build.parent))
    monkeypatch.setenv("API_MITIGATION_BUILD_ID", "captured-command")
    monkeypatch.setenv("API_MITIGATION_BEARER_TOKEN", "test-secret-token")
    monkeypatch.setattr(sys, "argv", ["runner.py", "--config", str(config_path)])
    monkeypatch.setattr(runner, "schemathesis_executable", lambda: "schemathesis")
    monkeypatch.setattr(runner, "write_database", lambda *args: None)
    monkeypatch.setattr(runner, "refresh_report", lambda *args: True)
    captured = []

    def capture(command, **kwargs):
        captured.append((command, kwargs))
        raise RuntimeError("Stop before executing Schemathesis")

    monkeypatch.setattr(runner.subprocess, "run", capture)
    assert runner.main() == 2
    (command, kwargs), = captured
    assert command[:2] == ["schemathesis", "--config-file"]
    effective = Path(command[2])
    assert effective != source
    assert effective.is_absolute()
    assert command[3:5] == ["run", str(schema_path)]
    index = command.index("--include-name")
    assert command[index + 1] == f"GET {PATH}"
    assert kwargs["cwd"] == app
    assert tomllib.loads(effective.read_text(encoding="utf-8"))["parameters"]["body.fundId"] == 1
    assert "test-secret-token" not in effective.read_text(encoding="utf-8")
    assert "test-secret-token" in source.read_text(encoding="utf-8")
