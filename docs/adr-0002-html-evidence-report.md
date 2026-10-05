# ADR 0002: Offline HTML evidence report

Status: Accepted

## Context

Per-build SQLite files preserve auditable results but are difficult to inspect across runs. TraceCov's HTML covers coverage, not the runner's verdict, check results, exceptions, or per-phase evidence. The existing database records only response status, while the Schemathesis event stream includes HTTP interactions.

## Decision

Generate one self-contained HTML report from all `evidence.sqlite` files available under the output root after each run. Also provide a rebuild command for archived databases. Report generation failures warn without changing the test verdict or exit status. Older database schemas remain readable, with absent HTTP exchanges clearly labeled. Companion TraceCov HTML is linked only when available; coverage measures are not copied into this report.

For newly written databases, store sanitized and bounded request and response details with each observation. Redact sensitive-looking headers, URL fields, and JSON keys; omit non-JSON bodies. Limit stored JSON body text to 64 KiB. Retain operation tags and summaries from the tested schema to group the report by entity; use the first tag, with a resource-path fallback for untagged operations and older databases. Keep the report and database under the consuming project's evidence access controls because redaction cannot guarantee removal of every secret.

## Consequences

Reports can compare the runs restored into one directory without a server or external assets. CI jobs with ephemeral workspaces show only the current run until archived databases are restored and the report is rebuilt. The SQLite schema gains nullable HTTP detail columns; archived files retain their original schema and remain readable. Large archives can produce large self-contained HTML files even with per-body limits.
