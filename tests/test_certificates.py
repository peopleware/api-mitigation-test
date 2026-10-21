"""Keep the emSign incident's affected roots out of both Python CA bundles."""

import ssl

import certifi
import pip._vendor.certifi as pip_certifi
import pytest


@pytest.mark.parametrize("bundle", [certifi, pip_certifi], ids=["application", "pip"])
def test_ca_bundle_excludes_compromised_chains(bundle):
    # See https://emudhra.we-have-a-problem.com/ for the affected root chains.
    # Load only this bundle, without merging the host's system trust store.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=bundle.where())
    names = {
        value
        for certificate in context.get_ca_certs()
        for rdn in certificate["subject"]
        for key, value in rdn
        if key == "commonName"
    }
    assert names, "The CA bundle must not be empty"
    assert not names & {"emSign ECC Root CA - G3", "AAA Certificate Services"}
