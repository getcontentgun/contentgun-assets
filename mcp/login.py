import hmac
import logging
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from mcp.server.auth.provider import AuthorizationParams

from accounts import Accounts, mask

logger = logging.getLogger(__name__)

LOGIN_SECONDS = 15 * 60
MAX_LOGINS = 2000
MAX_SUBMITS = 3
MAX_ATTEMPTS = 5
CODE_DIGITS = 8
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 4
DAY_SECONDS = 24 * 3600
ADDRESS_LIMITS = ((3, 15 * 60), (10, DAY_SECONDS))
GLOBAL_LIMIT = (30, 60)
CLAUDE_HOSTS = ("claude.ai", "claude.com")


class LoginsFull(Exception):
    pass


@dataclass
class Login:
    txn: str
    client_id: str
    params: AuthorizationParams
    started: float
    reference: str
    address: str | None = None
    tenant: str | None = None
    code: str | None = None
    submits: int = 0
    attempts: int = 0

    @property
    def expires(self) -> float:
        return self.started + LOGIN_SECONDS

    @property
    def callback_origin(self) -> str:
        parts = urlsplit(str(self.params.redirect_uri))
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def client_label(self) -> str:
        host = urlsplit(str(self.params.redirect_uri)).hostname or ""
        return f"Claude ({host})" if host in CLAUDE_HOSTS else host


@dataclass(frozen=True)
class Outgoing:
    address: str
    tenant: str
    code: str
    reference: str
    expires: float
    client_label: str


class Check(Enum):
    ACCEPTED = "accepted"
    WRONG = "wrong"
    LOCKED = "locked"


class Logins:
    """The sign-ins in progress, in memory: a restart ends them, which costs nobody more than
    starting the connection again."""

    def __init__(self, accounts: Accounts, clock: Callable[[], float]):
        self._accounts = accounts
        self._clock = clock
        self._pending: dict[str, Login] = {}
        self._sent: dict[str, deque[float]] = {}
        self._recent: deque[float] = deque()

    def start(self, client_id: str, params: AuthorizationParams) -> Login:
        now = self._clock()
        self._sweep(now)
        if len(self._pending) >= MAX_LOGINS:
            self._evict()
        login = Login(
            txn=secrets.token_urlsafe(24),
            client_id=client_id,
            params=params,
            started=now,
            reference="".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH)),
        )
        self._pending[login.txn] = login
        return login

    def find(self, txn: str | None) -> Login | None:
        login = self._pending.get(txn or "")
        if login is None:
            return None
        if login.expires <= self._clock():
            self.end(login)
            return None
        return login

    def end(self, login: Login) -> None:
        self._pending.pop(login.txn, None)

    def submit(self, login: Login, address: str) -> Outgoing | None:
        """Takes the address typed into a sign-in. Returns the mail to send, or None when the
        address belongs to no tenant or too many codes went out; the page looks the same."""
        login.submits += 1
        login.address = address
        login.tenant = None
        login.code = None
        tenant = self._accounts.tenant_of(address)
        if tenant is None:
            return None
        now = self._clock()
        if not self._may_send(address, now):
            logger.warning(
                "Held back a sign-in code for %s of tenant %s: too many codes went out.",
                mask(address),
                tenant,
            )
            return None
        self._note_sent(address, now)
        login.tenant = tenant
        login.code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        return Outgoing(
            address=address,
            tenant=tenant,
            code=login.code,
            reference=login.reference,
            expires=login.expires,
            client_label=login.client_label,
        )

    def check(self, login: Login, typed: str) -> Check:
        login.attempts += 1
        candidate = "".join(typed.split()).replace("-", "")
        # A sign-in without a mail compares against a value no typed code can match, so it
        # behaves exactly like a wrong code.
        expected = login.code or "-" * CODE_DIGITS
        matches = hmac.compare_digest(candidate.encode(), expected.encode())
        if matches and self._clock() < login.expires:
            return Check.ACCEPTED
        if login.attempts >= MAX_ATTEMPTS:
            self.end(login)
            return Check.LOCKED
        return Check.WRONG

    def _sweep(self, now: float) -> None:
        for txn in [txn for txn, login in self._pending.items() if login.expires <= now]:
            del self._pending[txn]
        for address in [
            address for address, times in self._sent.items() if times[-1] <= now - DAY_SECONDS
        ]:
            del self._sent[address]

    def _evict(self) -> None:
        txn = next((txn for txn, login in self._pending.items() if login.code is None), None)
        if txn is None:
            raise LoginsFull
        del self._pending[txn]

    def _may_send(self, address: str, now: float) -> bool:
        count, window = GLOBAL_LIMIT
        while self._recent and self._recent[0] <= now - window:
            self._recent.popleft()
        if len(self._recent) >= count:
            return False
        times = self._sent.get(address, ())
        return all(
            sum(1 for moment in times if moment > now - window) < count
            for count, window in ADDRESS_LIMITS
        )

    def _note_sent(self, address: str, now: float) -> None:
        times = self._sent.setdefault(address, deque())
        times.append(now)
        while times[0] <= now - DAY_SECONDS:
            times.popleft()
        self._recent.append(now)
