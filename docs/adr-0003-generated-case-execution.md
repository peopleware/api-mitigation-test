# ADR 0003: Classify generated cases by recorded execution evidence

Status: Accepted

Schemathesis can retain generated cases without HTTP interactions or checks. Classify these cases as **Not executed**, retain them for review, and exclude them from passing and tested-operation credit and the evidence-completeness gate; partial execution evidence remains **Incomplete evidence** and blocks passing. This deliberately accepts that wholly missing execution records are indistinguishable from non-execution, instead of requiring lifecycle proof unavailable in existing events.

Record generated, executed, and unexecuted counts separately. Authentication probes verified through their parent retain their existing behavior, and run errors, incomplete runs, zero tested operations, and coverage thresholds remain independent gates. Store execution status in new databases so even interactions without usable request or response details retain their classification. Rebuilt reports preserve archived verdicts; archives without HTTP detail columns cannot safely infer non-execution.
