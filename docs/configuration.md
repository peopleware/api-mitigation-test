# Configuration reference

Commit one JSON or YAML file per target. A relative `schema` path is resolved from this file's directory. The schema may instead be an HTTP(S) URL. The five thresholds are independent and optional; omitted thresholds never gate the run. A configured threshold with unavailable coverage fails the run.

```yaml
schema: ./openapi.yaml
base_url: https://test.example.net/api
component_version: 2.4.0
configuration_id: staging-eu
# Optional: test only GET, HEAD, and OPTIONS operations
read_only: true
# Optional: test only these exact method and schema path pairs
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

`test_operations` is optional. When present, it must be a nonempty list of exact HTTP method and OpenAPI schema path pairs (for example, `GET /orders/{orderId}`). Only those operations are selected for testing and recorded in the verdict and evidence database. Schemathesis's probes of undeclared HTTP methods are disabled for these runs so it does not send requests outside the list. Each pair must exist in the schema, and duplicates are rejected. Omit the setting to test every schema operation. When combined with `read_only: true`, every selected method must be `GET`, `HEAD`, or `OPTIONS`. TraceCov still measures coverage against the full schema, so coverage thresholds remain full-schema thresholds.

Exception fields are exact method, schema path, and Schemathesis failure type matches. All six fields are required. Expired exceptions never match. Original failure details remain in `events.ndjson`, `verdict.json`, and `evidence.sqlite`, even when a matching exception makes the failure acceptable. Set `API_MITIGATION_BEARER_TOKEN` as a CI secret when needed; never put it in the config. The runner replaces its value in generated text evidence. Treat reports as sensitive test data because they may contain API payloads.

See [CI integration](ci-integration.md) for execution and credentials, and [evidence](evidence.md) for verdict behavior.
