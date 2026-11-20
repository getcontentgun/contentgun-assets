import io
import smtplib
import ssl
from email.message import EmailMessage

import pytest

from mailer import ConsoleMailer, SmtpMailer, mailer_for
from remote_config import SmtpSettings
from tests.mcp_remote import ADDRESS, remote_config


class FakeSmtp:
    sessions: list["FakeSmtp"] = []
    starttls_offered = True

    def __init__(self, host, port, timeout=None, context=None):
        self.opened = (host, port, timeout)
        self.context = context
        self.steps: list[tuple] = []
        FakeSmtp.sessions.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.steps.append(("quit",))

    def starttls(self, context=None):
        if not FakeSmtp.starttls_offered:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        self.steps.append(("starttls", context))

    def login(self, user, password):
        self.steps.append(("login", user, password))

    def send_message(self, message):
        self.steps.append(("send", message["To"]))


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSmtp.sessions = []
    FakeSmtp.starttls_offered = True
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtp)
    return FakeSmtp


def message() -> EmailMessage:
    mail = EmailMessage()
    mail["To"] = ADDRESS
    mail["From"] = "ContentGun <login@contentgun.test>"
    mail["Subject"] = "Your ContentGun sign-in code"
    mail.set_content("Your code: 1234 5678\n")
    return mail


def settings(**changes) -> SmtpSettings:
    values = {
        "host": "smtp.example",
        "port": 587,
        "user": "login",
        "password": "secret",
        "security": "starttls",
    }
    return SmtpSettings(**(values | changes))


def verifies(context) -> bool:
    return (
        isinstance(context, ssl.SSLContext)
        and context.check_hostname
        and context.verify_mode == ssl.CERT_REQUIRED
    )


async def test_starttls_comes_first_with_a_verifying_context(fake_smtp):
    await SmtpMailer(settings()).send(message())
    [session] = fake_smtp.sessions
    assert session.opened == ("smtp.example", 587, 20)
    assert [step[0] for step in session.steps] == ["starttls", "login", "send", "quit"]
    assert verifies(session.steps[0][1])
    assert session.steps[2] == ("send", ADDRESS)


async def test_tls_opens_with_a_verifying_context_and_no_starttls(fake_smtp):
    await SmtpMailer(settings(security="tls", port=465)).send(message())
    [session] = fake_smtp.sessions
    assert verifies(session.context)
    assert [step[0] for step in session.steps] == ["login", "send", "quit"]


async def test_without_a_user_nothing_logs_in(fake_smtp):
    await SmtpMailer(settings(user=None, password=None)).send(message())
    assert [step[0] for step in fake_smtp.sessions[0].steps] == ["starttls", "send", "quit"]


async def test_a_server_without_starttls_gets_neither_login_nor_mail(fake_smtp):
    fake_smtp.starttls_offered = False
    with pytest.raises(smtplib.SMTPNotSupportedError):
        await SmtpMailer(settings()).send(message())
    assert [step[0] for step in fake_smtp.sessions[0].steps] == ["quit"]


async def test_the_console_writes_the_whole_mail():
    stream = io.StringIO()
    await ConsoleMailer(stream).send(message())
    assert f"To: {ADDRESS}" in stream.getvalue()
    assert "Your code: 1234 5678" in stream.getvalue()


def test_the_mailer_follows_the_settings():
    assert isinstance(mailer_for(remote_config()), ConsoleMailer)
    assert isinstance(mailer_for(remote_config(smtp=settings())), SmtpMailer)
