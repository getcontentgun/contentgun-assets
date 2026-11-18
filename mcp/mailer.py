import asyncio
import smtplib
import ssl
import sys
from email.message import EmailMessage
from typing import Protocol, TextIO

from remote_config import RemoteConfig, SmtpSettings

TIMEOUT_S = 20


class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class SmtpMailer:
    def __init__(self, settings: SmtpSettings):
        self._settings = settings

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        settings = self._settings
        # smtplib falls back to an unverified context unless it is handed this one.
        context = ssl.create_default_context()
        if settings.security == "tls":
            client = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=TIMEOUT_S, context=context
            )
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_S)
        with client:
            if settings.security == "starttls":
                client.starttls(context=context)
            if settings.user is not None and settings.password is not None:
                client.login(settings.user, settings.password)
            client.send_message(message)


class ConsoleMailer:
    """Writes each mail to stderr instead of sending it, for development only."""

    def __init__(self, stream: TextIO | None = None):
        self._stream = stream

    async def send(self, message: EmailMessage) -> None:
        stream = self._stream or sys.stderr
        print(
            f"\n----- mail -----\n{message.as_string()}----- end -----\n", file=stream, flush=True
        )


def mailer_for(config: RemoteConfig) -> Mailer:
    return ConsoleMailer() if config.smtp is None else SmtpMailer(config.smtp)
