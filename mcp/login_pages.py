import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from html import escape
from urllib.parse import parse_qs, urlencode

from mcp.server.mcpserver import MCPServer
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from accounts import is_address, mask, normalize
from login import MAX_ATTEMPTS, MAX_SUBMITS, Check, Login, Logins, Outgoing
from mailer import Mailer
from remote_config import RemoteConfig

logger = logging.getLogger(__name__)

LANGUAGES = ("de", "en")
MAX_FORM_BYTES = 8192
MAX_FORM_FIELDS = 10
FORM_TYPE = "application/x-www-form-urlencoded"
NO_STORE = {"Cache-Control": "no-store"}

TEXTS = {
    "de": {
        "sign_in": "Bei Contentgun anmelden",
        "intro": (
            "{client} möchte sich mit Contentgun verbinden. Danach kann Claude Karten mit den "
            "Vorlagen Ihres Teams rendern und Bilder dafür hochladen."
        ),
        "address": "E-Mail-Adresse",
        "address_hint": (
            "Die Adresse, die für Ihr Team bei Contentgun hinterlegt ist. Sie bekommen einen "
            "Code per Mail."
        ),
        "request": "Code anfordern",
        "not_an_address": "Das ist keine gültige E-Mail-Adresse.",
        "enter_code": "Code eingeben",
        "sent": (
            "Ist {address} bei Contentgun hinterlegt, kommt in einer Minute eine Mail mit einem "
            "Code."
        ),
        "reference": "Kennung dieser Anmeldung: {reference}. Dieselbe Kennung steht in der Mail.",
        "code": "Code aus der Mail",
        "confirm": "Anmelden",
        "wrong_one": "Der Code stimmt nicht. Noch 1 Versuch.",
        "wrong_many": "Der Code stimmt nicht. Noch {left} Versuche.",
        "other_address": "Andere Adresse verwenden",
        "expired_title": "Anmeldung abgelaufen",
        "expired": (
            "Diese Anmeldung ist abgelaufen oder unbekannt. Starten Sie die Verbindung in "
            "Claude neu."
        ),
        "locked_title": "Zu viele Versuche",
        "locked": "Diese Anmeldung ist gesperrt. Starten Sie die Verbindung in Claude neu.",
        "invalid_title": "Ungültige Anfrage",
        "invalid": (
            "Das Formular kam nicht vollständig an. Starten Sie die Verbindung in Claude neu."
        ),
        "imprint": "Impressum",
        "privacy": "Datenschutz",
        "mail_subject": "Ihr Anmeldecode für Contentgun",
        "mail_body": (
            "Guten Tag,\n\n"
            "{client} möchte sich mit Contentgun verbinden und hat dafür diese Adresse "
            "angegeben. Mit dem Code darf Claude danach Karten mit den Vorlagen Ihres Teams "
            "rendern und Bilder dafür hochladen.\n\n"
            "Ihr Code: {code}\n\n"
            "Kennung der Anmeldung: {reference}\n"
            "Der Code gilt bis {time} UTC und nur in dem Fenster, in dem die Anmeldung begonnen "
            "hat. Dort steht dieselbe Kennung.\n\n"
            "Geben Sie den Code niemals weiter, auch nicht an jemanden, der sich als "
            "Contentgun ausgibt. Haben Sie die Anmeldung nicht selbst begonnen, ignorieren Sie "
            "diese Mail.\n\n"
            "Contentgun\n"
        ),
    },
    "en": {
        "sign_in": "Sign in to Contentgun",
        "intro": (
            "{client} wants to connect to Contentgun. Claude can then render cards with your "
            "team's templates and upload images for them."
        ),
        "address": "Email address",
        "address_hint": (
            "The address your team has on file with Contentgun. You will get a code by mail."
        ),
        "request": "Send me a code",
        "not_an_address": "This is not a valid email address.",
        "enter_code": "Enter your code",
        "sent": (
            "If {address} is on file with Contentgun, a mail with a code arrives within a minute."
        ),
        "reference": "Reference of this sign-in: {reference}. The mail shows the same reference.",
        "code": "Code from the mail",
        "confirm": "Sign in",
        "wrong_one": "The code is not right. 1 attempt left.",
        "wrong_many": "The code is not right. {left} attempts left.",
        "other_address": "Use another address",
        "expired_title": "Sign-in expired",
        "expired": (
            "This sign-in has expired or is unknown. Start the connection in Claude again."
        ),
        "locked_title": "Too many attempts",
        "locked": "This sign-in is locked. Start the connection in Claude again.",
        "invalid_title": "Invalid request",
        "invalid": "The form did not arrive complete. Start the connection in Claude again.",
        "imprint": "Legal notice",
        "privacy": "Privacy",
        "mail_subject": "Your Contentgun sign-in code",
        "mail_body": (
            "Hello,\n\n"
            "{client} wants to connect to Contentgun and gave this address. With the code, "
            "Claude may then render cards with your team's templates and upload images for "
            "them.\n\n"
            "Your code: {code}\n\n"
            "Sign-in reference: {reference}\n"
            "The code works until {time} UTC, and only in the window where the sign-in began. "
            "That window shows the same reference.\n\n"
            "Never pass the code on, not even to someone who claims to be Contentgun. If you "
            "did not start this sign-in, ignore this mail.\n\n"
            "Contentgun\n"
        ),
    },
}

