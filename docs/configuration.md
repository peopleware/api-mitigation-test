# Configuration reference

## NAME

API mitigation configuration: target, operation selection, checks, coverage, and output settings.

## SYNOPSIS

One JSON or YAML configuration file per target. The following keys are required:

```yaml
schema: ./openapi.yaml
base_url: https://test.example.net/api
component_version: 2.4.0
configuration_id: staging-eu
```

## DESCRIPTION

Commit the configuration file alongside the project. Each top-level option is
listed below with its type, default, accepted values, and relevant interactions.
Unknown keys are rejected. YAML examples use the same keys and values as JSON.

## OPTIONS

### `schema: <path-or-url>`

**Type:** nonempty string. **Required:** yes. **Default:** none.

OpenAPI schema to test. Accepted forms:

- A relative file path, resolved from the configuration file's directory, such as `./openapi.yaml`.
- An absolute file path.
- An HTTP(S) URL, such as `https://test.example.net/openapi.json`.

### `base_url: <url>`

**Type:** nonempty string. **Required:** yes. **Default:** none.

HTTP(S) base URL of the API under test, such as `https://test.example.net/api`.

### `component_version: <version>`

**Type:** nonempty string. **Required:** yes. **Default:** none.

Version of the component under test, recorded in the build evidence. For example,
`2.4.0`. No particular version format is required.

### `configuration_id: <identifier>`

**Type:** nonempty string. **Required:** yes. **Default:** none.

Identifier for the target configuration, recorded in the build evidence. For
example, `staging-eu`.

### `read_only: true | false`

**Type:** boolean. **Default:** `false`.

- `false`: allow all schema HTTP methods, subject to `test_operations` when configured.
- `true`: test only `GET`, `HEAD`, and `OPTIONS` operations.

When combined with `test_operations`, every selected method must be `GET`, `HEAD`,
or `OPTIONS`; other methods cause a configuration error.

### `test_operations: <selectors>`

**Type:** nonempty list of objects with `method` and `path`. **Default:** all schema operations.

`test_operations` is optional. When present, it must be a nonempty list of HTTP methods and exact OpenAPI schema paths or path globs. Methods are case-insensitive; paths are case-sensitive. Patterns match the entire schema path, including literal parameter placeholders such as `{orderId}`, rather than concrete request URLs.

- `*` matches zero or more characters within a single path segment.
- `?` matches exactly one character within a single path segment.
- `**` matches across path segments. A trailing `/**` includes the base path itself: `/v1/admins/**` matches `/v1/admins`, `/v1/admins/{id}`, and `/v1/admins/{id}/roles`. `**/` also allows zero intermediate segments.
- All other characters, including braces and square brackets, are literal.

For example, select all GET, PUT, and POST operations under `/v1/admins`:

```yaml
test_operations:
  - method: GET
    path: /v1/admins/**
  - method: PUT
    path: /v1/admins/**
  - method: POST
    path: /v1/admins/**
```

Each selector must match at least one schema operation with that method; otherwise the run fails before sending requests. Identical method/path selectors are rejected, while overlapping patterns select each operation once. Only the expanded operations are selected for testing and recorded in the verdict and evidence database. Schemathesis's probes of undeclared HTTP methods are disabled for these runs so it does not send requests outside the selection. Omit the setting to test every schema operation. When combined with `read_only: true`, every selected method must be `GET`, `HEAD`, or `OPTIONS`. TraceCov still measures coverage against the full schema, so coverage thresholds remain full-schema thresholds.

### `stateful_max_examples: <count>`

**Type:** positive integer. **Default:** Schemathesis's default stateful behavior.

`stateful_max_examples` is optional. When set, it caps the stateful phase's generated examples per operation; smaller values reduce chained requests and evidence rows. The examples, coverage, and fuzzing phases still run. A cap can reduce the coverage the API actually exercises, so retain or set the `min_*_coverage` thresholds that matter to your project: the runner will fail if measured TraceCov coverage falls below them. Omit the setting to keep Schemathesis's default stateful behavior.

### `require_success_responses: true | false`

**Type:** boolean. **Default:** `false`.

- `false`: do not require a successful response from every selected operation.
- `true`: require a 2xx response from every selected operation.

`require_success_responses` defaults to false. Set it for functional scenarios
that must exercise each selected operation successfully. An operation with only
4xx/5xx results, or no executed response, fails this gate even if other checks
pass. This does not replace schema or error checks. Leave it false for deliberate
denial-only scenarios, such as an authenticated identity without application access.

### `check_statuses: <overrides>`

**Type:** list of objects. **Default:** no overrides (`[]`).

Each entry has these required fields:

| Field | Accepted values |
| --- | --- |
| `method` | HTTP method, case-insensitive. |
| `path` | Exact selected OpenAPI schema path, case-sensitive. |
| `check` | `positive_data_acceptance`, `negative_data_rejection`, `missing_required_header`, or `not_a_server_error`. |
| `expected_statuses` | Nonempty list of strings: individual HTTP codes (`'200'`, `'428'`) or status classes (`'1xx'` through `'5xx'`). |
| `reason` | Nonempty explanation of the override. |

Use `check_statuses` when an operation uses a documented HTTP status that differs
from Schemathesis's defaults. For example, an absent ETag precondition uses 428:

```yaml
check_statuses:
  - method: PUT
    path: /items/{id}
    check: missing_required_header
    expected_statuses: ['400', '401', '403', '406', '415', '422', '428']
    reason: Missing If-Match uses HTTP 428 Precondition Required
```

Each entry requires all five fields and an exact selected schema operation.
Supported checks are `positive_data_acceptance`, `negative_data_rejection`,
`missing_required_header`, and `not_a_server_error`. Values replace that check's native expected statuses
for that operation only; include the default statuses you still expect. Duplicate
operation/check entries and unknown operations fail configuration validation.
These settings are recorded in the build configuration and its hash. They do not
disable checks, accept response-schema violations, or create check exceptions.

For an isolated dependency-outage scenario, `not_a_server_error` can explicitly
expect `['1xx', '2xx', '3xx', '4xx', '503']` on the selected health operation.
That operation still rejects unexpected 500/502 responses and validates the
documented 503 response schema. Keep this expectation in the outage scenario;
normal scenarios retain their default server-error checks. Configure
`positive_data_acceptance` for 503 there as well when valid requests should
observe the unavailable dependency.

### `check_exceptions: <exceptions>`

**Type:** list of objects. **Default:** no exceptions (`[]`).

Each entry has these fields:

| Field | Requirement and accepted values |
| --- | --- |
| `failure_type` | Required. Exact failure type name, such as `AcceptedNegativeData`. |
| `reason` | Required. Nonempty explanation of the exception. |
| `owner` | Required. Nonempty name of the responsible person or team. |
| `expiry` | Required. ISO date string, such as `'2026-12-31'`; quote it in YAML. |
| `method` | Optional with `path`. HTTP method, case-insensitive. |
| `path` | Optional with `method`. Exact OpenAPI schema path, case-sensitive. |

Supported scopes:

- **Operation-specific:** include both `method` and `path`.
- **Global:** omit both to match the failure type across all operations.

Each check exception requires `failure_type`, `reason`, `owner`, and `expiry`. Include both `method` and `path` to limit it to an exact HTTP method (case-insensitive) and OpenAPI schema path (case-sensitive). Omit both to accept that failure type across every operation:

```yaml
check_exceptions:
  - failure_type: AcceptedNegativeData
    reason: Legacy validation accepts invalid input while migration is in progress
    owner: API team
    expiry: '2026-12-31'
```

Providing only one of `method` or `path` is invalid. Global and operation-specific exceptions can coexist; the first unexpired matching entry supplies the recorded exception metadata. Failure types always match exactly, and expired exceptions never match. Original failure details remain in `verdict.json` and `evidence.sqlite`, even when a matching exception makes the failure acceptable. Exceptions do not bypass coverage thresholds, missing evidence, or execution errors.

### `coverage_enabled: true | false`

**Type:** boolean. **Default:** `true`.

- `true`: collect TraceCov measurements and apply configured coverage thresholds.
- `false`: disable TraceCov tracking and its reports; coverage thresholds must be omitted.

`coverage_enabled` defaults to true and must be a boolean. Set it to false to
disable TraceCov tracking and omit both `coverage.html` and `coverage.json`:

```yaml
coverage_enabled: false
```

Remove all `min_*_coverage` settings when disabling coverage; combining them is
a configuration error, including thresholds of zero. API checks, authentication,
test generation, verdicts, SQLite evidence, and the main `report.html` remain
enabled. The console reports coverage as disabled, and the archived verdict
records this choice. Skipping tracking and coverage reports can reduce runtime;
the savings depend on the workload. Schemathesis's test generation phase named
`coverage` still runs; it is independent of TraceCov measurement.

### `min_operation_coverage: <percentage>`

**Type:** number from `0` to `100`. **Default:** no threshold.

