"""Tests for capability + options construction using real AppiumOptions."""

from mobile_e2e.config.capabilities import build_capabilities, build_options
from mobile_e2e.config.settings import AppiumSettings
from mobile_e2e.core.proxy import ProxyConfig


def _settings(**kw) -> AppiumSettings:
    base = dict(
        platform_name="Android",
        automation_name="UiAutomator2",
        device_name="emulator-5554",
    )
    base.update(kw)
    return AppiumSettings(**base)


def test_base_capabilities():
    caps = build_capabilities(_settings())
    assert caps["platformName"] == "Android"
    assert caps["appium:automationName"] == "UiAutomator2"
    assert caps["appium:deviceName"] == "emulator-5554"
    assert "proxy" not in caps


def test_optional_fields_included_when_set():
    caps = build_capabilities(
        _settings(platform_version="14", app_package="com.x", app_activity=".Main")
    )
    assert caps["appium:platformVersion"] == "14"
    assert caps["appium:appPackage"] == "com.x"
    assert caps["appium:appActivity"] == ".Main"


def test_proxy_merged_into_capabilities():
    proxy = ProxyConfig.from_string("10.0.0.1:8080:u:p")
    caps = build_capabilities(_settings(), proxy=proxy)
    assert caps["proxy"] == {
        "proxyType": "manual",
        "httpProxy": "10.0.0.1:8080",
        "sslProxy": "10.0.0.1:8080",
    }


def test_extra_overrides_defaults():
    caps = build_capabilities(_settings(), extra={"platformName": "iOS"})
    assert caps["platformName"] == "iOS"


def test_build_options_roundtrip():
    caps = build_capabilities(_settings())
    options = build_options(caps)
    loaded = options.to_capabilities()
    assert loaded["platformName"] == "Android"
    assert loaded["appium:automationName"] == "UiAutomator2"
