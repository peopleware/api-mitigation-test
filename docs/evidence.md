# Reports, verdicts, and evidence

The build ID is mandatory outside GitHub and Bitbucket. Each run creates `artifacts/<build-id>-<hash>/`. Set `API_MITIGATION_OUTPUT_DIR` to change the root. Reusing a build ID in the same root fails rather than overwriting evidence. The output includes `verdict.json`, `schemathesis.json`, `events.ndjson`, `junit.xml`, `coverage.html`, `coverage.json`, `console.log`, and `evidence.sqlite` when the tools produced them. The output root also receives a self-contained `report.html` that summarizes every `evidence.sqlite` currently under that root. The runner writes available evidence before returning a failure status. If HTML generation fails, it logs a warning without changing the test exit status.

## HTML evidence report

Open `artifacts/report.html` in a browser to review runs. The dark Swagger-style interface opens on the newest run. Use the run picker or previous/next buttons at the right of the header to navigate timestamps and verdicts. Report and history timestamps use `dd/MM/yyyy, HH:mm:ss` with a 24-hour clock in the browser's local timezone. Date-only exception expiries use `dd/MM/yyyy`. Chart date labels and tooltip date formats remain separate. The selected run's metadata and operation, case, check, failure, and exception counts appear above operations grouped by entity. New runs use the first OpenAPI tag as the entity label and retain operation summaries; operations outside the selected schema scope that have no tag inherit the first-tag category from other operations on the exact same path when those categories agree. This keeps unsupported-method probes with their tagged resource. Conflicting categories, declared untagged operations, and older evidence without tags use the resource name from their path. Category inheritance is computed per build when generating the report, so regenerating a report also updates existing evidence files that contain tags, without modifying those files. Expand an operation, then its phases and cases to inspect individual checks, skipped scenarios, exceptions, and HTTP exchanges. The compact toolbar provides search, result filters (including Incomplete), and expand/collapse controls. Entity shortcuts narrow the list without changing the original operation order. Select the Failed checks metric to filter failed operations. Stats retain their explanatory subtext; run issue details remain in the archived verdict rather than a separate overview summary. Result filters apply to operations, phases, and cases using each level's status; All restores the complete evidence. Summary counts continue to describe the full operation or phase. Case details show failures and accepted exceptions immediately, collapse passed checks and headers, and place the request and response side by side on desktop or stack them on mobile. Bodies show formatted JSON when possible, retain truncation labels, and offer copy and expand controls. Copy depends on browser clipboard access; selectable body text remains available offline. The separate Trends view shows changes from the previous run, charts unexplained failures in red, accepted failures in solid yellow, and total failed checks in blue together alongside observed, passed or accepted, and failed operations using bundled Chart.js with `tension: 0.4` and `cubicInterpolationMode: 'default'` within the same configuration ID and target URL, and provides a newest-first history table with schema-change markers. The Operations chart counts operations marked as tested, including operations outside the selected scope. Its passed-or-accepted line uses the same evidence classification as operation rows; failed and incomplete operations receive no pass credit. The red Failed operations line matches the report's Failed status; incomplete evidence remains a separate classification. The history Operations column shows passed or accepted / observed operations, for example `2/6`, preceded by a green ▲ for an increased passed/observed ratio, red ▼ for a decreased ratio, or neutral — for no change versus the preceding run in the same configuration/target group. The first run and comparisons involving zero observed operations have no indicator. Hover the cell for the difference in percentage points. Select a history run to open its operations. Chart.js and its license notices are embedded in the HTML; charts require no CDN or network connection. Charts use a dark tooltip with the build and status pill, a timestamp such as `13 Oct 2026 - 15:48:17`, all series values, and run stats. The timestamp and stats use smaller text. A vertical dotted guide covers all series for the selected run. Hover or touch a chart for run details, or focus it and use Left/Right, Home/End, and Escape to navigate its tooltip. Detailed coverage stays in the companion TraceCov `coverage.html`, linked when it is beside a database. The HTML report does not display coverage percentages.

CI workspaces often contain only the current build. To rebuild a historical report after restoring archived databases into directories below one root, run:

```sh
docker run --rm -v "$PWD:/workspace" -w /workspace \
  ppwcode/api-mitigation-test:<version> report
```

In PowerShell:

```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace `
  ppwcode/api-mitigation-test:<version> report
```

The `report` command only reads existing evidence; it does not run mitigation tests or require API configuration, credentials, or a build ID. By default it reads `artifacts/` (or `API_MITIGATION_OUTPUT_DIR` when set) and writes `report.html` there, replacing an existing report. For another archive location, append `--input-dir archived-evidence`; use `--output archived-evidence/custom.html` to choose a different output file. Paths are relative to the container's working directory unless absolute.

For a local Python installation, the equivalent command is:

```sh
python report.py --input-dir archived-evidence --output archived-evidence/report.html
```

The command recursively finds files named `evidence.sqlite`. It skips unreadable or unrelated SQLite files with warnings and fails if no readable evidence remains. A report generated from archived SQLite alone works offline; TraceCov links appear only when `coverage.html` is also beside a database. Older databases remain readable, but their HTTP exchanges show as unavailable.

New databases retain sanitized request and response headers, URL, status, and JSON bodies for each observed case. Sensitive-looking fields are redacted before SQLite insertion; non-JSON and invalid JSON bodies are omitted. A JSON body is stored as at most 64 KiB of sanitized text, with a truncation label when needed. Schemathesis may truncate a large response before the runner receives it, in which case an incomplete JSON body is omitted. Redaction is best effort: custom secrets and failure messages can still contain sensitive data. Apply the same access controls and retention rules to `report.html` as to `evidence.sqlite`.

## Verdict and evidence

The console prints the verdict and evidence directory, failed/passed/discovered operation counts, failed/unexplained/accepted/total check counts, elapsed duration, and a coverage summary with percentages and covered/total counts. Duration includes setup and testing through verdict assessment, excluding evidence database and HTML generation. Discovered operations are those in scope after operation selection and read-only filtering. An operation counts as passed when it has a successful check and no missing check/response evidence, invalid checks, unexplained failures, or errored/interrupted scenarios. Accepted exceptions permit an operation to pass when these other requirements are met. Failed operations are discovered operations that did not pass, including untested operations. Operation counts are independent of run-wide failures such as coverage thresholds. Check totals count observed check results; accepted failures are included in the failed count. Full issue details remain in `verdict.json` and the evidence database.

The verdict is green for `PASS` and red for `FAIL`. Failed counts are red, passed operations green, accepted check failures yellow, and discovered/total counts and duration white. Coverage is green when it meets its configured minimum and red below that minimum; yellow means no minimum was configured. Unavailable coverage displays `N/A`, red when a minimum is required and yellow otherwise. Percentages and covered/total counts come directly from TraceCov. ANSI colors are emitted for terminals and CI logs; set `NO_COLOR` to disable them.

The runner fails on unexplained check failures, Schemathesis execution errors, incomplete runs, missing or invalid evidence, zero tested operations, and configured coverage thresholds below their minima. TraceCov's percentage is `null` when a dimension has no applicable items; an omitted threshold permits this, while a configured threshold requires numeric evidence. A passing check exists only when a `success` check result occurs in Schemathesis events. Missing results are never counted as passing.

Authentication subrequests can be verified by an existing parent check without having their own check records. The runner requires an explicit `parent_id` within the same scenario recorder, the same method and schema path, no stateful transition, a recorded 401 or 403 response, a successful parent response (2xx), and a successful `ignored_auth` check on that parent. Missing, failed, or invalid parent authentication checks do not qualify, including failures accepted by an exception. Requests with their own checks retain their own results. A 401 or 403 alone is insufficient evidence.

Qualifying subrequests count as verified cases and do not block the operation or run merely because Schemathesis includes them in `test_cases.without_checks`. Generated cases with no recorded HTTP interaction and no checks are **Not executed**. They remain visible, earn no pass or coverage credit, and do not block the evidence-completeness gate. An operation containing only these cases is not tested. Cases with some execution evidence but missing response status or check outcomes remain **Incomplete evidence** and block passing; unknown check statuses also block passing. An intentionally omitted or truncated body alone does not make evidence incomplete when response status and check outcomes are recorded. Schemathesis execution errors and incomplete runs still fail under the existing rules.

The `without_checks` aggregate is accounted for by parent-verified and unexecuted cases; any excess still fails evidence validation. Check totals continue to count only actual check records. `verdict.json` records `cases.total` (all generated cases retained in events), `cases.generated`, `cases.executed`, `cases.unexecuted`, and `cases.verified_by_parent`. Executed counts include partially recorded cases and are not pass counts. Each qualifying HTML authentication case displays its parent case ID and the `ignored_auth` evidence source. New SQLite observations retain `parent_case_id`, nullable `verification_json`, and `execution_status` alongside the original checks and HTTP exchanges.

Classification is based on recorded evidence: wholly missing execution records are also treated as not executed, even if a request actually reached the API. A generated request definition, parent relationship, or stateful transition flag alone is not execution evidence. Older databases with HTTP columns can infer the classification from their retained exchanges, response statuses, and checks. Archives without HTTP columns retain unknown execution status where classification cannot be established. Rebuilding HTML never revises stored verdicts. See [the classification decision](adr-0003-generated-case-execution.md).

The per-build SQLite database records build/component/config/schema identity and SHA-256 hashes, tool versions, selected schema operations and whether they were tested, unexpected observed operations, scenario skips, observed cases and individual check results, sanitized HTTP exchanges for new observations, matched exception details, the five coverage measures, and the final verdict. Every table has a local 64-bit integer `id`. The `build` table keeps the run label in `build_number`; operations and coverage link to the build, scenarios and observations link to both the build and an operation when identifiable, and checks link to their observation. Foreign keys are enforced when writing. IDs are local to one file, so consumers combining archived files must assign new IDs. The raw event and coverage reports remain beside it for inspection.

The console places `Cases` between `Operations` and `Checks`, showing generated, executed, and not-executed counts, parent-verified cases when present, and failed cases only when nonzero. The HTML Cases card shows generated cases with separate executed and not-executed counts. A case with one or more failed checks counts once, including checks accepted by an exception, matching the check failure totals. The verdict records this count in `cases.failed`.

## Combined scenarios

The report displays documented response coverage and an expandable list of
method/path/status variants without native TraceCov hits. A combined view takes
the union of recorded variants, rather than averaging scenario percentages.
For missing 405 coverage, the list identifies successful unsupported-method
probes on the same URL without adding those probes to the native coverage count.

For one test invocation split into multiple scenarios (for example, an
administrator run, admission failures and session logout), rebuild its single
cohort directory with `report --input-dir <cohort> --combine-runs`. The report
opens with all operations together, deduplicated by HTTP method and path. Case
and check counts are summed; every case retains its source configuration and
run. One failed scenario makes the combined result fail. Individual scenarios
remain selectable for diagnosis and provenance. Their original verdicts and
TraceCov files remain unchanged; coverage percentages are never averaged.

Use this option only for scenarios belonging to the same test invocation, not a
directory of historical runs. Different API targets or component versions and
unreadable evidence are rejected rather than producing a partial combined view.

## Glossary

Unsupported-method coverage probes are attributed to their originating schema
operation. For example, a TRACE probe generated for `GET /orders` belongs to that
GET operation; its retained HTTP request still records `TRACE`. Such probes do
not invent additional schema operations, and their check failures still block
the owning operation. This mapping applies only to explicitly identified
`unspecified_http_method` coverage cases with a matching source path.

- **Operation:** An HTTP method and schema path, such as `POST /orders`.
- **Case:** A generated request definition for an operation, which may have execution evidence or remain unexecuted.
- **Not executed:** A generated case with no recorded HTTP interaction or check results; it earns no passing or coverage credit.
- **Incomplete evidence:** A case with recorded execution evidence insufficient to establish an outcome; it blocks passing.
- **Check:** A Schemathesis assertion evaluated against a case; a check failure has a concrete failure type.
- **Exception:** A time-limited, owner-assigned acceptance of one method/path/failure-type combination; it preserves the original failure.
- **Coverage:** TraceCov's measurement of exercised operations, parameters, JSON Schema keywords, examples, and responses.
- **Configuration ID:** The consuming project's name for one target environment or setup.
- **Build ID:** CI run identity, or the explicit identifier supplied for direct Docker use.
- **Evidence database:** The self-contained SQLite summary for one build, intended for durable archival.

See [ADR 0001](adr-0001-evidence-and-archival.md) and [ADR 0002](adr-0002-html-evidence-report.md) for design decisions.
