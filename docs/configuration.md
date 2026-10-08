# Configuration reference

Commit one JSON or YAML file per target. A relative `schema` path is resolved from this file's directory. The schema may instead be an HTTP(S) URL. The five thresholds are independent and optional; omitted thresholds never gate the run. A configured threshold with unavailable coverage fails the run.

```yaml
schema: ./openapi.yaml
base_url: https://test.example.net/api
component_version: 2.4.0
configuration_id: staging-eu
# Optional: test only GET, HEAD, and OPTIONS operations
read_only: true
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
check_exceptions:
  - method: POST
    path: /orders
    failure_type: AcceptedNegativeData
    reason: Legacy endpoint accepts an empty request while migration is in progress
    owner: API team
    expiry: '2026-12-31'
```

`stateful_max_examples` is optional. When set, it caps the stateful phase's generated examples per operation; smaller values reduce chained requests and evidence rows. The examples, coverage, and fuzzing phases still run. A cap can reduce the coverage the API actually exercises, so retain or set the `min_*_coverage` thresholds that matter to your project: the runner will fail if measured TraceCov coverage falls below them. Omit the setting to keep Schemathesis's default stateful behavior.

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

Each check exception requires `failure_type`, `reason`, `owner`, and `expiry`. Include both `method` and `path` to limit it to an exact HTTP method (case-insensitive) and OpenAPI schema path (case-sensitive). Omit both to accept that failure type across every operation:

```yaml
check_exceptions:
  - failure_type: AcceptedNegativeData
    reason: Legacy validation accepts invalid input while migration is in progress
    owner: API team
    expiry: '2026-12-31'
```

Providing only one of `method` or `path` is invalid. Global and operation-specific exceptions can coexist; the first unexpired matching entry supplies the recorded exception metadata. Failure types always match exactly, and expired exceptions never match. Original failure details remain in `events.ndjson`, `verdict.json`, and `evidence.sqlite`, even when a matching exception makes the failure acceptable. Exceptions do not bypass coverage thresholds, missing evidence, or execution errors.

Set `API_MITIGATION_BEARER_TOKEN` as a CI secret when needed; never put it in the config. The runner replaces its value in generated text evidence. Treat reports as sensitive test data because they may contain API payloads.

See [CI integration](ci-integration.md) for execution and credentials, and [evidence](evidence.md) for verdict behavior.
