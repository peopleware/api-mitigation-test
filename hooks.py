import os

import schemathesis
import tracecov

tracecov.schemathesis.install()


@schemathesis.hook
def before_call(ctx, case, kwargs):
    token = os.environ.get("API_MITIGATION_BEARER_TOKEN")
    if token:
        case.headers = {**(case.headers or {}), "Authorization": f"Bearer {token}"}
