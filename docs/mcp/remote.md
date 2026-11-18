# Remote MCP server for claude.ai

`mcp/remote_server.py` is the MCP server that claude.ai connects to as a custom connector. It
speaks Streamable HTTP on `/mcp`, is its own OAuth authorization server, and signs people in with
a code sent to an address in the tenants file. It renders through the service like any other
client, with `INTERNAL_KEY` and the tenant of the signed-in address in `X-Contentgun-Tenant`.

The local server in [README.md](README.md) shares the folder and stays as it is: it runs on a Mac
for the Claude desktop app and writes files there.

## How Claude connects

1. A user adds a custom connector in claude.ai with the address of `/mcp`, for example
   `https://mcp.contentgun.de/mcp`, and connects.
2. Claude reads the metadata, registers itself as an OAuth client and opens the sign-in page of
   this server in the browser.
3. The user types an address. If it belongs to a tenant, a mail with an eight-digit code goes out.
4. The user types the code into the same page. The page returns to Claude, which exchanges the
   result for tokens and calls the tools with them.

The page looks the same for an address that belongs to no tenant, so it does not tell who is a
customer. There is no link in the mail: a link that completes the sign-in would let someone start
a sign-in for a customer's address from their own Claude account and have it completed by an
unwary click. The code only works in the window where the sign-in began, and the mail warns never
to pass it on.

The page and the mail are in German for a browser that prefers German, otherwise in English.

## Who may sign in

An address may sign in when it belongs to a tenant:

- **With a tenants file** (`TENANTS_FILE`): the `emails` of each tenant, the same file the
  service reads, see [../tenants/README.md](../tenants/README.md). The server reads it again
  when it changes; a changed file that is invalid or gone leaves the addresses read before in use
  and logs a warning.
- **Without one** (`MCP_EMAILS`): a comma-separated list of addresses, all of the one tenant
  `default`, which the service has when it runs with `API_KEYS`.

Exactly one of the two must be set. The sign-in takes plain ASCII addresses only; an address in
the tenants file that does not fit is reported at start, masked, and cannot sign in.

## Sessions and taking access away

A sign-in lasts 90 days. Within it, Claude renews its access token, which lasts 60 minutes, with
a refresh token, which lasts 30 days. Nothing is stored: every client and token is signed with
`MCP_SECRET`, so a restart or a new release keeps everybody signed in.

On every request the server checks that the address still belongs to the tenant it signed in for.

| To | Do |
|---|---|
| End one person's access | Remove the address from the tenants file. The next request is refused, and Claude asks for a new sign-in |
| Move a person to another tenant | Move the address. The old tokens are refused, a new sign-in gets the new tenant |
| Sign everybody out | Set `MCP_SESSIONS_AFTER` to the current time and restart |
| Invalidate every client and token at once | Change `MCP_SECRET`. Claude then fails with `invalid_client` and needs a reconnect in its connector settings |

A single token cannot be revoked, and `/revoke` is off: Claude never calls it.

After access ends, claude.ai shows the connector as disconnected; the way back is "Reconnect" in
Customize > Connectors, which can need a reload of that page.

## Clients

Only Claude may register: a client must register with a client secret (`client_secret_post` or
`client_secret_basic`) and with Claude's callback, `https://claude.ai/api/mcp/auth_callback` or
`https://claude.com/api/mcp/auth_callback`. `MCP_REDIRECT_URIS` adds further callbacks.
Programs that register without a secret or with a callback on `localhost`, Claude Code among
them, are refused.

Every connect registers a new client. Nothing is stored for it: the client id is signed and
names its callbacks, and the secret is derived from the id.

## Settings

The server reads its settings from the environment only, never from `.env`.

| Variable | Required | Meaning |
|---|---|---|
| `MCP_PUBLIC_URL` | yes | The public address without a path, such as `https://mcp.contentgun.de`. `http://` only on `localhost` |
| `MCP_SECRET` | yes | Key of every signature, at least 32 characters, not `INTERNAL_KEY`. `openssl rand -hex 32` makes one |
| `ASSETS_BASE_URL` | yes | Address of the service, such as `http://assets:8010` |
| `INTERNAL_KEY` | yes | The internal key of the service, at least 32 characters |
| `TENANTS_FILE` | one of the two | The tenants file of the service. A relative path counts from the working directory |
| `MCP_EMAILS` | one of the two | Addresses of the tenant `default`, comma-separated |
| `MCP_MAIL` | no | `smtp` (default) or `console`, which writes every mail to the log instead. Development only |
| `SMTP_HOST` | with smtp | The mail server |
| `SMTP_PORT` | no | Default 587 with `starttls`, 465 with `tls` |
| `SMTP_SECURITY` | no | `starttls` (default) or `tls`. The certificate is always verified, and nothing is sent unencrypted |
| `SMTP_USER`, `SMTP_PASSWORD` | no | Both or neither |
| `MAIL_FROM` | with smtp | The sender, such as `Contentgun <login@contentgun.de>` |
| `MCP_SESSIONS_AFTER` | no | A moment with a time zone, such as `2026-11-18T12:00:00Z`. Sign-ins before it are no longer valid |
| `MCP_ACCESS_MINUTES` | no | Lifetime of an access token, 1 to 1440, default 60 |
| `MCP_REDIRECT_URIS` | no | Further allowed callbacks, `https://` addresses, comma-separated |
| `MCP_IMPRINT_URL`, `MCP_PRIVACY_URL` | no | Links in the footer of the sign-in page |
| `ASSETS_TIMEOUT_S` | no | Time for one request to the service, default 30 |

