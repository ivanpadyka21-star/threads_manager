"""Build Appium ``AppiumOptions`` from settings + optional per-session proxy.

Kept separate from :class:`SessionManager` so capability construction can be
unit-tested and reused across different session strategies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from mobile_e2e.config.settings import AppiumSettings
from mobile_e2e.core.proxy import ProxyConfig

if TYPE_CHECKING:
    from appium.options.common import AppiumOptions


def build_capabilities(
    settings: AppiumSettings,
    proxy: Optional[ProxyConfig] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the raw W3C capabilities dictionary.

    Args:
        settings: The active Appium settings.
        proxy: Optional parsed proxy applied as ``proxy`` capabilities.
        extra: Optional caller-supplied capabilities that override the defaults.

    Returns:
        A capabilities dict ready to feed into :func:`build_options`.
    """
    caps: Dict[str, Any] = {
        "platformName": settings.platform_name,
        "appium:automationName": settings.automation_name,
        "appium:deviceName": settings.device_name,
        "appium:newCommandTimeout": settings.command_timeout,
    }

    if settings.platform_version:
        caps["appium:platformVersion"] = settings.platform_version
    if settings.app_package:
        caps["appium:appPackage"] = settings.app_package
    if settings.app_activity:
        caps["appium:appActivity"] = settings.app_activity

    if proxy is not None:
        caps["proxy"] = proxy.as_capabilities()

    if extra:
        caps.update(extra)

    return caps


def build_options(capabilities: Dict[str, Any]) -> "AppiumOptions":
    """Wrap a capabilities dict in an :class:`AppiumOptions` instance.

    Appium Python Client 3+ expects an ``*Options`` object rather than the
    deprecated ``desired_capabilities`` argument.
    """
    from appium.options.common import AppiumOptions

    options = AppiumOptions()
    options.load_capabilities(capabilities)
    return options
