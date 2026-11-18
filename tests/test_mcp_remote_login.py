import logging
import re

import pytest

import login
from tests.mcp_remote import (
    ADDRESS,
    CALLBACK,
    PUBLIC,
    STATE,
    authorize,
    callback_of,
    pkce,
    register,
    remote_config,
    running,
    start,
    submit_address,
    submit_code,
    txn_of,
)

GERMAN = {"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
OTHER = "other@acme.example"


async def test_the_login_page_names_claude_and_asks_for_the_address():
    async with running() as remote:
        _, _, txn = await start(remote)
        response = await remote.client.get("/login", params={"txn": txn})
    assert response.status_code == 200
    assert "Claude (claude.ai) wants to connect to Contentgun" in response.text
    assert 'name="email"' in response.text
    assert '<html lang="en">' in response.text


async def test_an_unknown_or_expired_sign_in_is_gone():
    async with running() as remote:
        _, _, txn = await start(remote)
        unknown = await remote.client.get("/login", params={"txn": "nope"})
        remote.clock.advance(15 * 60)
        expired = await remote.client.get("/login", params={"txn": txn})
        posted = await submit_address(remote.client, txn)
    assert unknown.status_code == expired.status_code == posted.status_code == 410
    assert "Start the connection in Claude again" in expired.text
    assert remote.outbox.messages == []


async def test_every_login_page_forbids_framing_scripts_and_caching():
    async with running() as remote:
        _, _, txn = await start(remote)
        pages = [
            await remote.client.get("/login", params={"txn": txn}),
            await submit_address(remote.client, txn),
            await submit_code(remote.client, txn, "00000000"),
            await remote.client.get("/login", params={"txn": "nope"}),
        ]
    for page in pages:
        policy = page.headers["content-security-policy"]
        assert "default-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["referrer-policy"] == "no-referrer"
        assert "<script" not in page.text
    assert "form-action 'self' https://claude.ai;" in pages[1].headers["content-security-policy"]
    assert "form-action 'self';" in pages[3].headers["content-security-policy"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("de-DE,de;q=0.9,en;q=0.8", "de"),
        ("en-US,en;q=0.9,de;q=0.8", "en"),
        ("de;q=0.5, en;q=0.8", "en"),
        ("fr-FR,fr;q=0.9,de;q=0.7", "de"),
        ("fr-FR", "en"),
        ("", "en"),
        ("de;q=x", "en"),
    ],
)
async def test_the_language_follows_the_browser(header, expected):
    async with running() as remote:
        _, _, txn = await start(remote)
        response = await remote.client.get(
            "/login", params={"txn": txn}, headers={"Accept-Language": header}
        )
    assert f'<html lang="{expected}">' in response.text


async def test_a_known_address_gets_one_mail_with_the_code_in_the_language_of_the_page():
    async with running() as remote:
        _, _, txn = await start(remote)
        page = await submit_address(remote.client, txn, "  Team@ACME.example ", **GERMAN)
    assert page.status_code == 200
    assert '<html lang="de">' in page.text
    reference = re.search(r"Kennung dieser Anmeldung: ([A-Z2-9]{4})", page.text).group(1)
    [message] = remote.outbox.messages
    body = message.get_content()
    assert message["To"] == ADDRESS
    assert message["Subject"] == "Ihr Anmeldecode für Contentgun"
    assert not re.search(r"\d", message["Subject"])
    assert message["Auto-Submitted"] == "auto-generated"
    assert "Claude (claude.ai) möchte sich mit Contentgun verbinden" in body
    assert re.search(r"Ihr Code: \d{4} \d{4}", body)
    assert f"Kennung der Anmeldung: {reference}" in body
    assert re.search(r"gilt bis \d\d:\d\d UTC", body)
    assert "Geben Sie den Code niemals weiter" in body


async def test_known_unknown_and_held_back_addresses_get_the_same_page():
    async with running() as remote:
        pages = []
        for address in (ADDRESS, "nobody@acme.example"):
            _, _, txn = await start(remote)
            response = await submit_address(remote.client, txn, address)
            pages.append((response.status_code, response.text, txn, address))
        for _ in range(3):
            _, _, txn = await start(remote)
            response = await submit_address(remote.client, txn)
        pages.append((response.status_code, response.text, txn, ADDRESS))
    assert len(remote.outbox.messages) == 3

    def neutral(text, txn, address):
        text = text.replace(txn, "TXN").replace(address, "ADDRESS")
        return re.sub(r"(Reference of this sign-in: )[A-Z2-9]{4}", r"\1REF", text)

    shapes = {(status, neutral(text, txn, address)) for status, text, txn, address in pages}
    assert len(shapes) == 1


