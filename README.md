# API mitigation test

Run [Schemathesis](https://schemathesis.readthedocs.io/en/latest/) against one OpenAPI target and collect [TraceCov](https://docs.tracecov.sh/) coverage. The Docker image, GitHub action, and Bitbucket pipe invoke the same `runner.py`. Version `1.0.0` is the Docker Hub tag and `v1` is the intended GitHub major-version tag. These names become usable after the repository and image are published.

The runner enables Schemathesis's normal examples, coverage, fuzzing, and stateful phases. **It sends state-changing requests.** Point it at an isolated test environment with disposable data and suitable credentials. A successful build identifies exactly what was verified; it does not prove that the whole system is vulnerability-free.

## Project configuration

Commit one JSON or YAML file per target. A relative `schema` path is resolved from this file's directory. The schema may instead be an HTTP(S) URL. The five thresholds are independent and optional; omitted thresholds never gate the run. A configured threshold with unavailable coverage fails the run.

```yaml
schema: ./openapi.yaml
base_url: https://test.example.net/api
component_version: 2.4.0
configuration_id: staging-eu
min_operation_coverage: 90
min_parameters_coverage: 60
min_keywords_coverage: 40
min_examples_coverage: 0
min_responses_coverage: 75
check_exceptions:
  - method: POST
    path: /orders
    failure_type: AcceptedNegativeData
    reason: Legacy endpoint accepts an empty request while migration is in progress
    owner: API team
    expiry: '2026-12-31'
```

Exception fields are exact method, schema path, and Schemathesis failure type matches. All six fields are required. Expired exceptions never match. Original failure details remain in `events.ndjson`, `verdict.json`, and `evidence.sqlite`, even when a matching exception makes the failure acceptable. Set `API_MITIGATION_BEARER_TOKEN` as a CI secret when needed; never put it in the config. The runner replaces its value in generated text evidence. Treat reports as sensitive test data because they may contain API payloads.

## GitHub Actions

```yaml
jobs:
  api-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ppwcode/api-mitigation-test@v1
        with:
          config: tests/api-mitigation.yaml
        env:
          API_MITIGATION_BEARER_TOKEN: ${{ secrets.API_MITIGATION_BEARER_TOKEN }}
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: api-mitigation-evidence
          path: artifacts/**
          if-no-files-found: warn
```

The build identity is `github-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`. Configure a repository retention period and archive the SQLite file elsewhere if a longer audit period is required.

## Bitbucket Pipelines

```yaml
pipelines:
  default:
    - step:
        name: API mitigation test
        script:
          - pipe: ppwcode/api-mitigation-test:1.0.0
            variables:
              CONFIG: tests/api-mitigation.yaml
              API_MITIGATION_BEARER_TOKEN: $API_MITIGATION_BEARER_TOKEN
        artifacts:
          - name: API mitigation evidence
            paths:
              - artifacts/**
            capture-on: always
```

Store the token as a secured Bitbucket variable. The build identity is `bitbucket-<BITBUCKET_BUILD_NUMBER>`. Bitbucket Cloud CI artifacts last [at most 14 days](https://support.atlassian.com/bitbucket-cloud/docs/use-artifacts-in-steps/). **The consuming project must archive each build's `evidence.sqlite` durably**, including failed builds, using its own approved storage and retention rules. `capture-on: always` retains available evidence when the test step fails; it does not replace durable archival.

## Direct Docker use

```sh
docker run --rm \
  -e API_MITIGATION_BUILD_ID=manual-2026-09-25-1 \
  -e API_MITIGATION_BEARER_TOKEN \
  -v "$PWD:/workspace" -w /workspace \
  ppwcode/api-mitigation-test:1.0.0 --config tests/api-mitigation.yaml
```

The build ID is mandatory outside GitHub and Bitbucket. Each run creates `artifacts/<build-id>-<hash>/`. Set `API_MITIGATION_OUTPUT_DIR` to change the root. Reusing a build ID in the same root fails rather than overwriting evidence. The output includes `verdict.json`, `schemathesis.json`, `events.ndjson`, `junit.xml`, `coverage.html`, `coverage.json`, `console.log`, and `evidence.sqlite` when the tools produced them. The runner writes available evidence before returning a failure status.

## Verdict and evidence

The runner fails on unexplained check failures, Schemathesis execution errors, incomplete runs, missing or invalid evidence, zero tested operations, and configured coverage thresholds below their minima. TraceCov's percentage is `null` when a dimension has no applicable items; an omitted threshold permits this, while a configured threshold requires numeric evidence. A passing check exists only when a `success` check result occurs in Schemathesis events. Missing results are never counted as passing.

The per-build SQLite database records build/component/config/schema identity and SHA-256 hashes, tool versions, every schema operation and whether it was tested, scenario skips, observed cases and individual check results, matched exception details, the five coverage measures, and the final verdict. The raw event and coverage reports remain beside it for inspection.

## Development

Install Python 3.12, then `pip install -r requirements.txt pytest==8.4.2` and run `pytest -q`. The integration tests start a disposable local HTTP API. Docker integration requires a running Docker daemon. The release workflow publishes `ppwcode/api-mitigation-test:<version>` from a `v<version>` Git tag with `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets. Publishing the image requires Docker Hub access; publishing `ppwcode/api-mitigation-test@v1` requires access to that GitHub repository and major-version tag.

## Glossary

- **Operation:** An HTTP method and schema path, such as `POST /orders`.
- **Case:** A generated request and observed response for an operation.
- **Check:** A Schemathesis assertion evaluated against a case; a check failure has a concrete failure type.
- **Exception:** A time-limited, owner-assigned acceptance of one method/path/failure-type combination; it preserves the original failure.
- **Coverage:** TraceCov's measurement of exercised operations, parameters, JSON Schema keywords, examples, and responses.
- **Configuration ID:** The consuming project's name for one target environment or setup.
- **Build ID:** CI run identity, or the explicit identifier supplied for direct Docker use.
- **Evidence database:** The self-contained SQLite summary for one build, intended for durable archival.
