# contentgun-assets

Renders social media cards as PNG images from HTML templates, and carousels as one PDF for
LinkedIn. A template is filled with Jinja2, loaded into headless Chromium and screenshotted, so
colors, fonts and layout are exact and reproducible, unlike the image models the contentgun flows
used before.

Templates come in bundles: a bundle is a folder with the templates of one design and everything
they need, fonts and images included, see [docs/bundles/README.md](docs/bundles/README.md). A
template is addressed as `<bundle>/<template>`. The repository ships the built-in bundle
`bundles/demo/`, a neutral design in slate and amber: `card` and `card-image` for single cards,
and `carousel-cover`, `carousel-content` and `carousel-closing` for the slides of a carousel.
`GET /v1/templates` lists them with their formats and fields.

Every request belongs to a tenant, the owner of its key. A tenant sees the built-in bundles and
its own, which live outside the repository in its folder under `BUNDLES_DIR`. A fresh clone has
one tenant, whose keys are `API_KEYS`; several tenants come from a tenants file, see
[docs/tenants/README.md](docs/tenants/README.md).

Without storage nothing is stored: an image or icon, sent in the request or fetched by the service
from an `https` URL, lives in the request only, the PNG or PDF in the response. With an S3-compatible bucket the service also hands out results as URLs,
takes uploads through signed URLs and reads the bundles of the tenants from the bucket, see
[docs/storage/README.md](docs/storage/README.md).

## Requirements

- Python 3.11 or newer (developed on 3.13)
- Chromium, installed through Playwright

## Installation

From the repository root:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
```

`API_KEYS` is empty in the example, so generate a key and put it in `.env` before starting:

```
openssl rand -hex 32
```

## Running

```
bin/development
```

The script verifies what the service needs (virtualenv, dependencies, `.env`, a key, the tenants
and their bundles, Chromium, a free port) and then runs uvicorn in the foreground on
`127.0.0.1:8010` with reload. `bin/development --check` stops after the checks, `HOST`, `PORT` and `RELOAD=0` override
the defaults, and any further arguments go to uvicorn.

Without the script:

```
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

Render a sample card, then one on the template's fallback image and one on an image of your own:

```
API_KEY=... bash scripts/render_sample.sh
TEMPLATE=demo/card-image API_KEY=... bash scripts/render_sample.sh
IMAGE=photo.jpg API_KEY=... bash scripts/render_sample.sh
```

The script encodes the file for you. For a hand-written `curl` call, `base64 -i photo.jpg` is the
string the `image` field expects. `render_sample.sh` always sends a subline, so it is meant for
the `card-*` templates.

Render the sample carousel, five slides into `carousel/01.png` to `carousel/05.png`, in `4:5` or
in `1:1`, and once with an SVG icon under the text of every content slide:

```
API_KEY=... bash scripts/render_carousel.sh
FORMAT=1:1 API_KEY=... bash scripts/render_carousel.sh
ICON=lightbulb.svg API_KEY=... bash scripts/render_carousel.sh
```

