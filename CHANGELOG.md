<!-- Copyright 2026 PeopleWare N.V. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

## Unreleased

### Features

- Add `output_mode: essentials` to retain only SQLite evidence and the main HTML
  report after successful report generation.
- Allow disabling coverage measurement with `coverage_enabled: false` and
  skipping the separate TraceCov HTML report with `coverage_html: false`.

### Fixes

- Keep unsupported-method checks in their tagged report groups and hide passing
  unsupported-method probes from reports.

### Maintenance

- Remove processed event streams from run output and run issue notices from the
  report overview.
- Restructure the configuration reference as a man page.
- Use versioned changelog entries as GitHub release notes and promote the
  Unreleased section when creating a version commit.

## 1.0.0

### Changes

- Publish the Docker image under `ppwcode/api-mitigation-test`.
- Disable the Docker Hub overview update in the release workflow.
