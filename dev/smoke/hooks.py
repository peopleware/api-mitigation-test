"""Authenticate the disposable fixture without undoing negative-auth probes."""

import os

import schemathesis
import tracecov

tracecov.schemathesis.install()


@schemathesis.auth(refresh_interval=None)
class FixtureBearerAuth:
    def get(self, case, context):
        return os.environ["API_MITIGATION_BEARER_TOKEN"]

    def set(self, case, data, context):
        case.headers = {**(case.headers or {}), "Authorization": "Bearer " + data}