`BUNDLE` picks the bundle whose carousel templates render the slides, `demo` by default.
`HANDLE` and `TARGET_DIR` override the handle and the output directory, and `ICON_COLOR=text`
draws the icon in the text color. How a flow builds a carousel from these templates is described
in [docs/render.md](docs/render.md#carousel).

## Docker

```
docker compose -f compose.dev.yaml up --build
```

Builds the image from `docker/assets.Dockerfile`, which carries the built-in bundles, and runs it on
`127.0.0.1:8010` with the configuration from `.env`. Like `bin/development`, it takes another port
of the host with `PORT`. Image, configuration, tenants in the container and the health check are
described in [docs/docker.md](docs/docker.md).

## Storage

```
docker compose -f compose.dev.yaml --profile storage up -d storage
.venv/bin/python scripts/setup_storage.py
```

Starts RustFS, an S3-compatible store, on `127.0.0.1:9010` and prepares its bucket, for results as
URLs, uploads and bundles from the bucket. `STORAGE_PORT` in `.env` moves it to another port. The
settings that go into `.env` and the whole flow are in
[docs/storage/README.md](docs/storage/README.md).

## Looking at a template

A `template.html.j2` cannot be opened directly: it is a Jinja template with placeholders, and the
fonts and CSS are only embedded while rendering. The debug route serves the real markup, through
the same code path as the screenshot, so the preview cannot drift from the output:

```
open http://127.0.0.1:8010/v1/debug
open "http://127.0.0.1:8010/v1/debug/html?template=demo/carousel-content&format=1:1"
```

`/v1/debug` is the overview: it links every template in every format and variant, with a sample
image and a sample icon where a template takes one. On `/v1/debug/html`, every field the template
declares can be set as a query parameter, for example `&pagination=3/7`; whatever the URL leaves
out comes from the sample copy in the template's `template.toml`.

The routes need `DEBUG_ROUTES=true` in `.env`. `template.css` and the `.j2` files, a bundle's
`_base/` included, are read again on every request, so an edit needs only a browser reload. Set
the browser window to 1080px wide to judge the proportions, and add `fit=0` to switch off the
fit-text pass while tuning the font sizes in the CSS.

## Tests

```
bin/test
```

Runs `ruff check`, `ruff format --check` and the suite as separate stages and prints a summary.
`--lint` and `--suite` pick one of them, `--bail` stops at the first failure, and any other
argument goes to pytest. The render tests drive real Chromium; without it they are skipped and the
script says so.

## MCP server for Claude Desktop

`mcp/` holds a local MCP server that lets Cowork in the Claude desktop app list the templates and
render cards and whole carousels into a local folder, through this service. Setup, tools and
troubleshooting are in [docs/mcp/README.md](docs/mcp/README.md).

## Documentation

[docs/README.md](docs/README.md) is the API reference: one page per endpoint, plus the
authentication rules. [docs/tenants/README.md](docs/tenants/README.md) describes tenants, keys and
the tenants file, [docs/storage/README.md](docs/storage/README.md) the bucket. [CLAUDE.md](CLAUDE.md)
holds the architecture and the pitfalls of the rendering.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEYS` | empty | Comma-separated bearer keys of the one tenant `default`, in plain text. Without a key anywhere, every authenticated route answers `500` |
| `TENANTS_FILE` | empty | A TOML file with several tenants, their key hashes and settings, see [docs/tenants/README.md](docs/tenants/README.md). Not together with `API_KEYS`. A relative path counts from the repository root |
| `INTERNAL_KEY` | empty | The key of a service that acts for every tenant and names it in `X-Contentgun-Tenant`. At least 32 characters; empty switches it off |
| `DEBUG_ROUTES` | `false` | Mounts the overview, debug route and sample files under `/v1/debug`, plus `/docs` and `/openapi.json`, all unauthenticated. Development only |
| `STRICT_FONTS` | `true` | A font that fails to load becomes a `500` instead of a wrong-looking card |
| `RENDER_CONCURRENCY` | `2` | Parallel browser pages |
| `RENDER_TIMEOUT_MS` | `15000` | Per-render budget, then `504` |
| `QUEUE_TIMEOUT_MS` | `10000` | Wait for a free slot, then `503` |
| `MAX_IMAGE_BYTES` | `8388608` | Largest image a request may carry, decoded or fetched from a URL, and the budget of all images and icons of a document together, then `413`. An icon is capped at 256 KB below that |
| `BUNDLES_DIR` | empty | The folder with one subfolder per tenant, holding the bundles of that tenant. Empty means the built-in bundles only. A relative path counts from the repository root |
| `DEFAULT_TEMPLATE` | `demo/card` | The template id used when the request omits `template`, for every tenant without a `default_template` of its own |
| `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | empty | The bucket for results, uploads and the bundles of the tenants, see [docs/storage/README.md](docs/storage/README.md). All of them with `PUBLIC_FILES_URL`, or none |
| `PUBLIC_FILES_URL` | empty | The address under which callers reach `/o/` and `/u/` of the service |
| `STORAGE_TTL_HOURS` | `24` | How long results and uploads stay available |

The handle on the card comes from the request. There is no server-side account list.

## Adding a template

Create `<bundle>/<template>/` with `template.toml`, `template.html.j2` and `template.css`, in a
built-in bundle under `bundles/` or in a bundle of a tenant under `<BUNDLES_DIR>/<tenant>/`, or a
new bundle with a `bundle.toml` that declares its fonts. The registry picks it up at startup and
`GET /v1/templates` lists it for every tenant that can use it. A folder whose name
starts with `_`, such as `_base/` with the shared skeleton of the carousel templates, is not a
template and is skipped. [docs/bundles/README.md](docs/bundles/README.md) describes the format,
[CLAUDE.md](CLAUDE.md) how a template is built.

## License

MIT, see [LICENSE](LICENSE).
