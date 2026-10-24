# ADR 0002: Offline HTML evidence report

Status: Accepted

## Context

Per-build SQLite files preserve auditable results but are difficult to inspect across runs. TraceCov's HTML covers coverage, not the runner's verdict, check results, exceptions, or per-phase evidence. The existing database records only response status, while the Schemathesis event stream includes HTTP interactions.

## Decision

Generate one offline HTML report bundle from all `evidence.sqlite` files available under the output root after each run. The bundle consists of a small HTML viewer/manifest and a companion `<output-stem>.data/` directory. Also provide a rebuild command for archived databases. Report generation failures warn without changing the test verdict or exit status. Older database schemas remain readable, with absent HTTP exchanges clearly labeled. Companion TraceCov HTML is linked only when available.

The combined scenario view also summarizes documented response coverage from the retained native TraceCov operation data. Count each method/path/status once across scenarios and list the variants without native hits. Unsupported-method probes that received 405 on the same URL are identified separately; they do not retroactively earn native response coverage. This keeps a missing probe distinct from TraceCov's attribution limitation. Other coverage dimensions remain in the companion TraceCov reports, and stored verdicts are never revised by this presentation.

For newly written databases, store sanitized and bounded request and response details with each observation. Redact sensitive-looking headers, URL fields, and JSON keys; omit non-JSON bodies. Limit stored JSON body text to 64 KiB. Retain operation tags and summaries from the tested schema to group the report by entity; use the first tag, with a resource-path fallback for untagged operations and older databases. Keep the report and database under the consuming project's evidence access controls because redaction cannot guarantee removal of every secret.

Generate compact operation/phase summaries, paged case indexes, and full case evidence separately. Stream SQLite rows into gzip-compressed JSON chunks, targeting 1 MiB uncompressed (larger individual records remain intact). Wrap payloads in classic JavaScript registration files so browsers can load them on demand from `file://`; plain JSON fetches would require a server, while an in-browser SQLite engine would add runtime complexity. Native browser decompression avoids a new library. Keep all cases, checks, and sanitized exchanges.

Use summaries for navigation, filters, response coverage, and trends. Page operation and case rows, load full evidence only on case expansion, bound the decoded-payload cache, and discard inactive rendered details. Stage immutable data generations and publish the HTML last; a failed publication preserves the previous bundle.

## Consequences

Reports can compare the runs restored into one directory without a server or network dependencies. Users must copy or archive the HTML and companion directory together and use a browser supporting native gzip decompression (current Chrome and Edge). CI artifact patterns must include both; `artifacts/**` already does. CI jobs with ephemeral workspaces show only the current run until archived databases are restored and the report is rebuilt. The SQLite schema gains nullable HTTP detail columns; archived files retain their original schema and remain readable. Old single-file reports remain unchanged until regenerated. SQLite files require no migration and remain the durable source of evidence. Rebuilding replaces the viewer bundle; existing open tabs should be reloaded because obsolete data files are pruned.
