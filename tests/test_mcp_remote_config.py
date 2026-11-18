import time
from datetime import UTC, datetime

import pytest
from pydantic import AnyUrl

from app.tenants import MIN_INTERNAL_KEY_LENGTH as SERVICE_MIN_INTERNAL_KEY_LENGTH
from errors import ConfigError
from remote_config import (
    CLAUDE_CALLBACKS,
    CONSOLE_SENDER,
    MIN_INTERNAL_KEY_LENGTH,
    load_remote_config,
)

VALID = {
    "MCP_PUBLIC_URL": "https://mcp.contentgun.de",
    "MCP_SECRET": "s" * 40,
    "ASSETS_BASE_URL": "http://assets:8010",
    "INTERNAL_KEY": "k" * 40,
    "MCP_EMAILS": "Team@Acme.example, other@acme.example",
    "MCP_MAIL": "console",
}
SMTP = {
    "MCP_MAIL": "smtp",
    "SMTP_HOST": "smtp.example",
    "MAIL_FROM": "Contentgun <login@contentgun.de>",
}


def load(**changes):
    return load_remote_config({**VALID, **changes})


def refused(**changes) -> str:
    with pytest.raises(ConfigError) as caught:
        load(**changes)
    return str(caught.value)


def test_a_minimal_environment_gives_the_defaults():
    config = load()
    assert config.public_url == "https://mcp.contentgun.de"
    assert config.resource_url == "https://mcp.contentgun.de/mcp"
    assert config.origin == "https://mcp.contentgun.de"
    assert config.base_url == "http://assets:8010"
    assert config.emails == ("team@acme.example", "other@acme.example")
    assert config.tenants_file is None
    assert config.smtp is None
    assert config.mail_from == CONSOLE_SENDER
    assert config.access_minutes == 60
    assert config.timeout_s == 30.0
    assert config.sessions_after is None
    assert config.redirect_uris == CLAUDE_CALLBACKS


@pytest.mark.parametrize(
    "name", ["MCP_PUBLIC_URL", "MCP_SECRET", "ASSETS_BASE_URL", "INTERNAL_KEY"]
)
def test_a_missing_variable_is_named(name):
    assert refused(**{name: ""}) == f"{name} is not set."


def test_the_public_url_loses_its_trailing_slash_and_the_case_of_its_host():
    assert load(MCP_PUBLIC_URL="https://MCP.Contentgun.de/").public_url == (
        "https://mcp.contentgun.de"
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://mcp.contentgun.de",
        "https://mcp.contentgun.de/mcp",
        "https://mcp.contentgun.de?x=1",
        "https://mcp.contentgun.de#top",
        "https://user:secret@mcp.contentgun.de",
        "ftp://mcp.contentgun.de",
        "mcp.contentgun.de",
    ],
)
def test_a_public_url_that_is_no_https_origin_stops_the_start(value):
    assert "MCP_PUBLIC_URL must be the https:// address" in refused(MCP_PUBLIC_URL=value)


@pytest.mark.parametrize("value", ["http://localhost:8020", "http://127.0.0.1:8020"])
def test_plain_http_works_on_loopback(value):
    config = load(MCP_PUBLIC_URL=value)
    assert config.public_url == value
    assert config.is_loopback


def test_the_secret_needs_32_characters_and_a_value_of_its_own():
    assert "at least 32" in refused(MCP_SECRET="s" * 31)
    assert "must not be INTERNAL_KEY" in refused(MCP_SECRET="k" * 40)


def test_the_internal_key_needs_the_length_the_service_demands():
    assert MIN_INTERNAL_KEY_LENGTH == SERVICE_MIN_INTERNAL_KEY_LENGTH
    assert "at least 32" in refused(INTERNAL_KEY="k" * 31)


def test_the_base_url_must_be_http():
    assert "ASSETS_BASE_URL must be an http://" in refused(ASSETS_BASE_URL="assets:8010")


@pytest.mark.parametrize("value", ["0", "-1", "abc", "inf"])
def test_a_bad_timeout_stops_the_start(value):
    assert "ASSETS_TIMEOUT_S" in refused(ASSETS_TIMEOUT_S=value)


def test_exactly_one_source_of_addresses_is_set(tmp_path):
    tenants = tmp_path / "tenants.toml"
    tenants.write_text("", encoding="utf-8")
    assert "exactly one of TENANTS_FILE" in refused(MCP_EMAILS="")
    assert "exactly one of TENANTS_FILE" in refused(TENANTS_FILE=str(tenants))


def test_a_relative_tenants_file_counts_from_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / "tenants.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = load(MCP_EMAILS="", TENANTS_FILE="tenants.toml")
    assert config.tenants_file == (tmp_path / "tenants.toml").resolve()
    assert config.emails == ()


def test_a_tenants_file_that_does_not_exist_stops_the_start(tmp_path):
    message = refused(MCP_EMAILS="", TENANTS_FILE=str(tmp_path / "nope.toml"))
    assert "TENANTS_FILE names" in message