A missing or invalid setting stops the start with a sentence that names it.

## Running it

Locally, with the service on port 8011 and the mails in the log:

```
MCP_PUBLIC_URL=http://localhost:8020 MCP_SECRET="$(openssl rand -hex 32)" ASSETS_BASE_URL=http://127.0.0.1:8011 INTERNAL_KEY="$(grep '^INTERNAL_KEY=' .env | cut -d= -f2- | tr -d "\"' ")" MCP_EMAILS=you@example.com MCP_MAIL=console .venv/bin/python mcp/remote_server.py
```

`--host` and `--port` set where it listens, by default `127.0.0.1:8020`. `GET /health` answers
without a token. claude.ai only reaches a public `https://` address; a tunnel such as
`cloudflared tunnel --url http://127.0.0.1:8020` gives one, and `MCP_PUBLIC_URL` must then be the
address of the tunnel.

## Operation

- **One process.** A sign-in in progress and the authorization code at its end live in memory.
  A restart ends the sign-ins in progress, nothing else; several processes would not share them.
- **Restarts are cheap.** Tokens survive them. A request that arrives while the server is down
  fails, and Claude can need a few minutes to recover when its access token expires in that
  moment, so keep the downtime of a deploy short and let the proxy retry.
- **The long request.** Claude keeps one request on `/mcp` open for minutes as an event stream
  (`subscriptions/listen`). A proxy in front must not buffer or cut it, and a shutdown gives it
  three seconds before it ends.
- **Host and Origin.** `/mcp` takes requests only for the host of `MCP_PUBLIC_URL`, with or
  without the default port, and only from that origin or claude.ai's; requests without `Origin`,
  which is how Claude sends them, pass. A proxy must pass the original `Host` on.
- **Keep-alive.** Idle connections stay open for 130 seconds, longer than the two minutes Caddy
  keeps an idle upstream connection.
- **No access log.** It would show query strings, and the sign-in carries its id in one.

## Logs

The server logs to stderr: registrations by the host of their callback, codes sent and held back,
completed sign-ins and refused renewals with their reason, each with the tenant and the address
masked to its first letter and domain (`t…@acme.example`). It never logs a token, a code, a client
secret, `MCP_SECRET` or a full address, and an address that belongs to no tenant not at all.

## Limits

| What | Limit |
|---|---|
| A sign-in in progress | 15 minutes from the start in Claude |
| Codes per address | 3 in 15 minutes, 10 in 24 hours, for all its sign-ins together |
| Codes for all addresses | 30 a minute |
| Wrong codes | 5 per sign-in; the fifth ends it |
| Addresses per sign-in | 3 |
| Sign-ins in progress | 2,000; when full, the oldest one without a code goes |
| A form | 8 KB |

A code held back by a limit gets the same page as one sent, and the log says so.

## Modules

| Module | Role |
|---|---|
| `remote_server.py` | `build_app()`, the tools, `/health` and `main()` |
| `remote_config.py` | The settings from the environment |
| `oauth.py` | The OAuth provider: signed clients, codes and tokens |
| `login.py` | The sign-ins in progress, the codes and the limits |
| `login_pages.py` | The sign-in pages, their texts in German and English, and the mail |
| `accounts.py` | Address to tenant, from the tenants file or `MCP_EMAILS` |
| `signing.py` | HMAC keys derived per purpose from `MCP_SECRET` and `MCP_PUBLIC_URL` |
| `mailer.py` | SMTP and the console |
| `version.py` | The version, the same as the service's |

The remote modules import nothing of the local server, and `tests/test_mcp_remote_server.py`
checks that. The tests run the server in process with `tests/mcp_remote.py`, which plays the steps
of claude.ai, a mailbox that collects the codes, and a clock that moves forward.
