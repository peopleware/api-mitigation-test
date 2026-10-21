# Contributing

This repository delivers the same runner through a Docker image, GitHub Action, and Bitbucket pipe. Keep their behavior consistent and preserve the meaning of the evidence they produce.

## Develop and test locally

Use Python 3.12. From the repository root, create a virtual environment and activate it (`. .venv/bin/activate` in a POSIX shell or `.\.venv\Scripts\Activate.ps1` in PowerShell), then install dependencies and run the tests:

```sh
python -m venv .venv
# Activate the environment before continuing.
python -m pip --disable-pip-version-check install --no-cache-dir --no-index --no-deps --require-hashes -r requirements-bootstrap.txt
python -m pip install -r requirements.txt pytest==8.4.2
python -m pip check
python -m pytest -q tests
```

Run the bootstrap and dependency installation commands in existing environments
too. The [October 2026 emSign incident](https://discuss.python.org/t/109576)
requires updating both pip's bundled CA certificates and the application's
`certifi` package. The bootstrap verifies the pip wheel against the SHA256 from
the notice before installation. Repeat it after creating any new virtual
environment, since Python's bundled pip may still be affected. Keep the host's
OS certificate store updated as well.

With Node.js and npm installed, `package.json` also provides command shortcuts. Activate the Python virtual environment first so the shortcuts use its interpreter. No `npm install` is needed; the `version` field in `package.json` is the source of truth for the release version. Run `npm run` to list the shortcuts:

```sh
npm test
npm run test:version
npm run deps:check
npm run image:build
npm run version:bump -- patch
# Forward additional arguments to a command:
npm test -- -k version
```

The version shortcut creates a release commit and tag and requires a clean working tree, just like calling the Python script directly.

Tests use a disposable HTTP API. Wrapper tests require `sh` or `bash`; report interaction tests use Node.js when available. Container checks require a running Docker daemon. Use the same project-owned Docker Compose strategy for a local API sandbox. See [local environments and container testing](docs/local-testing.md) for the image build, sandbox setup, and smoke-test workflow.

Keep credentials, live API payloads, and generated artifacts out of commits. Run generated requests only against an isolated target with disposable data.

## Make a change

- Describe the problem, resulting behavior, and validation in your pull request.
- Keep target configuration project-owned. Use `runner.py` for shared execution, `hooks.py` for request/coverage hooks, and `report.py` plus `report_assets/` for the viewer.
- Add focused tests for changed behavior. Preserve coverage of authentication, configuration, verdicts, evidence, and all entry points.
- Update the relevant [consumer documentation](README.md) and linked guides. Record an ADR when changing evidence or archival responsibilities.

Missing checks must never become passes. Exceptions must preserve original failures, and failed or incomplete runs must retain available evidence. Read [ADR 0001](docs/adr-0001-evidence-and-archival.md) and [ADR 0002](docs/adr-0002-html-evidence-report.md) before changing these contracts.

## Release

Run the test suite and container smoke checks before releasing. Version changes start from a clean working tree and must account for consumer compatibility. Follow the [release procedure](docs/releasing.md) for versioning, tags, publication, and verification.
