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

## Tools

| Tool | What it does |
|---|---|
| `list_templates` | The templates of the tenant, as `GET /v1/templates` lists them |
| `render_card` | One card, stored by the service; answers with its URL, its size, until when the URL works, a preview, and the `curl` command that downloads the card |
| `render_carousel` | Cover, content slides and closing slide, optionally also as one PDF; answers with every URL in order, one preview of all slides, and the `curl` command that downloads every file |
| `create_upload` | A URL that takes one file, and the `curl -T` command that sends it |
| `whoami` | The signed-in address, its tenant, and until when the sign-in lasts |

The tenant always comes from the token: no tool takes one. Every request to the service carries
`INTERNAL_KEY` and that tenant in `X-Contentgun-Tenant`, so a person sees and renders exactly
what the keys of the tenant would.

Results are stored by the service, with `"output": "url"`, so they need storage there; without it,
every render answers that storage is not configured. The model gets the public URL of the file,
under `PUBLIC_FILES_URL` of the service. The server reads the PNG back through `ASSETS_BASE_URL`
for the preview, so the preview does not depend on the public address. When that read fails, the
answer has the URL without a preview.

The preview is for the model, which checks the result with it; claude.ai does not show it in
Claude's answer. A result reaches the chat as a file instead: every answer ends with a `curl`
command that downloads it, and the descriptions of both tools tell Claude to run the command in
its code execution environment and share the files, which claude.ai shows in the chat with a
download. The command of a carousel saves the slides as `carousel-<id>-01.png` onward, in their
order, and the PDF as `carousel-<id>.pdf`, where `<id>` is the start of the cover's request id,
so two carousels in one chat do not overwrite each other. The download needs the same entry as
an upload, see below; without it, or without a code execution environment, the user gets the URLs.

### Images and icons

A card or a content slide takes at most one of six sources:

| Parameter | Source |
|---|---|
| `icon_name` | The name of an icon of the template's own set, as `list_templates` lists it under `icons` |
| `image_url` | A public `https` address of a PNG, JPEG or WebP, which the service fetches |
| `image_upload` | The `upload_id` of a PNG, JPEG or WebP sent with `create_upload` |
| `icon_svg` | The markup of an SVG pictogram, which Claude draws itself |
| `icon_url` | A public `https` address of an SVG |
| `icon_upload` | The `upload_id` of an SVG sent with `create_upload` |

For a template that lists icons, the descriptions tell Claude to give every content slide the icon
whose name fits its point, unless it shows a picture or the user wants a slide without one, and to
draw or link a pictogram only for a template without a set, see
[Icons of a bundle](../render.md#icons-of-a-bundle). The service itself never requires an icon.

`icon_name`, `icon_svg` and `image_url` work without anything else. A file attached to the chat goes through
`create_upload`: Claude runs the returned command in its code execution environment, which takes
an outside address only when it is allowed there. An owner of the Claude organization adds the
host of `PUBLIC_FILES_URL`, for example `files.contentgun.de`, under Settings, Capabilities,
"Allow network egress", "Additional allowed domains". Without that entry the command fails with
`CONNECT tunnel failed, response 403`, and the description of `create_upload` tells Claude to say
so. The same entry lets Claude download a result and show it in the chat as a file; when that
download fails, the descriptions of `render_card` and `render_carousel` tell Claude to say once
that the entry is missing.

`extra_fields` takes the further fields a template declares, but none of the keys the request
sets itself, such as `template`, `image`, `code` or `output`.

### Code

`render_card` takes `code`, `language`, `line_numbers` and `highlight_lines` for a template that
accepts code, which `list_templates` reports as `accepts_code`, such as `demo/code`. They go to
the service as they are, which checks and highlights the code, see
[Code](../render.md#code). The tool's description gives the model the limit of 14 lines of 48
columns and asks for the code without Markdown fences.

### Failures

A message of the service comes through as the tool's error, with the hint the local server gives
for its status. A failure of the service itself names no internal address: it says that the
ContentGun service did not answer, cannot be reached, refused the connector, or answered
unexpectedly. In a carousel, the error names the slide; the slides rendered before it stay stored
until they expire.

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
| `MAIL_FROM` | with smtp | The sender, such as `ContentGun <login@contentgun.de>` |
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

## In a container

`docker/mcp.Dockerfile` builds the image, and the compose profile `remote` runs it next to the
service, behind a gateway that also serves the file URLs, see
[../docker.md](../docker.md#the-profile-remote).

## Trying it with claude.ai

claude.ai reaches only a public `https://` address. Locally, a tunnel to the gateway of the
profile `remote` gives one, for the MCP server and the file URLs together. The service needs its
storage, and `.env` its storage settings and `INTERNAL_KEY`.

1. Start the tunnel and note its address, `https://<name>.trycloudflare.com`:

   ```
   cloudflared tunnel --url http://127.0.0.1:8030
   ```

2. Start everything with that address. `MCP_SECRET` stays the same for the whole session, so a
   restart of `mcp` keeps everybody signed in:

   ```
   export PUBLIC_FILES_URL=https://<name>.trycloudflare.com MCP_SECRET="$(openssl rand -hex 32)" MCP_EMAILS=you@example.com S3_ENDPOINT_IN_COMPOSE=http://storage:9000
   docker compose -f compose.dev.yaml --profile storage --profile remote up --build
   ```

3. In claude.ai, add a custom connector with `https://<name>.trycloudflare.com/mcp`, connect, and
   type the address. The code is in the log:

   ```
   docker compose -f compose.dev.yaml logs -f mcp
   ```

4. For uploads from the chat, add `<name>.trycloudflare.com` under "Additional allowed domains"
   in the capabilities of Claude.

The address of a quick tunnel changes with every start of `cloudflared`: then export the new one,
start compose again, and change the URL of the connector.

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
| `remote_server.py` | `build_app()`, the descriptions of the tools, `/health` and `main()` |
| `remote_tools.py` | What the tools do, without the SDK, and the wording of failures |
| `cards.py` | What both servers share: the limits, the carousel models, the bodies of cards and carousels |
| `client.py` | The HTTP client of the service, shared with the local server |
| `preview.py` | The JPEG preview and the contact sheet, shared with the local server |
| `remote_config.py` | The settings from the environment |
| `oauth.py` | The OAuth provider: signed clients, codes and tokens |
| `login.py` | The sign-ins in progress, the codes and the limits |
| `login_pages.py` | The sign-in pages, their texts in German and English, and the mail |
| `accounts.py` | Address to tenant, from the tenants file or `MCP_EMAILS` |
| `signing.py` | HMAC keys derived per purpose from `MCP_SECRET` and `MCP_PUBLIC_URL` |
| `mailer.py` | SMTP and the console |
| `version.py` | The version, the same as the service's |

The remote modules import nothing that only the local server uses, and
`tests/test_mcp_remote_server.py` checks that. The tests run the server in process with `tests/mcp_remote.py`, which plays the steps
of claude.ai, a mailbox that collects the codes, and a clock that moves forward.
`tests/test_mcp_remote_tools.py` runs the tools against a fake of the service, and
`tests/test_mcp_remote_e2e.py` against the real one, with Chromium and storage in memory.
