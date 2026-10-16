# Local environments and container testing

See [CONTRIBUTING](../CONTRIBUTING.md) for Python setup and the test suite. Use the disposable API in `tests/fixture_api.py` for runner development; it avoids relying on a real application's database or IdP.

## Build and exercise the image

With Docker running, build from the root of this runner repository:

```sh
docker build -t daviddkppw/api-mitigation-test:local .
```

From PowerShell 7, run the repository-owned container smoke test:

```powershell
./dev/smoke/Test-Runner.ps1 -Image ppwcode/api-mitigation-test:local
```

This starts an isolated fixture on an internal Docker network, with no published
ports, database or identity provider. It verifies that a valid authenticated
response passes and a deliberately invalid response fails with recorded evidence.
Its authentication provider leaves negative-authentication probes under
Schemathesis's control. The wrapper removes its containers and network on exit;
both results remain under `artifacts/mitigation-runner/report.html`.
This fixture and the runner/report implementation belong to this repository.
Application-specific environments, tokens, scenarios and seed data belong to
the consuming API repository.

Switch to the consuming API repository containing your test configuration. Start an isolated target, export a fresh `API_MITIGATION_BEARER_TOKEN` if authentication is required, and run the local image:

```sh
docker run --rm \
  -e API_MITIGATION_BUILD_ID=local-test-001 \
  -e API_MITIGATION_BEARER_TOKEN \
  -v "$PWD:/workspace" -w /workspace \
  daviddkppw/api-mitigation-test:local --config api-mitigation.yml
```

Use a new build ID for each invocation. Add `--network` when the API is on a Docker network. Confirm that the API and schema are reachable from the container, not only from the host.

Inspect the exit status, `verdict.json`, `evidence.sqlite`, JUnit, event stream, and TraceCov reports. Open `artifacts/report.html` for the combined viewer. Repeat with a failing API or threshold to confirm failures retain available evidence. See [configuration](configuration.md) and [evidence](evidence.md).

## Prepare a local API sandbox

Keep a reproducible sandbox in the consuming repository. Supply the application's supported database and IdP, migrations, representative seed data, corresponding test identities and permissions, and any required queues, storage, or external-service test doubles.

Docker Compose is the recommended setup. A single Compose definition can coordinate healthy dependencies, a successful one-shot migration/seed job, API readiness, and a runner service on the same network. Use `service_healthy` and `service_completed_successfully` as appropriate; starting a container alone does not establish readiness. See [Compose dependency conditions](https://github.com/docker/compose/blob/main/pkg/compose/service_containers.go).

A project-owned script should start the sandbox, verify a protected request, obtain a fresh token, and invoke the runner. Match issuer/claims to the API and seeded application permissions. These scripts belong to the consuming project; this runner does not provision databases or identity providers. See [environment and authentication requirements](ci-integration.md#prepare-the-target).

Reset disposable state before independent runs because generated requests can modify it. Normal Compose `down` preserves named volumes; `down --volumes` removes them. Collect evidence before deleting sandbox state.