STYLE = """\
:root { color-scheme: light dark; --text: #1e2a36; --muted: #5b6770; --line: #c9d1d8;
  --accent: #a35f00; --background: #f6f4ef; --card: #ffffff; --error: #b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --text: #f3efe6; --muted: #a8b3bc; --line: #3a4652; --accent: #f2b33d;
    --background: #141c24; --card: #1e2a36; --error: #ff8a80; } }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: flex; flex-direction: column; align-items: center;
  justify-content: center; padding: 24px 16px; background: var(--background); color: var(--text);
  font: 17px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { width: 100%; max-width: 28rem; background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; padding: 28px 24px; }
.brand { margin: 0 0 8px; font-weight: 700; color: var(--accent); }
h1 { margin: 0 0 16px; font-size: 1.4rem; line-height: 1.25; }
label { display: block; margin: 16px 0 6px; font-weight: 600; }
input { width: 100%; font: inherit; padding: 10px 12px; border: 1px solid var(--line);
  border-radius: 8px; background: transparent; color: inherit; }
#code { font-variant-numeric: tabular-nums; letter-spacing: 0.15em; }
button { margin-top: 16px; width: 100%; font: inherit; font-weight: 600; padding: 10px 16px;
  border: 0; border-radius: 8px; background: var(--accent); color: #141c24; cursor: pointer; }
.hint, .reference, .other { color: var(--muted); font-size: 0.9rem; }
.error { color: var(--error); font-weight: 600; }
a { color: var(--accent); }
footer { margin-top: 16px; font-size: 0.85rem; color: var(--muted); }
footer a { color: inherit; margin: 0 8px; }
"""

LAYOUT = """\
<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Contentgun</title>
<link rel="stylesheet" href="/login/style.css">
</head>
<body>
<main>
<p class="brand">Contentgun</p>
<h1>{title}</h1>
{body}
</main>
{footer}
</body>
</html>
"""


class FormError(Exception):
    def __init__(self, status: int):
        self.status = status


def language_of(request: Request) -> str:
    """The first of de and en with the highest weight in Accept-Language, else en."""
    best, best_weight = "en", 0.0
    for part in request.headers.get("accept-language", "").split(","):
        tag, *parameters = part.strip().split(";")
        primary = tag.strip().lower().split("-")[0]
        weight = 1.0
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            if name.strip() == "q":
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
        if primary in LANGUAGES and weight > best_weight:
            best, best_weight = primary, weight
    return best


async def read_form(request: Request) -> dict[str, str]:
    """The fields of a small urlencoded form, read with a limit before anything is parsed."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != FORM_TYPE:
        raise FormError(400)
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_FORM_BYTES):
        raise FormError(413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FORM_BYTES:
            raise FormError(413)
    try:
        fields = parse_qs(
            body.decode("utf-8"), keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS
        )
    except (UnicodeDecodeError, ValueError):
        raise FormError(400) from None
    return {name: values[0] for name, values in fields.items()}


def code_mail(outgoing: Outgoing, language: str, sender: str) -> EmailMessage:
    texts = TEXTS[language]
    code = f"{outgoing.code[:4]} {outgoing.code[4:]}"
    message = EmailMessage()
    message["Subject"] = texts["mail_subject"]
    message["From"] = sender
    message["To"] = outgoing.address
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = make_msgid(domain="contentgun.invalid")
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(
        texts["mail_body"].format(
            client=outgoing.client_label,
            code=code,
            reference=outgoing.reference,
            time=datetime.fromtimestamp(outgoing.expires, UTC).strftime("%H:%M"),
        )
    )
    return message


class Pages:
    def __init__(self, config: RemoteConfig):
        self._links = [
            (key, url)
            for key, url in (("imprint", config.imprint_url), ("privacy", config.privacy_url))
            if url
        ]

    def _page(
        self,
        language: str,
        title_key: str,
        body: str,
        status: int = 200,
        login: Login | None = None,
        background: BackgroundTask | None = None,
    ) -> HTMLResponse:
        texts = TEXTS[language]
        title = escape(texts[title_key])
        footer = ""
        if self._links:
            links = "".join(
                f'<a href="{escape(url)}">{escape(texts[key])}</a>' for key, url in self._links
            )
            footer = f"<footer>{links}</footer>"
        # Chrome applies form-action to the redirect after a form post, so the callback of
        # this sign-in has to be listed next to 'self'.
        targets = "'self'" if login is None else f"'self' {login.callback_origin}"
        headers = NO_STORE | {
            "Content-Security-Policy": (
                f"default-src 'none'; style-src 'self'; form-action {targets}; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        html = LAYOUT.format(language=language, title=title, body=body, footer=footer)
        return HTMLResponse(html, status_code=status, headers=headers, background=background)

    def address_form(
        self, language: str, login: Login, error: str | None = None, status: int = 200
    ) -> HTMLResponse:
        texts = TEXTS[language]
        note = f'<p class="error">{escape(error)}</p>' if error else ""
        body = f"""\
