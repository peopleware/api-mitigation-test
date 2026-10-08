# CI integration

Select a published image version or Action major tag from the [README](../README.md). Replace `<version>` and `<major-tag>` in the examples before use. Configuration and environment orchestration belong in the consuming API repository.

## Prepare the target

Your pipeline should deploy the component under test, wait for its database and other dependencies, apply migrations, seed data, and verify API readiness. Use isolated databases, storage, queues, and identity-provider configuration so test side effects remain within the test environment.

Obtain a fresh **access token** through your IdP's supported flow and pass the raw value as `API_MITIGATION_BEARER_TOKEN`, without the `Bearer ` prefix. The runner uses one fixed token per invocation and does not log in or refresh it. Ensure it remains valid for the entire run. Keep credentials in CI secret storage and tokens out of committed configuration and logs.

Match the token's issuer, audience, roles/scopes, and application-specific claims to the API. If claims map to database users, seed those users and their permissions too. A service identity does not automatically represent a human user. Use separate invocations and configuration IDs for different permission profiles; testing those profiles alone does not prove all authorization boundaries.

The runner must reach the API's `base_url` and any remote schema URL. The API must reach its IdP discovery/signing-key endpoints. For a protected schema, export a local copy first: the runner's schema download does not attach the API bearer token. Inside a container, `localhost` refers to that container; attach the runner to the appropriate network when using API service names.

## Docker image

From the consuming repository, with the token exported if needed, use a unique build ID and mount the configuration, schema, and writable output directory:

```sh
docker run --rm \
  -e API_MITIGATION_BUILD_ID=ci-build-123 \
  -e API_MITIGATION_BEARER_TOKEN \
  -v "$PWD:/workspace" -w /workspace \
  daviddkppw/api-mitigation-test:<version> --config tests/api-mitigation.yaml
```

Add `--network` with your target's network name when required. Outside GitHub/Bitbucket, `API_MITIGATION_BUILD_ID` is mandatory. Do not reuse it in the same output root. See [evidence outputs](evidence.md).

## GitHub Actions

```yaml
jobs:
  api-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: peopleware/api-mitigation-test@<major-tag>
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

These steps assume the API is already ready and reachable from the Action container. For dynamically acquired credentials, pass the token from your project's authentication step rather than using a long-lived stored token.

## Bitbucket Pipelines

For an API environment that is already running, use the pipe and supply its access token:

```yaml
pipelines:
  default:
    - step:
        name: API mitigation test
        script:
          - pipe: docker://daviddkppw/api-mitigation-test:<version>
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

## Recommended Docker Compose workflow

Use one project-owned `compose.yaml` and one mitigation configuration per target. This is the recommended approach for coordinating the API, database, IdP/test authentication, migrations, and runner in a reproducible environment. Hosted dependencies can remain dedicated test instances.

Have a project-owned orchestration script perform this sequence:

1. Start dependencies and wait for readiness.
2. Apply migrations and seed data and test identities.
3. Start the API and verify readiness and a representative authenticated request.
4. Obtain a fresh token and invoke the runner, preserving its exit status.
5. Collect available reports and service logs even on failure.
6. Archive evidence and remove disposable resources.

Give each build its own environment/database state. A Compose project can include a runner service on the API network; the CI worker must support the required container runtime and workspace mounts. Provisioning details depend on your API, DBMS, and IdP.

Add a runner service to your application's Compose definition (this fragment assumes an existing `api` service with a readiness health check):

```yaml
services:
  mitigation:
    image: ${API_MITIGATION_IMAGE:?Set a published runner image}
    profiles: [mitigation]
    working_dir: /workspace
    command: [--config, api-mitigation.yml]
    environment:
      - API_MITIGATION_BEARER_TOKEN
      - API_MITIGATION_BUILD_ID
    volumes:
      - .:/workspace
    depends_on:
      api:
        condition: service_healthy
```

Use the API service name in `base_url`, for example `http://api:8080/api`. Gate API startup on healthy dependencies and successful migration/seed completion in your Compose definition. Keep test identities aligned with seeded application users and permissions.

After the script has started the environment, verified readiness, and exported a fresh token and unique build ID, invoke `docker compose run --rm -T --no-deps mitigation`. Capture its exit status so cleanup cannot hide a failed verdict. Use a per-build `COMPOSE_PROJECT_NAME` and disposable data; isolation also requires appropriate published ports and external-resource names.

Keep cleanup in the CI platform's always-run mechanism, such as GitHub's `if: always()` or Bitbucket's `after-script`. In Bitbucket, an `after-script` command failure stops remaining cleanup commands and does not change the step's reported status. See [Bitbucket step options](https://support.atlassian.com/bitbucket-cloud/docs/step-options).

Retain `artifacts/**`, including `report.html`, and durably archive each `evidence.sqlite` even when the test fails. See [reports and retention](evidence.md).
