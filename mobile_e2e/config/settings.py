"""Typed settings for the E2E framework, loaded from env / ``.env``.

Uses ``pydantic-settings`` (already a project dependency) so configuration can
be injected from the environment inside Docker containers without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Platform = Literal["Android", "iOS"]


class AppiumSettings(BaseSettings):
    """Runtime configuration for Appium sessions.

    Every field can be overridden via an ``E2E_``-prefixed environment variable,
    e.g. ``E2E_SERVER_URL`` or ``E2E_PLATFORM_NAME``.
    """

    model_config = SettingsConfigDict(
        env_prefix="E2E_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Appium server -----------------------------------------------------
    server_url: str = Field(
        default="http://127.0.0.1:4723",
        description="Base URL of the Appium server (without the /wd/hub suffix).",
    )
    command_timeout: int = Field(
        default=120,
        ge=1,
        description="newCommandTimeout in seconds before Appium ends an idle session.",
    )
    startup_timeout: int = Field(
        default=180,
        ge=1,
        description="Max seconds to wait for a session to come up.",
    )

    # --- Device / platform -------------------------------------------------
    platform_name: Platform = Field(default="Android")
    automation_name: str = Field(default="UiAutomator2")
    device_name: str = Field(default="emulator-5554")
    platform_version: str | None = Field(default=None)
    app_package: str | None = Field(default=None)
    app_activity: str | None = Field(default=None)

    @property
    def remote_url(self) -> str:
        """The URL passed to the Appium ``Remote`` command executor."""
        return self.server_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> AppiumSettings:
    """Return a cached :class:`AppiumSettings` instance."""
    return AppiumSettings()
