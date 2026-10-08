# Reports, verdicts, and evidence

The build ID is mandatory outside GitHub and Bitbucket. Each run creates `artifacts/<build-id>-<hash>/`. Set `API_MITIGATION_OUTPUT_DIR` to change the root. Reusing a build ID in the same root fails rather than overwriting evidence. The output includes `verdict.json`, `schemathesis.json`, `events.ndjson`, `junit.xml`, `coverage.html`, `coverage.json`, `console.log`, and `evidence.sqlite` when the tools produced them. The output root also receives a self-contained `report.html` that summarizes every `evidence.sqlite` currently under that root. The runner writes available evidence before returning a failure status. If HTML generation fails, it logs a warning without changing the test exit status.

## HTML evidence report

Open `artifacts/report.html` in a browser to review runs. The dark Swagger-style interface opens on the newest run. Use the run picker or previous/next buttons at the right of the header to navigate timestamps and verdicts. The selected run's metadata and operation, case, check, failure, and exception counts appear above operations grouped by entity. New runs use the first OpenAPI tag as the entity label and retain operation summaries; older or untagged operations use the resource name from their path. Expand an operation, then its phases and cases to inspect individual checks, skipped scenarios, exceptions, and HTTP exchanges. Search, result filters, and expand/collapse controls help locate evidence. The separate trends view compares unexplained failed checks and case counts within the same configuration ID and target URL and marks schema changes. Detailed coverage stays in the companion TraceCov `coverage.html`, linked when it is beside a database. The HTML report does not display coverage percentages.

CI workspaces often contain only the current build. To rebuild a historical report after restoring archived databases into directories below one root, run:

```sh
python report.py --input-dir archived-evidence --output archived-evidence/report.html
```

The command recursively finds files named `evidence.sqlite`. It skips unreadable or unrelated SQLite files with warnings and fails if no readable evidence remains. A report generated from archived SQLite alone works offline; TraceCov links appear only when `coverage.html` is also beside a database. Older databases remain readable, but their HTTP exchanges show as unavailable.

New databases retain sanitized request and response headers, URL, status, and JSON bodies for each observed case. Sensitive-looking fields are redacted before SQLite insertion; non-JSON and invalid JSON bodies are omitted. A JSON body is stored as at most 64 KiB of sanitized text, with a truncation label when needed. Schemathesis may truncate a large response before the runner receives it, in which case an incomplete JSON body is omitted. Redaction is best effort: custom secrets and failure messages can still contain sensitive data. Apply the same access controls and retention rules to `report.html` as to `evidence.sqlite`.

## Verdict and evidence

The runner fails on unexplained check failures, Schemathesis execution errors, incomplete runs, missing or invalid evidence, zero tested operations, and configured coverage thresholds below their minima. TraceCov's percentage is `null` when a dimension has no applicable items; an omitted threshold permits this, while a configured threshold requires numeric evidence. A passing check exists only when a `success` check result occurs in Schemathesis events. Missing results are never counted as passing.

The per-build SQLite database records build/component/config/schema identity and SHA-256 hashes, tool versions, selected schema operations and whether they were tested, unexpected observed operations, scenario skips, observed cases and individual check results, sanitized HTTP exchanges for new observations, matched exception details, the five coverage measures, and the final verdict. Every table has a local 64-bit integer `id`. The `build` table keeps the run label in `build_number`; operations and coverage link to the build, scenarios and observations link to both the build and an operation when identifiable, and checks link to their observation. Foreign keys are enforced when writing. IDs are local to one file, so consumers combining archived files must assign new IDs. The raw event and coverage reports remain beside it for inspection.

## Glossary

- **Operation:** An HTTP method and schema path, such as `POST /orders`.
- **Case:** A generated request and observed response for an operation.
- **Check:** A Schemathesis assertion evaluated against a case; a check failure has a concrete failure type.
- **Exception:** A time-limited, owner-assigned acceptance of one method/path/failure-type combination; it preserves the original failure.
- **Coverage:** TraceCov's measurement of exercised operations, parameters, JSON Schema keywords, examples, and responses.
- **Configuration ID:** The consuming project's name for one target environment or setup.
- **Build ID:** CI run identity, or the explicit identifier supplied for direct Docker use.
- **Evidence database:** The self-contained SQLite summary for one build, intended for durable archival.

See [ADR 0001](adr-0001-evidence-and-archival.md) and [ADR 0002](adr-0002-html-evidence-report.md) for design decisions.
