"""AI-driven test-data generation.

This package is fully self-contained and has no dependency on Appium/Selenium,
so it can be used to turn screen text into styled responses from any context.
"""

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.ai.settings import AISettings, get_ai_settings

__all__ = ["AIAgent", "AISettings", "get_ai_settings"]
