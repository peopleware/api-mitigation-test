"""Guard against upstream regressions observed in real stateful API runs."""
import pytest
import requests
import schemathesis

from schemathesis.checks import CheckContext, not_a_server_error
from schemathesis.config import ChecksConfig
from schemathesis.core.transport import Response
from schemathesis.core.failures import ServerError
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata, ComponentInfo, GenerationInfo, ParameterLocation,
    PhaseInfo, StatefulPhaseData, TestPhase,
)
from schemathesis.specs.openapi.checks import AcceptedNegativeData, negative_data_rejection


@pytest.mark.parametrize("identifier,invalid", [("7095", False), ("not-an-integer", True), ("0", True)])
def test_stateful_numeric_path_checks_the_serialized_value(identifier, invalid):
    schema = schemathesis.openapi.from_dict({
        "openapi": "3.0.3", "info": {"title": "Numeric path regression", "version": "1"},
        "paths": {"/items/{id}": {"get": {
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "integer", "minimum": 1}}],
            "responses": {"200": {"description": "OK"}},
        }}},
    })
    # Stateful links can replace a negative integer with the string extracted
    # from Location while the generated case still carries negative metadata.
    case = schema["/items/{id}"]["get"].Case(path_parameters={"id": 0}, _meta=CaseMetadata(
        generation=GenerationInfo(time=0, mode=GenerationMode.NEGATIVE),
        components={ParameterLocation.PATH: ComponentInfo(mode=GenerationMode.NEGATIVE)},
        phase=PhaseInfo(TestPhase.STATEFUL, StatefulPhaseData("Positive test case", None, None, None)),
    ))
    case.path_parameters = {"id": identifier}
    assert case.meta.generation.mode.is_negative is invalid
    request = requests.Request("GET", "http://example.test/items/" + identifier).prepare()
    response = Response(status_code=200, headers={}, content=b"", request=request, elapsed=0, verify=True)
    context = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(),
                           transport_kwargs=None, response_checks=None)
    if invalid:
        with pytest.raises(AcceptedNegativeData):
            negative_data_rejection(context, response, case)
    else:
        negative_data_rejection(context, response, case)


@pytest.mark.parametrize("status", [503, 500, 502])
@pytest.mark.parametrize("outage", [False, True])
def test_outage_expectation_allows_only_the_explicit_server_status(status, outage):
    schema = schemathesis.openapi.from_dict({
        "openapi": "3.0.3", "info": {"title": "Dependency health", "version": "1"},
        "paths": {"/health": {"get": {"responses": {
            "200": {"description": "Healthy"}, "503": {"description": "Dependency unavailable"},
        }}}},
    })
    case = schema["/health"]["get"].Case()
    request = requests.Request("GET", "http://example.test/health").prepare()
    response = Response(status_code=status, headers={}, content=b"", request=request, elapsed=0, verify=True)
    config = ChecksConfig.from_dict({"not_a_server_error": {
        "expected-statuses": ["1xx", "2xx", "3xx", "4xx", "503"],
    }}) if outage else ChecksConfig()
    context = CheckContext(override=None, auth=None, headers=None, config=config,
                           transport_kwargs=None, response_checks=None)
    if outage and status == 503:
        not_a_server_error(context, response, case)
    else:
        with pytest.raises(ServerError):
            not_a_server_error(context, response, case)
