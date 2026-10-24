# ADR 0001: Per-build evidence and archival

Status: Accepted

## Context

An API mitigation result must be auditable after a CI job ends. A green CI status alone cannot identify the schema, target, tested operations, individual check outcomes, exceptions, or coverage. Bitbucket Cloud artifacts expire after at most 14 days.

## Decision

Produce one SQLite database per build, alongside the detailed Schemathesis and TraceCov reports. Record build, component, configuration, and schema identity, SHA-256 hashes of the config and schema bytes, tool versions, selected schema operations and tested flags, skipped scenarios, each observed case/check result, matched exception metadata, coverage values, and the final verdict. Never synthesize passing checks from missing events. Validate agreement between reported and observed operation counts and preserve original failures even when an exception matches.

The raw `events.ndjson` stream is an intermediate input to verdict assessment and SQLite persistence. Remove it after the database is successfully written, regardless of the test verdict, to reduce artifact size. Preserve it if parsing or persistence fails, and warn if cleanup fails without changing the exit status. SQLite retains the selected, sanitized evidence needed for archived reports; discarding the full event stream prevents later reprocessing of additional event details.

Each table has a local integer primary key. The build's textual run label is `build_number`; operations, scenarios, observations, and coverage reference the build, while checks reference observations. Scenarios and observations reference operations when their method and path can be identified. Unexpected operations are retained and marked outside the selected scope. Foreign keys restrict deletion of linked evidence. Archived files retain their original schema; this structure applies to newly generated files.

Coverage measurement is enabled by default. A consuming project can set `coverage_enabled: false` to skip TraceCov tracking and its HTML/JSON reports while retaining API checks and their evidence. Reject coverage thresholds in that configuration. Record the disabled state in the archived verdict and omit coverage rows, so missing measurements cannot be mistaken for measured zero coverage. `coverage_html: false` independently permits JSON-only measurement when coverage is enabled.

The consuming project owns the config and archives every `evidence.sqlite` to durable storage with its required access controls and retention period. CI artifact capture is a transport and short-term debugging aid. Artifact upload must run on failure, too. Publishing or archiving evidence externally is outside the test image because each consuming project has different storage governance.

Projects can choose `output_mode: essentials` to retain only the per-build SQLite
database and the main report bundle (HTML and companion data directory) after both are successfully written. This mode
skips the companion coverage HTML and removes other generated outputs for that
run, including JUnit and the standalone verdict; their absence is intentional.
Recorded API failures still receive compact output because the database preserves
their evidence. Processing or main report generation failures retain diagnostic
files. Coverage measurement is controlled separately and remains enabled by
default. Existing runs are not pruned.

## Consequences

The database and companion reports make a build's actual scope reviewable. A build can pass with documented, unexpired exceptions or omitted coverage thresholds; the verdict records both. A passing build states what was verified during that run and does not establish that the entire system has no vulnerabilities. Missing reports, a zero-operation run, or unexplained failures fail closed while retaining available files for diagnosis.
