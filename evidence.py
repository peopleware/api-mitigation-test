"""Classification of generated cases from recorded execution evidence."""


def not_executed(case: dict) -> bool:
    """Missing execution records earn no passing or coverage credit."""
    if case.get("execution_status") is not None:
        return case["execution_status"] == "not_executed"
    return (not case.get("checks") and not case.get("verification") and
            case.get("response_status") is None and
            case.get("request") is None and case.get("response") is None)
