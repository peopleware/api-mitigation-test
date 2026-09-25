# ADR 0001: Per-build evidence and archival

Status: Accepted

## Context

An API mitigation result must be auditable after a CI job ends. A green CI status alone cannot identify the schema, target, tested operations, individual check outcomes, exceptions, or coverage. Bitbucket Cloud artifacts expire after at most 14 days.

## Decision

Produce one SQLite database per build, alongside the detailed Schemathesis and TraceCov reports. Record build, component, configuration, and schema identity, SHA-256 hashes of the config and schema bytes, tool versions, all schema operations and tested flags, skipped scenarios, each observed case/check result, matched exception metadata, coverage values, and the final verdict. Never synthesize passing checks from missing events. Validate agreement between reported and observed operation counts and preserve original failures even when an exception matches.

The consuming project owns the config and archives every `evidence.sqlite` to durable storage with its required access controls and retention period. CI artifact capture is a transport and short-term debugging aid. Artifact upload must run on failure, too. Publishing or archiving evidence externally is outside the test image because each consuming project has different storage governance.

## Consequences

The database and companion reports make a build's actual scope reviewable. A build can pass with documented, unexpired exceptions or omitted coverage thresholds; the verdict records both. A passing build states what was verified during that run and does not establish that the entire system has no vulnerabilities. Missing reports, a zero-operation run, or unexplained failures fail closed while retaining available files for diagnosis.