def test_the_addresses_are_checked_and_collapse_when_doubled():
    assert load(MCP_EMAILS="a@b.example,A@B.example,,").emails == ("a@b.example",)
    assert "not a plain email address" in refused(MCP_EMAILS="a@b.example, nobody")
    assert "lists no address" in refused(MCP_EMAILS=" , ")


def test_the_mail_mode_is_smtp_or_console():
    assert "MCP_MAIL must be smtp or console" in refused(MCP_MAIL="pigeon")


def test_smtp_is_the_default_and_needs_a_host():
    message = refused(MCP_MAIL="")
    assert message.startswith("SMTP_HOST is not set.")
    assert "MCP_MAIL=console" in message


def test_console_mail_next_to_an_smtp_host_stops_the_start():
    assert "SMTP_HOST is set" in refused(SMTP_HOST="smtp.example")


def test_smtp_takes_starttls_on_587_by_default():
    config = load(**SMTP)
    assert config.smtp is not None
    assert (config.smtp.host, config.smtp.port, config.smtp.security) == (
        "smtp.example",
        587,
        "starttls",
    )
    assert config.smtp.user is None
    assert config.mail_from == "Contentgun <login@contentgun.de>"


def test_smtp_with_tls_takes_465_and_an_explicit_port_wins():
    assert load(**SMTP, SMTP_SECURITY="TLS").smtp.port == 465
    assert load(**SMTP, SMTP_SECURITY="tls", SMTP_PORT="2465").smtp.port == 2465


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"SMTP_SECURITY": "none"}, "SMTP_SECURITY must be starttls or tls."),
        ({"SMTP_PORT": "abc"}, "SMTP_PORT must be a port number."),
        ({"SMTP_PORT": "70000"}, "SMTP_PORT must be a port number."),
        ({"SMTP_USER": "login"}, "SMTP_USER and SMTP_PASSWORD go together"),
        ({"SMTP_PASSWORD": "secret"}, "SMTP_USER and SMTP_PASSWORD go together"),
        ({"MAIL_FROM": ""}, "MAIL_FROM is not set."),
        ({"MAIL_FROM": "Contentgun"}, "MAIL_FROM must be a sender address"),
        ({"MAIL_FROM": "a@b.example\nBcc: c@d.example"}, "MAIL_FROM must be a sender address"),
    ],
)
def test_bad_smtp_settings_stop_the_start(changes, expected):
    assert expected in refused(**(SMTP | changes))


def test_smtp_with_login():
    config = load(**SMTP, SMTP_USER="login", SMTP_PASSWORD="secret")
    assert (config.smtp.user, config.smtp.password) == ("login", "secret")


def test_sessions_after_is_a_moment_with_a_time_zone_in_the_past():
    assert load(MCP_SESSIONS_AFTER="2026-01-01T00:00:00Z").sessions_after == int(
        datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    )
    assert "with a time zone" in refused(MCP_SESSIONS_AFTER="2026-01-01T00:00:00")
    assert "with a time zone" in refused(MCP_SESSIONS_AFTER="yesterday")
    future = datetime.fromtimestamp(time.time() + 3600, UTC).isoformat()
    assert "lies in the future" in refused(MCP_SESSIONS_AFTER=future)


def test_the_access_minutes_lie_between_one_minute_and_one_day():
    assert load(MCP_ACCESS_MINUTES="3").access_minutes == 3
    for value in ("0", "1441"):
        assert "between 1 and 1440" in refused(MCP_ACCESS_MINUTES=value)
    assert "whole number of minutes" in refused(MCP_ACCESS_MINUTES="1.5")


def test_further_redirect_uris_come_after_the_ones_of_claude():
    config = load(
        MCP_REDIRECT_URIS=" https://Example.COM/callback , , https://claude.ai/api/mcp/auth_callback"
    )
    assert config.redirect_uris == (*CLAUDE_CALLBACKS, "https://example.com/callback")


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com/callback",
        "https://example.com/callback?x=1",
        "https://example.com/#x",
        "nope",
    ],
)
def test_a_redirect_uri_that_is_no_plain_https_address_stops_the_start(value):
    assert "MCP_REDIRECT_URIS takes https://" in refused(MCP_REDIRECT_URIS=value)


def test_claude_s_callbacks_are_written_as_the_sdk_compares_them():
    for uri in CLAUDE_CALLBACKS:
        assert str(AnyUrl(uri)) == uri


def test_the_links_of_the_login_page_must_be_http():
    config = load(MCP_IMPRINT_URL="https://contentgun.de/impressum")
    assert config.imprint_url == "https://contentgun.de/impressum"
    assert config.privacy_url is None
    assert "MCP_PRIVACY_URL must be an http://" in refused(MCP_PRIVACY_URL="contentgun.de")
