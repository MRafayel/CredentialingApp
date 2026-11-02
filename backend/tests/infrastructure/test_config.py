from pathlib import Path

import pytest
from pydantic import ValidationError

from credentialing.infrastructure.config import DEFAULT_USER_AGENT, Settings


def test_user_agent_defaults_to_a_standard_browser_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CREDENTIALING_USER_AGENT", raising=False)

    settings = Settings(_env_file=None)

    assert settings.user_agent == DEFAULT_USER_AGENT
    assert settings.user_agent.startswith("Mozilla/5.0 ")
    assert "Chrome/" in settings.user_agent


def test_user_agent_is_overridden_by_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIALING_USER_AGENT", "X")

    settings = Settings(_env_file=None)

    assert settings.user_agent == "X"


@pytest.mark.parametrize("value", ["", "   "])
def test_user_agent_must_not_be_empty(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, user_agent=value)


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("0", True), ("", False)])
def test_no_color_follows_the_standard_variable(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("NO_COLOR", value)

    assert Settings(_env_file=None).no_color is expected


def test_colour_is_allowed_without_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    assert Settings(_env_file=None).no_color is False


def test_the_browser_fallback_is_on_with_chrome_and_a_profile_under_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("FALLBACK_ENABLED", "CHANNEL", "PROFILE_DIR"):
        monkeypatch.delenv(f"CREDENTIALING_BROWSER_{name}", raising=False)

    settings = Settings(_env_file=None)

    assert settings.browser_fallback_enabled is True
    assert settings.browser_channel == "chrome"
    assert settings.browser_profile_dir == Path("data/browser-profile")


def test_the_browser_fallback_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIALING_BROWSER_FALLBACK_ENABLED", "false")

    assert Settings(_env_file=None).browser_fallback_enabled is False
