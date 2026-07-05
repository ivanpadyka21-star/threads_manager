"""Test ProxyConfig -> Selenium Proxy object conversion (needs selenium)."""

from selenium.webdriver.common.proxy import ProxyType

from mobile_e2e.core.proxy import ProxyConfig


def test_as_selenium_proxy():
    proxy = ProxyConfig.from_string("10.0.0.1:8080:u:p").as_selenium_proxy()
    assert proxy.proxy_type == ProxyType.MANUAL
    assert proxy.http_proxy == "10.0.0.1:8080"
    assert proxy.ssl_proxy == "10.0.0.1:8080"
