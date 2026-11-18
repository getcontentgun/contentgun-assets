import logging
import os

import pytest

from accounts import Accounts, is_address, mask, read_tenants_file
from errors import ConfigError
from tests.conftest import key_hash, tenants_for, write_tenants_file
from tests.mcp_remote import rewrite_tenants

TENANTS = {
    "acme": {
        "api_keys": [key_hash("acme-key")],
        "emails": [" Team@Acme.example ", "ops@acme.example"],
    },
    "studio": {"api_keys": [key_hash("studio-key")], "emails": ["hello@studio.example"]},
    "solo": {"api_keys": [key_hash("solo-key")]},
}


def tenants_file(tmp_path, tenants=None):
    return write_tenants_file(tmp_path / "tenants.toml", TENANTS if tenants is None else tenants)


def test_the_service_and_the_server_read_a_tenants_file_alike(tmp_path):
    path = tenants_file(tmp_path)
    directory = tenants_for(tenants_file=path)
    expected = {address: tenant.name for tenant in directory.tenants for address in tenant.emails}
    assert read_tenants_file(path) == expected
    assert expected == {
        "team@acme.example": "acme",
        "ops@acme.example": "acme",
        "hello@studio.example": "studio",
    }


@pytest.mark.parametrize(
    ("tenants", "expected"),
    [
        ({"acme": {"emails": "team@acme.example"}}, "must list its emails as strings"),
        ({"acme": {"emails": ["nobody"]}}, "not an email address (entry 1)"),
        ({"acme": {"emails": ["a@b.example", "A@B.example"]}}, "lists an email address twice"),
        (
            {"acme": {"emails": ["a@b.example"]}, "studio": {"emails": ["a@b.example"]}},
            "shares an email address with tenant 'acme'",
        ),
        ({"Acme": {"emails": ["a@b.example"]}}, "The tenant name 'Acme'"),
    ],
)
def test_an_invalid_tenants_file_is_refused_without_naming_an_address(tmp_path, tenants, expected):
    path = tenants_file(tmp_path, tenants)
    with pytest.raises(ConfigError) as caught:
        read_tenants_file(path)
    assert expected in str(caught.value)
    assert str(caught.value).startswith(f"{path}: ")
    assert "a@b.example" not in str(caught.value)


def test_a_file_without_tenants_or_toml_is_refused(tmp_path):
    path = tmp_path / "tenants.toml"
    path.write_text("[other]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="declares no tenant"):
        read_tenants_file(path)
    path.write_text("[tenants.acme\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        read_tenants_file(path)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        Accounts.from_file(tmp_path / "nope.toml")


def test_the_short_form_maps_every_address_to_default():
    accounts = Accounts.from_addresses(["a@b.example", "c@d.example"])
    assert accounts.tenant_of("c@d.example") == "default"
    assert accounts.tenant_of("x@y.example") is None


def test_a_changed_file_applies_at_the_next_lookup(tmp_path):
    path = tenants_file(tmp_path)
    accounts = Accounts.from_file(path)
    assert accounts.tenant_of("hello@studio.example") == "studio"
    rewrite_tenants(path, {"acme": {"emails": ["hello@studio.example"]}})
    assert accounts.tenant_of("hello@studio.example") == "acme"
    assert accounts.tenant_of("team@acme.example") is None


def test_an_invalid_change_keeps_the_addresses_read_before(tmp_path, caplog):
    path = tenants_file(tmp_path)
    accounts = Accounts.from_file(path)
    stamp = path.stat().st_mtime_ns
    path.write_text("[tenants.acme\n", encoding="utf-8")
    os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
    with caplog.at_level(logging.WARNING):
        assert accounts.tenant_of("team@acme.example") == "acme"
        assert accounts.tenant_of("team@acme.example") == "acme"
    warnings = [record for record in caplog.records if "not taken" in record.getMessage()]
    assert len(warnings) == 1


def test_a_vanished_file_keeps_the_addresses_read_before(tmp_path):
    path = tenants_file(tmp_path)
    accounts = Accounts.from_file(path)
    path.unlink()
    assert accounts.tenant_of("team@acme.example") == "acme"


def test_an_address_the_sign_in_cannot_take_is_reported_masked(tmp_path, caplog):
    path = tenants_file(tmp_path, {"acme": {"emails": ["jürgen@acme.example"]}})
    with caplog.at_level(logging.WARNING):
        Accounts.from_file(path)
    lines = [record.getMessage() for record in caplog.records]
    assert any("j…@acme.example" in line and "acme" in line for line in lines)
    assert not any("jürgen" in line for line in lines)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("team@acme.example", True),
        ("first.last+tag@sub.acme.example", True),
        ("team@acme", False),
        ("team@@acme.example", False),
        ("team acme@acme.example", False),
        ("team@acme.example\r\nbcc: x@y.example", False),
        ("jürgen@acme.example", False),
        ("team@-acme.example", False),
        ("a" * 250 + "@b.example", False),
    ],
)
def test_a_typed_address_must_be_plain(value, expected):
    assert is_address(value) is expected


def test_an_address_is_masked_to_its_first_letter_and_domain():
    assert mask("team@acme.example") == "t…@acme.example"
