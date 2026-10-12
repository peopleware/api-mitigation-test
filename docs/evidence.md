# Reports, verdicts, and evidence

The build ID is mandatory outside GitHub and Bitbucket. Each run creates `artifacts/<build-id>-<hash>/`. Set `API_MITIGATION_OUTPUT_DIR` to change the root. Reusing a build ID in the same root fails rather than overwriting evidence. The output includes `verdict.json`, `schemathesis.json`, `events.ndjson`, `junit.xml`, `coverage.html`, `coverage.json`, `console.log`, and `evidence.sqlite` when the tools produced them. The output root also receives a self-contained `report.html` that summarizes every `evidence.sqlite` currently under that root. The runner writes available evidence before returning a failure status. If HTML generation fails, it logs a warning without changing the test exit status.

## HTML evidence report

Open `artifacts/report.html` in a browser to review runs. The dark Swagger-style interface opens on the newest run. Use the run picker or previous/next buttons at the right of the header to navigate timestamps and verdicts. The selected run's metadata and operation, case, check, failure, and exception counts appear above operations grouped by entity. New runs use the first OpenAPI tag as the entity label and retain operation summaries; older or untagged operations use the resource name from their path. Expand an operation, then its phases and cases to inspect individual checks, skipped scenarios, exceptions, and HTTP exchanges. Search, result filters, and expand/collapse controls help locate evidence. The separate trends view compares unexplained failed checks and case counts within the same configuration ID and target URL and marks schema changes. Detailed coverage stays in the companion TraceCov `coverage.html`, linked when it is beside a database. The HTML report does not display coverage percentages.

CI workspaces often contain only the current build. To rebuild a historical report after restoring archived databases into directories below one root, run:

```sh
docker run --rm -v "$PWD:/workspace" -w /workspace \
  daviddkppw/api-mitigation-test:<version> report
```

In PowerShell:

```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace `
  daviddkppw/api-mitigation-test:<version> report
```

The `report` command only reads existing evidence; it does not run mitigation tests or require API configuration, credentials, or a build ID. By default it reads `artifacts/` (or `API_MITIGATION_OUTPUT_DIR` when set) and writes `report.html` there, replacing an existing report. For another archive location, append `--input-dir archived-evidence`; use `--output archived-evidence/custom.html` to choose a different output file. Paths are relative to the container's working directory unless absolute.

For a local Python installation, the equivalent command is:

```sh
python report.py --input-dir archived-evidence --output archived-evidence/report.html
```

The command recursively finds files named `evidence.sqlite`. It skips unreadable or unrelated SQLite files with warnings and fails if no readable evidence remains. A report generated from archived SQLite alone works offline; TraceCov links appear only when `coverage.html` is also beside a database. Older databases remain readable, but their HTTP exchanges show as unavailable.

New databases retain sanitized request and response headers, URL, status, and JSON bodies for each observed case. Sensitive-looking fields are redacted before SQLite insertion; non-JSON and invalid JSON bodies are omitted. A JSON body is stored as at most 64 KiB of sanitized text, with a truncation label when needed. Schemathesis may truncate a large response before the runner receives it, in which case an incomplete JSON body is omitted. Redaction is best effort: custom secrets and failure messages can still contain sensitive data. Apply the same access controls and retention rules to `report.html` as to `evidence.sqlite`.

## Verdict and evidence

The console prints the verdict and evidence directory, failed/passed/discovered operation counts, failed/unexplained/accepted/total check counts, elapsed duration, and a coverage summary with percentages and covered/total counts. Duration includes setup and testing through verdict assessment, excluding evidence database and HTML generation. Discovered operations are those in scope after operation selection and read-only filtering. An operation counts as passed when it has a successful check and no missing check/response evidence, invalid checks, unexplained failures, or errored/interrupted scenarios. Accepted exceptions permit an operation to pass when these other requirements are met. Failed operations are discovered operations that did not pass, including untested operations. Operation counts are independent of run-wide failures such as coverage thresholds. Check totals count observed check results; accepted failures are included in the failed count. Full issue details remain in `verdict.json` and the evidence reports.

The verdict is green for `PASS` and red for `FAIL`. Failed counts are red, passed operations green, accepted check failures yellow, and discovered/total counts and duration white. Coverage is green when it meets its configured minimum and red below that minimum; yellow means no minimum was configured. Unavailable coverage displays `N/A`, red when a minimum is required and yellow otherwise. Percentages and covered/total counts come directly from TraceCov. ANSI colors are emitted for terminals and CI logs; set `NO_COLOR` to disable them.

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