async def test_an_address_that_is_none_is_refused_on_the_page():
    async with running() as remote:
        _, _, txn = await start(remote)
        response = await submit_address(remote.client, txn, "nobody")
    assert response.status_code == 400
    assert "This is not a valid email address." in response.text
    assert remote.outbox.messages == []


async def test_the_right_code_returns_to_claude_with_code_and_state():
    async with running() as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn)
        code = remote.outbox.code()
        response = await submit_code(remote.client, txn, f"{code[:4]} {code[4:]}")
        again = await submit_code(remote.client, txn, code)
    callback = callback_of(response)
    assert callback["state"] == STATE
    assert response.headers["location"].startswith(f"{CALLBACK}?")
    assert again.status_code == 410


async def test_the_code_may_be_typed_with_a_hyphen():
    async with running() as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn)
        code = remote.outbox.code()
        response = await submit_code(remote.client, txn, f"{code[:4]}-{code[4:]}")
    assert response.status_code == 303


async def test_five_wrong_codes_lock_the_sign_in():
    async with running() as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn, **GERMAN)
        code = remote.outbox.code()
        wrong = "00000000" if code != "00000000" else "11111111"
        answers = [await submit_code(remote.client, txn, wrong, **GERMAN) for _ in range(5)]
        late = await submit_code(remote.client, txn, code)
    assert [answer.status_code for answer in answers] == [400, 400, 400, 400, 429]
    assert "Noch 4 Versuche." in answers[0].text
    assert "Noch 1 Versuch." in answers[3].text
    assert "Zu viele Versuche" in answers[4].text
    assert late.status_code == 410


async def test_another_address_does_not_reset_the_attempts():
    async with running() as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn)
        for _ in range(3):
            await submit_code(remote.client, txn, "--------")
        await submit_address(remote.client, txn, OTHER)
        await submit_code(remote.client, txn, "--------")
        locked = await submit_code(remote.client, txn, remote.outbox.code())
    assert locked.status_code == 429


async def test_a_fourth_address_in_one_sign_in_locks_it():
    config = remote_config(emails=(ADDRESS, OTHER))
    async with running(config) as remote:
        _, _, txn = await start(remote)
        first = await submit_address(remote.client, txn)
        await submit_address(remote.client, txn, OTHER)
        third = await submit_address(remote.client, txn)
        fourth = await submit_address(remote.client, txn, OTHER)
    assert "Use another address" in first.text
    assert "Use another address" not in third.text
    assert fourth.status_code == 429


async def test_only_the_code_of_the_last_address_counts():
    config = remote_config(emails=(ADDRESS, OTHER))
    async with running(config) as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn)
        first = remote.outbox.code()
        await submit_address(remote.client, txn, OTHER)
        old = await submit_code(remote.client, txn, first)
        new = await submit_code(remote.client, txn, remote.outbox.code())
    assert old.status_code == 400
    assert new.status_code == 303


async def test_a_sign_in_without_a_mail_takes_no_code():
    async with running() as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn, "nobody@acme.example")
        answers = [
            await submit_code(remote.client, txn, value)
            for value in ("--------", "00000000", "", "é" * 8)
        ]
    assert [answer.status_code for answer in answers] == [400, 400, 400, 400]


async def test_the_code_of_one_sign_in_does_not_open_another():
    async with running() as remote:
        _, _, first = await start(remote)
        _, _, second = await start(remote)
        await submit_address(remote.client, first)
        await submit_address(remote.client, second)
        response = await submit_code(remote.client, second, remote.outbox.code(0))
    assert response.status_code == 400


async def test_a_code_before_an_address_shows_the_address_form():
    async with running() as remote:
        _, _, txn = await start(remote)
        response = await submit_code(remote.client, txn, "12345678")
    assert response.status_code == 200
    assert 'name="email"' in response.text


async def test_the_code_expires_with_the_sign_in():
    async with running() as remote:
        _, _, txn = await start(remote)
        remote.clock.advance(10 * 60)
        await submit_address(remote.client, txn)
        remote.clock.advance(5 * 60)
        response = await submit_code(remote.client, txn, remote.outbox.code())
    assert response.status_code == 410


async def ask_for_a_code(remote) -> None:
    _, _, txn = await start(remote)
    await submit_address(remote.client, txn)


async def test_an_address_gets_three_mails_in_15_minutes():
    async with running() as remote:
        for _ in range(4):
            await ask_for_a_code(remote)
        assert len(remote.outbox.messages) == 3
        remote.clock.advance(15 * 60)
        await ask_for_a_code(remote)
    assert len(remote.outbox.messages) == 4


