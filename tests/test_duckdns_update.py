# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Tests for the optional DuckDNS updater.

Every provider interaction is mocked: these tests never contact duckdns.org.
"""

import importlib.util
import io
import json
import logging
import pathlib
import urllib.error

import pytest

MODULE_PATH = (pathlib.Path(__file__).resolve().parents[1]
               / "contrib" / "ddns" / "duckdns_update.py")
_spec = importlib.util.spec_from_file_location("duckdns_update", MODULE_PATH)
duckdns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(duckdns)

TOKEN = "a7c4d0ad-114e-40ef-ba1d-d217904a50f2".replace("-", "")


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "duckdns_token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def fake_opener(body, *, error=None):
    """Return an urlopen replacement that records the URL it was given."""
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()
            return False

    def opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        if error is not None:
            raise error
        return Response(body.encode())

    opener.captured = captured
    return opener


# ----------------------------------------------------------------- hostnames
@pytest.mark.parametrize("value", [
    "my-ravencoin-node",
    "node1",
    "a",
    "my-node,second-node",
])
def test_valid_domains_are_accepted(value):
    assert duckdns.validate_domains(value) == value


@pytest.mark.parametrize("value, reason", [
    ("", "empty"),
    ("  ", "empty or padded"),
    ("my-ravencoin-node.duckdns.org", "only the subname"),
    ("node.example.org", "single label"),
    ("https://node", "single label"),
    ("-leading", "may not start"),
    ("trailing-", "may not start"),
    ("UPPER", "lowercase"),
    ("under_score", "lowercase"),
    ("x" * 64, "1-63 characters"),
])
def test_invalid_domains_are_rejected(value, reason):
    with pytest.raises(duckdns.DuckDnsError):
        duckdns.validate_domains(value)


# --------------------------------------------------------------------- token
def test_missing_token_file_is_reported(tmp_path):
    with pytest.raises(duckdns.DuckDnsError, match="does not exist"):
        duckdns.read_token(tmp_path / "absent")


def test_empty_token_file_is_reported(tmp_path):
    path = tmp_path / "duckdns_token"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(duckdns.DuckDnsError, match="is empty"):
        duckdns.read_token(path)


def test_implausible_token_is_reported(tmp_path):
    path = tmp_path / "duckdns_token"
    path.write_text("short", encoding="utf-8")
    with pytest.raises(duckdns.DuckDnsError, match="plausible"):
        duckdns.read_token(path)


def test_world_readable_token_warns_but_works(tmp_path, caplog):
    path = tmp_path / "duckdns_token"
    path.write_text(TOKEN, encoding="utf-8")
    path.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        assert duckdns.read_token(path) == TOKEN
    assert "world accessible" in caplog.text
    assert TOKEN not in caplog.text


# ------------------------------------------------------------------ updating
def test_ip_changed_reports_updated(token_file):
    opener = fake_opener("OK\n203.0.113.7\n\nUPDATED\n")
    outcome = duckdns.update("my-ravencoin-node", token_file, "off", opener=opener)
    assert outcome == {"result": "OK", "ipv4": "203.0.113.7", "ipv6": "",
                       "status": "UPDATED"}


def test_ip_unchanged_reports_nochange(token_file):
    opener = fake_opener("OK\n203.0.113.7\n\nNOCHANGE\n")
    outcome = duckdns.update("my-ravencoin-node", token_file, "off", opener=opener)
    assert outcome["status"] == "NOCHANGE"


def test_ipv4_is_left_for_duckdns_to_detect(token_file):
    opener = fake_opener("OK\n203.0.113.7\n\nUPDATED\n")
    duckdns.update("my-ravencoin-node", token_file, "off", opener=opener)
    assert "ip=&" in opener.captured["url"] or opener.captured["url"].endswith("ip=")
    assert "203.0.113" not in opener.captured["url"]


def test_explicit_ipv6_is_sent(token_file):
    opener = fake_opener("OK\n203.0.113.7\n2001:db8::5\nUPDATED\n")
    outcome = duckdns.update("my-ravencoin-node", token_file, "2001:db8::5",
                             opener=opener)
    assert "ipv6=2001%3Adb8%3A%3A5" in opener.captured["url"]
    assert outcome["ipv6"] == "2001:db8::5"


def test_invalid_explicit_ipv6_is_rejected(token_file):
    with pytest.raises(duckdns.DuckDnsError, match="not a valid IPv6"):
        duckdns.update("my-ravencoin-node", token_file, "not-an-address",
                       opener=fake_opener("OK\n"))


def test_auto_ipv6_uses_host_address(token_file, monkeypatch):
    monkeypatch.setattr(duckdns, "detect_global_ipv6", lambda: "2001:db8::9")
    opener = fake_opener("OK\n203.0.113.7\n2001:db8::9\nUPDATED\n")
    duckdns.update("my-ravencoin-node", token_file, "auto", opener=opener)
    assert "ipv6=2001%3Adb8%3A%3A9" in opener.captured["url"]


def test_auto_ipv6_absent_still_updates_ipv4(token_file, monkeypatch):
    monkeypatch.setattr(duckdns, "detect_global_ipv6", lambda: None)
    opener = fake_opener("OK\n203.0.113.7\n\nUPDATED\n")
    duckdns.update("my-ravencoin-node", token_file, "auto", opener=opener)
    assert "ipv6=" not in opener.captured["url"]


# ----------------------------------------------------------------- failures
def test_provider_rejection_is_an_error(token_file):
    with pytest.raises(duckdns.DuckDnsError, match="rejected the update"):
        duckdns.update("my-ravencoin-node", token_file, "off",
                       opener=fake_opener("KO\n"))


def test_unrecognized_response_is_an_error(token_file):
    with pytest.raises(duckdns.DuckDnsError, match="unrecognized"):
        duckdns.update("my-ravencoin-node", token_file, "off",
                       opener=fake_opener("<html>maintenance</html>"))


def test_temporary_network_failure_is_an_error(token_file):
    opener = fake_opener("", error=urllib.error.URLError("Name resolution failed"))
    with pytest.raises(duckdns.DuckDnsError, match="unreachable"):
        duckdns.update("my-ravencoin-node", token_file, "off", opener=opener)


def test_http_error_is_an_error(token_file):
    failure = urllib.error.HTTPError("https://www.duckdns.org/update", 503,
                                     "Service Unavailable", {}, None)
    with pytest.raises(duckdns.DuckDnsError, match="HTTP 503"):
        duckdns.update("my-ravencoin-node", token_file, "off",
                       opener=fake_opener("", error=failure))


# ---------------------------------------------------------- token redaction
def test_token_never_appears_in_logs(token_file, caplog):
    opener = fake_opener("OK\n203.0.113.7\n\nUPDATED\n")
    with caplog.at_level(logging.DEBUG):
        duckdns.update("my-ravencoin-node", token_file, "off", opener=opener)
    assert TOKEN not in caplog.text
    assert "duckdns.org/update?" not in caplog.text


def test_token_is_redacted_from_error_text(token_file):
    failure = urllib.error.URLError(f"failed for token={TOKEN}")
    with pytest.raises(duckdns.DuckDnsError) as caught:
        duckdns.update("my-ravencoin-node", token_file, "off",
                       opener=fake_opener("", error=failure))
    assert TOKEN not in str(caught.value)
    assert duckdns.REDACTED in str(caught.value)


def test_state_file_records_outcome_without_token(token_file, tmp_path):
    state = tmp_path / "state" / "duckdns.json"
    opener = fake_opener("OK\n203.0.113.7\n\nUPDATED\n")
    duckdns.update("my-ravencoin-node", token_file, "off", state_file=state,
                   opener=opener)
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["result"] == "OK"
    assert payload["status"] == "UPDATED"
    assert TOKEN not in state.read_text(encoding="utf-8")


def test_state_file_records_failure_without_token(token_file, tmp_path):
    state = tmp_path / "duckdns.json"
    failure = urllib.error.URLError(f"token={TOKEN} refused")
    with pytest.raises(duckdns.DuckDnsError):
        duckdns.update("my-ravencoin-node", token_file, "off", state_file=state,
                       opener=fake_opener("", error=failure))
    text = state.read_text(encoding="utf-8")
    assert json.loads(text)["result"] == "ERROR"
    assert TOKEN not in text


# ------------------------------------------------------------ command line
def test_missing_domain_exits_non_zero():
    assert duckdns.main(["--domain", "", "--token-file", "/nonexistent"]) == 2


def test_failure_exits_non_zero(tmp_path):
    assert duckdns.main(["--domain", "my-node",
                         "--token-file", str(tmp_path / "absent")]) == 1


def test_https_endpoint_is_used():
    assert duckdns.UPDATE_ENDPOINT.startswith("https://")
