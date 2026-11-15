# Running in a container

`docker/assets.Dockerfile` builds the service on Microsoft's Playwright image, and
`compose.dev.yaml` runs it locally with the bundles of this repository. The image holds the code
and its Python dependencies, nothing else: no bundles, no `.env`, no tests.

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
| User | `pwuser`, which the base image creates; the service never runs as root |
| Port | `8010`, uvicorn on `0.0.0.0` inside the container |
| Bundles | `/bundles`, set as `BUNDLES_DIR`, from a volume |
| Health | `HEALTHCHECK` calls `/health` every 30 seconds |

`.dockerignore` lists what the build may see instead of what it may not: everything is excluded
except `app/` and `requirements.txt`, so `.env`, the virtualenv, the bundles and the handover notes
never reach the build context. A file the Dockerfile is meant to copy has to be added there too.

## Configuration

The variables are the ones in the [README](../README.md#configuration), passed as environment
variables. Compose takes them from `.env` with `env_file` and sets `BUNDLES_DIR` to `/bundles` on
top: a `BUNDLES_DIR` in `.env` names a folder of the host, which does not exist in the container,
and `env_file` would otherwise hand it over. `DEFAULT_TEMPLATE` has to name a template of the
mounted bundles, or the container stops at startup with a message listing the ones it found.

## Bundles

Compose mounts `./bundles` read-only at `/bundles`. To render the bundles of another folder,
change the left side of the volume. `template.css` and the `.j2` files are read again on every
request, so an edit there shows on the next render; `bundle.toml`, `template.toml`, fonts and
fallback images are read at startup and need a restart:

```
docker compose -f compose.dev.yaml restart
```

Mount a folder, not the root of a file system: a `lost+found` folder there would count as a bundle
and stop the start.

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
docker run --rm --init --shm-size=1g -p 127.0.0.1:8010:8010 --env-file .env -e BUNDLES_DIR=/bundles -v "$PWD/bundles:/bundles:ro" contentgun-assets
```

Anywhere the container is reachable from outside the host, keep `DEBUG_ROUTES` off: the debug
routes render arbitrary text without a key.