<p>{escape(texts["intro"].format(client=login.client_label))}</p>
{note}
<form method="post" action="/login">
<input type="hidden" name="txn" value="{escape(login.txn)}">
<label for="email">{escape(texts["address"])}</label>
<input id="email" name="email" type="email" autocomplete="email" maxlength="254" required autofocus>
<p class="hint">{escape(texts["address_hint"])}</p>
<button type="submit">{escape(texts["request"])}</button>
</form>"""
        return self._page(language, "sign_in", body, status, login)

    def code_form(
        self,
        language: str,
        login: Login,
        error: str | None = None,
        status: int = 200,
        background: BackgroundTask | None = None,
    ) -> HTMLResponse:
        texts = TEXTS[language]
        note = f'<p class="error">{escape(error)}</p>' if error else ""
        other = ""
        if login.submits < MAX_SUBMITS:
            again = urlencode({"txn": login.txn})
            other = (
                f'<p class="other"><a href="/login?{escape(again)}">'
                f"{escape(texts['other_address'])}</a></p>"
            )
        body = f"""\
<p>{escape(texts["sent"].format(address=login.address or ""))}</p>
<p class="reference">{escape(texts["reference"].format(reference=login.reference))}</p>
{note}
<form method="post" action="/login/code">
<input type="hidden" name="txn" value="{escape(login.txn)}">
<label for="code">{escape(texts["code"])}</label>
<input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="12"
 required autofocus>
<button type="submit">{escape(texts["confirm"])}</button>
</form>
{other}"""
        return self._page(language, "enter_code", body, status, login, background)

    def notice(self, language: str, kind: str, status: int) -> HTMLResponse:
        body = f"<p>{escape(TEXTS[language][kind])}</p>"
        return self._page(language, f"{kind}_title", body, status)


def add_login_routes(
    server: MCPServer,
    logins: Logins,
    finish: Callable[[Login], str],
    config: RemoteConfig,
    mailer: Mailer,
) -> None:
    pages = Pages(config)

    async def deliver(outgoing: Outgoing | None, language: str) -> None:
        if outgoing is None:
            return
        try:
            await mailer.send(code_mail(outgoing, language, config.mail_from))
        except Exception as exc:
            # Only the type: a refused recipient comes back with the address in its message.
            logger.error(
                "Could not send the sign-in code for tenant %s: %s",
                outgoing.tenant,
                type(exc).__name__,
            )
            return
        logger.info(
            "Sent a sign-in code to %s of tenant %s.", mask(outgoing.address), outgoing.tenant
        )

    @server.custom_route("/login", methods=["GET"])
    async def address_page(request: Request) -> Response:
        language = language_of(request)
        login = logins.find(request.query_params.get("txn"))
        if login is None:
            return pages.notice(language, "expired", 410)
        return pages.address_form(language, login)

    @server.custom_route("/login", methods=["POST"])
    async def address_submitted(request: Request) -> Response:
        language = language_of(request)
        try:
            form = await read_form(request)
        except FormError as exc:
            return pages.notice(language, "invalid", exc.status)
        login = logins.find(form.get("txn"))
        if login is None:
            return pages.notice(language, "expired", 410)
        if login.submits >= MAX_SUBMITS:
            logins.end(login)
            return pages.notice(language, "locked", 429)
        address = normalize(form.get("email", ""))
        if not is_address(address):
            return pages.address_form(language, login, TEXTS[language]["not_an_address"], 400)
        outgoing = logins.submit(login, address)
        return pages.code_form(
            language, login, background=BackgroundTask(deliver, outgoing, language)
        )

    @server.custom_route("/login/code", methods=["POST"])
    async def code_submitted(request: Request) -> Response:
        language = language_of(request)
        try:
            form = await read_form(request)
        except FormError as exc:
            return pages.notice(language, "invalid", exc.status)
        login = logins.find(form.get("txn"))
        if login is None:
            return pages.notice(language, "expired", 410)
        if login.address is None:
            return pages.address_form(language, login)
        result = logins.check(login, form.get("code", ""))
        if result is Check.ACCEPTED:
            logins.end(login)
            return RedirectResponse(finish(login), status_code=303, headers=NO_STORE)
        if result is Check.LOCKED:
            return pages.notice(language, "locked", 429)
        left = MAX_ATTEMPTS - login.attempts
        texts = TEXTS[language]
        error = texts["wrong_one"] if left == 1 else texts["wrong_many"].format(left=left)
        return pages.code_form(language, login, error, 400)

    @server.custom_route("/login/style.css", methods=["GET"])
    async def style(request: Request) -> Response:
        return Response(
            STYLE,
            media_type="text/css",
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )
