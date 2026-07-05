"""Mobile E2E testing framework.

A lightweight, typed framework for end-to-end testing of mobile UIs through
Appium. Provides session lifecycle management, per-session network (proxy)
configuration and a place to build UI workers (page objects) on top.
"""

__version__ = "0.1.0"

from mobile_e2e.core.proxy import ProxyConfig
from mobile_e2e.core.session_manager import SessionManager

__all__ = ["ProxyConfig", "SessionManager", "__version__"]