async def test_an_address_gets_ten_mails_a_day():
    async with running() as remote:
        first = remote.clock.now
        for _ in range(12):
            await ask_for_a_code(remote)
            remote.clock.advance(5 * 60)
        assert len(remote.outbox.messages) == 10
        remote.clock.now = first + 24 * 3600 - 1
        await ask_for_a_code(remote)
        assert len(remote.outbox.messages) == 10
        remote.clock.now = first + 24 * 3600 + 1
        await ask_for_a_code(remote)
    assert len(remote.outbox.messages) == 11


async def test_all_addresses_together_get_at_most_30_mails_a_minute():
    addresses = tuple(f"person{number}@acme.example" for number in range(31))
    async with running(remote_config(emails=addresses)) as remote:
        for address in addresses:
            _, _, txn = await start(remote)
            await submit_address(remote.client, txn, address)
        assert len(remote.outbox.messages) == 30
        remote.clock.advance(61)
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn, addresses[-1])
    assert len(remote.outbox.messages) == 31


async def test_a_failed_mail_changes_nothing_on_the_page_and_is_logged_plainly(caplog):
    async with running() as remote:
        _, _, txn = await start(remote)
        sent = await submit_address(remote.client, txn)
        remote.outbox.failing = True
        _, _, txn = await start(remote)
        with caplog.at_level(logging.ERROR):
            failed = await submit_address(remote.client, txn)
    assert failed.status_code == sent.status_code == 200
    [record] = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert record.getMessage() == (
        "Could not send the sign-in code for tenant default: ConnectionRefusedError"
    )


async def test_when_the_sign_ins_are_full_the_oldest_without_a_mail_goes(monkeypatch):
    monkeypatch.setattr(login, "MAX_LOGINS", 3)
    async with running() as remote:
        _, _, quiet = await start(remote)
        _, _, first = await start(remote)
        _, _, second = await start(remote)
        await submit_address(remote.client, first)
        await submit_address(remote.client, second)
        _, _, third = await start(remote)
        gone = await remote.client.get("/login", params={"txn": quiet})
        await submit_address(remote.client, third)
        registered = (await register(remote.client)).json()
        full = await authorize(remote.client, registered["client_id"], pkce()[1])
        kept = await remote.client.get("/login", params={"txn": first})
    assert gone.status_code == 410
    assert "error=temporarily_unavailable" in full.headers["location"]
    assert kept.status_code == 200


@pytest.mark.parametrize(
    ("content", "headers", "status"),
    [
        (b"txn=x&email=" + b"a" * 9000, {"Content-Type": "application/x-www-form-urlencoded"}, 413),
        (b'{"txn": "x"}', {"Content-Type": "application/json"}, 400),
        (b"txn=x&email=\xff", {"Content-Type": "application/x-www-form-urlencoded"}, 400),
        (
            "&".join(f"f{number}=x" for number in range(20)).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
            400,
        ),
    ],
)
async def test_a_form_that_is_too_big_or_malformed_is_refused(content, headers, status):
    async with running() as remote:
        for path in ("/login", "/login/code"):
            response = await remote.client.post(path, content=content, headers=headers)
            assert response.status_code == status


async def test_a_chunked_form_without_a_length_is_cut_at_the_limit():
    async def chunks():
        yield b"txn=x&email="
        for _ in range(10):
            yield b"a" * 1000

    async with running() as remote:
        response = await remote.client.post(
            "/login",
            content=chunks(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 413


async def test_the_sign_in_ignores_a_forged_host():
    async with running() as remote:
        registered = (await register(remote.client)).json()
        response = await authorize(remote.client, registered["client_id"], pkce()[1])
        remote.client.headers["Host"] = "evil.example"
        forged = await authorize(remote.client, registered["client_id"], pkce()[1])
    assert response.headers["location"].startswith(f"{PUBLIC}/login?txn=")
    assert forged.headers["location"].startswith(f"{PUBLIC}/login?txn=")
    txn_of(forged)


async def test_the_footer_links_appear_when_configured():
    config = remote_config(
        imprint_url="https://contentgun.de/impressum",
        privacy_url="https://contentgun.de/datenschutz",
    )
    async with running(config) as remote:
        _, _, txn = await start(remote)
        german = await remote.client.get("/login", params={"txn": txn}, headers=GERMAN)
        plain = await remote.client.get("/login", params={"txn": "nope"})
    assert '<a href="https://contentgun.de/impressum">Impressum</a>' in german.text
    assert '<a href="https://contentgun.de/datenschutz">Privacy</a>' in plain.text


async def test_the_style_sheet_is_served():
    async with running() as remote:
        response = await remote.client.get("/login/style.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
