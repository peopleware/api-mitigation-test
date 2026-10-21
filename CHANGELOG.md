<!-- Copyright 2026 PeopleWare N.V. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

## Unreleased

### Maintenance

- Update pinned Python dependencies to compatible stable releases, including
  Schemathesis 4.32.2 and TraceCov 0.25.4.

### Security

- Address the [emSign CA incident](https://discuss.python.org/t/109576) by updating
  certifi to 2026.10.19 and bootstrapping pip 26.2.2 from a hash-verified wheel
  before dependency installation in Docker, CI, and local development.
- Update the runner base to Python 3.12.15 on Debian Bookworm, refresh system CA
  certificates, and rebuild release images without cached layers. Existing
  environments and deployed images must be updated separately.

## 1.1.0

### Features

- Add a stacked failure-type chart to the Trends view with per-segment
  occurrence counts and navigation to matching run evidence.
- Add failure-type filtering to the report toolbar, including accepted
  failures and failures without a recorded type.
- Add `output_mode: essentials` to retain only SQLite evidence and the main HTML
  report after successful report generation.
- Allow disabling coverage measurement with `coverage_enabled: false` and
  skipping the separate TraceCov HTML report with `coverage_html: false`.

### Fixes

- Keep unsupported-method checks in their tagged report groups and hide passing
  unsupported-method probes from reports.

### Maintenance

- Skip standalone test workflow runs for version tag pushes because the release
  workflow already calls the tests.
- Remove processed event streams from run output and run issue notices from the
  report overview.
- Restructure the configuration reference as a man page.
- Use versioned changelog entries as GitHub release notes and promote the
  Unreleased section when creating a version commit.

## 1.0.0

### Changes

- Publish the Docker image under `ppwcode/api-mitigation-test`.
- Disable the Docker Hub overview update in the release workflow.
