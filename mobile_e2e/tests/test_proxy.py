"""Unit tests for proxy parsing (no Appium server required)."""

import pytest

from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.core.proxy import ProxyConfig


def test_parse_with_auth():
    proxy = ProxyConfig.from_string("10.0.0.1:8080:user:secret")
    assert proxy.host == "10.0.0.1"
    assert proxy.port == 8080
    assert proxy.login == "user"
    assert proxy.password == "secret"
    assert proxy.has_auth is True
    assert proxy.url == "http://user:secret@10.0.0.1:8080"


def test_parse_without_auth():
    proxy = ProxyConfig.from_string("192.168.1.1:3128")
    assert proxy.has_auth is False
    assert proxy.url == "http://192.168.1.1:3128"
    assert proxy.host_port == "192.168.1.1:3128"


def test_credentials_are_percent_encoded():
    proxy = ProxyConfig.from_string("10.0.0.1:8080:us er:p@ss")
    assert proxy.url == "http://us%20er:p%40ss@10.0.0.1:8080"


def test_str_masks_password():
    proxy = ProxyConfig.from_string("10.0.0.1:8080:user:secret")
    assert "secret" not in str(proxy)
    assert "***" in str(proxy)


def test_capabilities_shape():
    proxy = ProxyConfig.from_string("10.0.0.1:8080")
    caps = proxy.as_capabilities()
    assert caps == {
        "proxyType": "manual",
        "httpProxy": "10.0.0.1:8080",
        "sslProxy": "10.0.0.1:8080",
    }


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "10.0.0.1",  # missing port
        "10.0.0.1:8080:user",  # 3 fields
        "10.0.0.1:notaport",  # non-numeric port
        "10.0.0.1:70000",  # port out of range
        ":8080:user:pass",  # missing host
        "10.0.0.1:8080:user:",  # empty password
    ],
)
def test_invalid_strings_raise(raw):
    with pytest.raises(ProxyParseError):
        ProxyConfig.from_string(raw)
