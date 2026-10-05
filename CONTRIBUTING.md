# Contributing

Thanks for helping improve API mitigation test. This repository ships one runner through three entry points: a Docker image, a GitHub Action, and a Bitbucket pipe. Keep their behavior aligned and preserve the meaning of the evidence they produce.

## Before you start

- Open an issue or describe the proposed change in a pull request, including the behavior, affected entry points, and any change to the config or evidence format.
- Keep credentials, bearer tokens, live API responses, and generated `artifacts/` out of commits. Use the disposable API in `tests/fixture_api.py` for development.
- Assume tests can send `POST`, `PUT`, `PATCH`, and `DELETE` requests. Only run the tool against an isolated target with disposable data.
- Read [README.md](README.md) for the user contract and [ADR 0001](docs/adr-0001-evidence-and-archival.md) before changing verdict or archival behavior.

## Set up a development environment

Use Python 3.12, matching the Docker base image. From the repository root:

```sh
python -m venv .venv
# Activate the environment for your shell, then:
python -m pip install -r requirements.txt
python -m pip install pytest==8.4.2
python -m pip check
python -m pytest -q tests/test_runner.py
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1`; on POSIX shells, use `. .venv/bin/activate`. The tests start a local disposable HTTP API. Two tests exercise the shared shell entry point as the GitHub Action and Bitbucket pipe would use it; they need `sh` or `bash`. A running Docker daemon is needed for container build and smoke tests.

## Make a change

1. Keep target selection in the project-owned JSON or YAML config. Do not add a second target or put bearer tokens in that file. The Action accepts only `config`, the pipe accepts `CONFIG`, and direct Docker use accepts `--config`.
2. Change `runner.py` for shared behavior, `report.py` and `report_assets/` for the offline viewer, and `hooks.py` for Schemathesis or TraceCov hooks. Change `bitbucket-pipe/run.sh`, `action.yml`, or `pipe.yml` only when their entry point or metadata changes.
3. Add or update focused tests using the disposable API and synthetic evidence databases. Run `python -m pytest -q tests`; report interaction tests use Node.js when it is available. Cover both JSON and YAML if the config contract changes. Preserve tests for local and remote schema loading, authentication, exceptions, thresholds, zero-operation runs, report/database consistency, and both wrappers.
4. Update `README.md` for user-visible behavior. Record a new ADR in `docs/` when changing the evidence model, verdict meaning, or archival responsibility.
5. Run the test command above and review the diff. In the pull request, explain what changed, how it was tested, and any compatibility or evidence-format impact.

Do not turn missing events or absent check results into passing checks. A matching exception must retain the original Schemathesis failure, and an incomplete run must leave its available evidence behind.

## Test the container

With Docker running, build the candidate image before release:

```sh
docker build -t daviddkppw/api-mitigation-test:local .
```

Run it against an isolated API reachable **from the container**, mount the config and schema into the workspace, set a unique `API_MITIGATION_BUILD_ID`, and inspect `artifacts/<build-id>-<hash>/`. Check the process exit status, `verdict.json`, `evidence.sqlite`, JUnit, event stream, and TraceCov HTML/JSON. Repeat with a failing API or threshold and confirm that available evidence remains. Do not run this smoke test against production: normal Schemathesis phases include state-changing requests.

## Version and release

Use `python scripts/version.py <newversion|major|minor|patch|premajor|preminor|prepatch|prerelease>` to update the Docker version in `pipe.yml`, version references in the README and wrapper contract, then create a version commit and `v<version>` Git tag. The script reads the current version from `pipe.yml` and requires a clean working tree. Treat config, output, or verdict changes that require consumers to change their setup as breaking changes. Before running it:

1. Run the Python suite and a Docker smoke test. Review the reports and SQLite evidence for consistency and token redaction.
2. Run the version script on the intended release commit. It creates the exact version tag locally; push the commit and tag to trigger `.github/workflows/release.yml`, which builds and pushes `daviddkppw/api-mitigation-test:<major>.<minor>.<patch>` and its Docker major tag.
3. Verify the published Docker Hub digest and run the released image against the disposable test target. Move the GitHub `v<major>` Action tag to the latest compatible release commit and push it so `peopleware/api-mitigation-test@v<major>` resolves as documented. Coordinate changes to an existing major tag with maintainers and consumers.

Publishing requires access to the GitHub repository, the Docker Hub namespace, and repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. A local Docker build does not publish an image. Do not claim a release is available until the image and Git tags are visible and verified.

## Deploy and operate

The image and Action are delivery mechanisms; each consuming project owns its target config, CI invocation, secured `API_MITIGATION_BEARER_TOKEN`, and evidence retention. After a release, update consuming projects to the intended Action major tag or Bitbucket pipe image version, run against their isolated API, and capture artifacts even when the test fails. Bitbucket Cloud CI artifacts are short-lived; the consuming project must archive every per-build `evidence.sqlite` durably under its own retention and access rules. See [README.md](README.md) for example CI configurations and the output layout.
