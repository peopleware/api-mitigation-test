import os

import schemathesis

if os.environ.get("API_MITIGATION_COVERAGE_ENABLED", "true") == "true":
    import tracecov

    tracecov.schemathesis.install()


@schemathesis.hook
def before_call(ctx, case, kwargs):
    token = os.environ.get("API_MITIGATION_BEARER_TOKEN")
    if token:
        case.headers = {**(case.headers or {}), "Authorization": f"Bearer {token}"}
