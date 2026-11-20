# Running in a container

`docker/assets.Dockerfile` builds the service on Microsoft's Playwright image, and
`compose.dev.yaml` runs it locally. The image holds the code, its Python dependencies and the
built-in bundles in `bundles/`, nothing else: no bundles of a tenant, no `.env`, no tenants file,
no tests.

`docker/mcp.Dockerfile` builds the [remote MCP server](mcp/remote.md), a second image; compose
runs it with the profile `remote`, behind a gateway, see [below](#the-profile-remote).

## Local run

The container reads its configuration from `.env`, so create it first, as described in the
[README](../README.md#installation), with a key in `API_KEYS`. Then build and start, and render the
demo card from a second terminal:

```
docker compose -f compose.dev.yaml up --build
curl -sS -o card.png -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{"template": "demo/card", "headline": "Hallo"}' http://127.0.0.1:8010/v1/render
```

`$API_KEY` is one of the keys from `API_KEYS`. The port is published on `127.0.0.1` only, which
matters because `.env.example` switches the unauthenticated debug routes on; the overview is at
`http://127.0.0.1:8010/v1/debug`.

On the host, compose takes port 8010, which `bin/development` uses as well. `PORT` moves it to
another one, as it does for `bin/development`; inside the container uvicorn stays on 8010, where
the health check expects it:

```
PORT=8011 docker compose -f compose.dev.yaml up
```

## The image

| | |
|---|---|
| Base | `mcr.microsoft.com/playwright/python:v1.62.0-noble`: Ubuntu 24.04, Python 3.12 and the browsers of Playwright 1.62.0 under `/ms-playwright` |
| Code | `app/` and the packages of `requirements.txt` in `/app` |
| Bundles | the built-in bundles in `/app/bundles`, where the service finds them on its own |
| User | `pwuser`, which the base image creates; the service never runs as root |
| Port | `8010`, uvicorn on `0.0.0.0` inside the container |
| Health | `HEALTHCHECK` calls `/health` every 30 seconds |

`.dockerignore` lists what the builds may see instead of what they may not: everything is
excluded except `app/`, `bundles/`, `mcp/`, `requirements.txt` and `requirements-mcp.txt`, so
`.env`, a tenants file, the virtualenv and the handover notes never reach the build context. A file
a Dockerfile is meant to copy has to be added there too; `tests/test_docker.py` checks every
`COPY` of both Dockerfiles against it.

## The image of the MCP server

| | |
|---|---|
| Base | `python:3.12.16-slim-trixie`, pinned by its digest as well, because slim tags are rebuilt in place. Python 3.12, as in the image of the service and in CI |
| Code | `mcp/` and the packages of `requirements-mcp.txt` in `/app`, nothing of `app/` |
| User | `contentgun`, uid 10001, created in the image |
| Port | `8020` |
| Health | `HEALTHCHECK` calls `/health` every 30 seconds; it answers without the service |
| Start | `python mcp/remote_server.py`: a script start puts `mcp/` on the import path, which its flat imports need |

The server keeps no state on disk, so the container needs no volume and can run with a read-only
file system. Its settings come from the environment, see [mcp/remote.md](mcp/remote.md#settings).
A new base image means a new tag and a new digest in the `FROM` line together:
`docker buildx imagetools inspect python:3.12-slim-trixie` shows both.

## The profile `remote`

```
docker compose -f compose.dev.yaml --profile storage --profile remote up --build
```

adds two services to the service and its storage:

- **`mcp`**, the remote MCP server. Compose hands it its settings one by one instead of the whole
  `.env`, so it sees neither `API_KEYS` nor the keys of the bucket. It reaches the service at
  `http://assets:8010`, takes `INTERNAL_KEY` from the shell or `.env`, and defaults `MCP_MAIL` to
  `console`: the codes appear in `docker compose -f compose.dev.yaml logs -f mcp`.
- **`gateway`**, a Caddy on `127.0.0.1:8030` (`GATEWAY_PORT` moves it) that sends `/o/` and `/u/`
  to the service and everything else to `mcp`, as `files.contentgun.de` and `mcp.contentgun.de`
  will in production. One tunnel to the gateway therefore publishes the MCP server and the file
  URLs, and nothing else of the service. While `mcp` restarts, the gateway holds requests for up to
  ten seconds instead of failing them.

`MCP_PUBLIC_URL` defaults to `PUBLIC_FILES_URL`, because behind the gateway both are the address
of the tunnel. How to try it with claude.ai is in
[mcp/remote.md](mcp/remote.md#trying-it-with-claudeai).

## Configuration

The variables are the ones in the [README](../README.md#configuration), passed as environment
variables. Compose takes them from `.env` with `env_file` and sets `BUNDLES_DIR` and `TENANTS_FILE`
to empty on top: both name paths of the host, which do not exist in the container, and `env_file`
would otherwise hand them over. A blank value counts as unset, so the container runs with the one
tenant of `API_KEYS` and the built-in bundles. `DEFAULT_TEMPLATE` has to name a template the tenant
can use, or the container stops at startup with a message listing the ones it found.

## Bundles

The built-in bundles are part of the image. Compose mounts `./bundles` over them, read-only at
`/app/bundles`, so an edit in the repository shows without a rebuild. `template.css` and the `.j2`
files are read again on every request, so an edit there shows on the next render; `bundle.toml`,
`template.toml`, fonts and fallback images are read at startup and need a restart:

```
docker compose -f compose.dev.yaml restart
```

## Tenants in the container

With several tenants the container needs the tenants file and the folder of their bundles, mounted
from the host, and `API_KEYS` empty, since the service refuses to start with both. A second compose
file next to `compose.dev.yaml` does that without touching it, for example `compose.tenants.yaml`
with the paths of your machine:

```
services:
  assets:
    environment:
      API_KEYS: ""
      TENANTS_FILE: /config/tenants.toml
      BUNDLES_DIR: /tenants
    volumes:
      - /path/to/tenants.toml:/config/tenants.toml:ro
      - /path/to/contentgun-templates/bundles:/tenants:ro
```

```
docker compose -f compose.dev.yaml -f compose.tenants.yaml up
```

The keys then belong to the tenants, see [Tenants](tenants/README.md). Mount a folder as
`BUNDLES_DIR`, not the root of a file system: a `lost+found` folder there would belong to no tenant
and stop the start.

## Storage in the container

The profile `storage` of `compose.dev.yaml` adds RustFS, an S3-compatible store, as the service
`storage` on `127.0.0.1:9010` with its console on `127.0.0.1:9011` and its data in the volume
`storage`. `STORAGE_PORT` and `STORAGE_CONSOLE_PORT` in `.env` move the two ports of the host, as
`PORT` moves the one of the service. On its own it serves the service on the host:

```
docker compose -f compose.dev.yaml --profile storage up -d storage
```

With the service in the container as well, the endpoint differs: inside the container `127.0.0.1`
is the container itself, and RustFS is `http://storage:9000`, on its own port rather than the one
of the host. Compose therefore passes
`S3_ENDPOINT_IN_COMPOSE` from `.env` as `S3_ENDPOINT`, and `S3_ENDPOINT` itself where it is unset:

```
S3_ENDPOINT_IN_COMPOSE=http://storage:9000
```

```
docker compose -f compose.dev.yaml --profile storage up --build
```

The other storage settings stay as they are; `PUBLIC_FILES_URL` is the address the host reaches the
container under. The whole flow, the bucket and the checks are in
[Storage](storage/README.md#locally-with-rustfs).

## Health

`/health` answers `503` until Chromium is connected, so the container only turns healthy once it
can render; `docker compose -f compose.dev.yaml ps` shows the state. The check runs with Python's
`urllib`, which the image is sure to have, so it needs no extra package.

## Chromium in a container

- **`shm_size: 1gb`.** Chromium keeps the pages it renders in `/dev/shm`, and Docker's default of 64 MB lets its renderer crash on a page with a large image.
- **`init: true`.** Chromium starts processes of its own; a small init as PID 1 collects the ones that end, which uvicorn as PID 1 would leave as zombies.
- **No extra privileges.** Playwright starts Chromium without its sandbox unless asked otherwise, so the container needs neither added capabilities nor a seccomp profile.

## The Playwright version

The image tag and `playwright==1.62.0` in `requirements.txt` have to be the same version: the
Python package drives exactly the browser revision the image carries. Bump both together;
`tests/test_docker.py` fails as soon as they drift apart. The `jammy` tags are no option, because
the Python 3.10 of Ubuntu 22.04 is below the floor of 3.11.

## Without compose

```
docker build -f docker/assets.Dockerfile -t contentgun-assets .
docker run --rm --init --shm-size=1g -p 127.0.0.1:8010:8010 --env-file .env -e BUNDLES_DIR= -e TENANTS_FILE= contentgun-assets
docker build -f docker/mcp.Dockerfile -t contentgun-mcp .
```

## Smoke test

`bin/smoke` starts a built image, waits for `/health` and sends one request: the service renders
a card of the built-in `demo` bundle, the MCP server answers `/mcp` without a token with `401` and
the metadata claude.ai starts from. Both checks also make sure the container does not run as root.
CI runs it after every build; locally:

```
bin/smoke assets contentgun-assets
bin/smoke mcp contentgun-mcp
```

It publishes the container on `127.0.0.1:18080`; `SMOKE_PORT` moves it.

Anywhere the container is reachable from outside the host, keep `DEBUG_ROUTES` off: the debug
routes render arbitrary text without a key.

## CI and releases

`.github/workflows/ci.yml` runs three jobs:

| Job | When | What |
|---|---|---|
| `test` | every push and pull request | ruff and the suite in the Playwright image of the pin, so on Linux with the Chromium of production; `--require-chromium` turns a skipped render test into a failure |
| `images` | pushes to `main`, pull requests | builds both images and runs `bin/smoke` on them, publishes nothing |
| `release` | a tag `vX.Y.Z`, after `test` | checks that the tag equals `__version__` in `app/__init__.py` and that neither image of that version exists, builds both for `linux/amd64`, runs `bin/smoke` and pushes `ghcr.io/getcontentgun/assets:X.Y.Z` and `ghcr.io/getcontentgun/mcp:X.Y.Z` |

There is no `latest`: a deployment names its version. One version number covers both images, and
`tests/test_mcp_remote_server.py` keeps `mcp/version.py` equal to `app/__init__.py`. A release is
`bin/release`: it sets the version in both files and in `docs/health.md`, runs `bin/test`, commits,
pushes `main`, pushes the tag `vX.Y.Z` and follows `release` until both images are in GHCR,
asking before every step that changes the repository. `bin/deploy` in `contentgun-infra` then
puts the version on the server. Only `release` may write packages, and every action is pinned by
its commit, which `tests/test_ci.py` checks.

GHCR creates both packages private on the first push. Set them to public once in the package
settings on GitHub, or the server needs a `docker login ghcr.io` before it can pull.