Minimum operation coverage percentage measured by TraceCov.
See [Coverage threshold rules](#coverage-threshold-rules).

### `min_parameters_coverage: <percentage>`

**Type:** number from `0` to `100`. **Default:** no threshold.

Minimum parameters coverage percentage measured by TraceCov.
See [Coverage threshold rules](#coverage-threshold-rules).

### `min_keywords_coverage: <percentage>`

**Type:** number from `0` to `100`. **Default:** no threshold.

Minimum keywords coverage percentage measured by TraceCov.
See [Coverage threshold rules](#coverage-threshold-rules).

### `min_examples_coverage: <percentage>`

**Type:** number from `0` to `100`. **Default:** no threshold.

Minimum examples coverage percentage measured by TraceCov.
See [Coverage threshold rules](#coverage-threshold-rules).

### `min_responses_coverage: <percentage>`

**Type:** number from `0` to `100`. **Default:** no threshold.

Minimum responses coverage percentage measured by TraceCov.
See [Coverage threshold rules](#coverage-threshold-rules).

### Coverage threshold rules

The five thresholds are independent and optional. Omitted thresholds never gate
the run. A configured threshold fails the run if measured coverage is below it
or coverage is unavailable. `0` is a configured threshold, not an omitted one.

All thresholds require `coverage_enabled: true`. Combining any threshold with
`coverage_enabled: false` is a configuration error, including thresholds of zero.
TraceCov measures against the full schema, even when `read_only` or
`test_operations` limits the operations tested.

### `coverage_html: true | false`

**Type:** boolean. **Default:** `true`.

- `true`: generate the separate TraceCov HTML report when coverage is enabled and `output_mode` is `full`.
- `false`: skip the separate TraceCov HTML report.

`coverage_html` defaults to true and must be a boolean. Set it to false to skip
generating the separate TraceCov `coverage.html` file. JSON coverage, coverage
thresholds, SQLite evidence, and the main `report.html` are still produced and
processed as usual. The main report omits the TraceCov link when HTML coverage
is disabled. This option has no effect when `coverage_enabled` is false.

### `output_mode: full | essentials`

**Type:** string. **Default:** `full`.

- `full`: retain all generated output, subject to `coverage_enabled` and `coverage_html`.
- `essentials`: retain the per-run SQLite evidence database and the main HTML report after successful report generation.

`output_mode` accepts `full` (the default) or `essentials`. To retain only the
per-run SQLite database and the main HTML report:

```yaml
output_mode: essentials
```

The runner writes `evidence.sqlite` in the build directory and `report.html` at
the output root. Once both are written, it removes intermediate reports, JUnit,
the console log, the standalone verdict, and generated Schemathesis configuration.
The verdict, checks, sanitized HTTP exchanges, and any coverage measurements remain
in SQLite; the main report can be rebuilt from it. The separate TraceCov HTML
report is not generated, regardless of `coverage_html`. Coverage collection and
thresholds remain enabled unless `coverage_enabled: false` is also set.

Cleanup applies to passing runs and recorded test failures. If processing or main
report generation fails, diagnostic files remain. File removal failures emit
warnings without changing the test exit status. Existing runs under the output
root are not cleaned up.

## ENVIRONMENT

### `API_MITIGATION_BEARER_TOKEN`

Bearer token for authenticated requests. Supply it as a CI secret.

### `API_MITIGATION_*_TOKEN`

Additional identity token variables whose values are also redacted from generated
text evidence.

Set `API_MITIGATION_BEARER_TOKEN` as a CI secret when needed; never put it in the config. The runner replaces its value in generated text evidence. Additional identities supplied through `API_MITIGATION_*_TOKEN` variables are redacted as well. Treat reports as sensitive test data because they may contain API payloads.

## EXAMPLES

A target configuration with operation selection, coverage thresholds, and an
operation-specific check exception:

```yaml
schema: ./openapi.yaml
base_url: https://test.example.net/api
component_version: 2.4.0
configuration_id: staging-eu
# Optional: test only GET, HEAD, and OPTIONS operations
read_only: true
# Optional: omit the separate TraceCov HTML report to reduce output size
coverage_html: false
# Optional: select methods with exact schema paths or path globs
test_operations:
  - method: GET
    path: /orders
min_operation_coverage: 90
min_parameters_coverage: 60
min_keywords_coverage: 40
min_examples_coverage: 0
min_responses_coverage: 75
# Optional: cap generated cases per operation in the stateful phase
stateful_max_examples: 20
# Optional: require a 2xx response for every selected operation
require_success_responses: true
check_exceptions:
  - method: POST
    path: /orders
    failure_type: AcceptedNegativeData
    reason: Legacy endpoint accepts an empty request while migration is in progress
    owner: API team
    expiry: '2026-12-31'
```

## SEE ALSO

[CI integration](ci-integration.md) for execution and credentials;
[evidence](evidence.md) for verdict behavior.
