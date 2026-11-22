# MCP server for Claude Desktop

`mcp/` holds a local MCP server that lets a Claude session on the Mac, Cowork in the Claude
desktop app, work with this service: list the templates, render single cards and render whole
carousels into the Social folder. The desktop app starts it over stdio. It talks to the service
over HTTP, `bin/development` locally or a deployed instance, and is never deployed itself.

The same folder holds the remote MCP server that claude.ai connects to as a connector, with a
sign-in by mail. It is deployed and described in [remote.md](remote.md).

## Why it runs locally

- **The session cannot reach the service.** Cowork's shell runs in a Linux VM whose egress proxy does not let it reach the service, and `127.0.0.1` there is the VM, not the Mac.
- **The key stays out of the chat.** It lives in the desktop app's configuration; a key in the chat would end up in the transcript.
- **Files need a local process.** A remote `/mcp` endpoint on the service could hand the model an image, but not write a file to the Mac, and an image or icon would have to travel as base64 in a tool argument.

The flip side: the server exists only while the Mac and the desktop app are running.

## Setup

1. Install the dependencies. They are part of `requirements-dev.txt`, so the server runs from the development virtualenv:

   ```
   .venv/bin/pip install -r requirements-dev.txt
   .venv/bin/pip check
   ```

2. Start the service:

   ```
   bin/development
   ```

3. Add the server to `~/Library/Application Support/Claude/claude_desktop_config.json`, merged into whatever `mcpServers` already holds. `ASSETS_API_KEY` is a key of the tenant the server works for; locally that is one of the keys from `API_KEYS` in this repository's `.env`, the keys of the one tenant `default`:

   ```
   {
     "mcpServers": {
       "contentgun-assets": {
         "command": "/Users/<you>/Development/contentgun-assets/.venv/bin/python",
         "args": ["/Users/<you>/Development/contentgun-assets/mcp"],
         "env": {
           "ASSETS_BASE_URL": "http://127.0.0.1:8010",
           "ASSETS_API_KEY": "<a key from API_KEYS in the local .env>",
           "ASSETS_OUTPUT_ROOT": "/Users/<you>/Social",
           "ASSETS_BUNDLE": "demo",
           "ASSETS_IMAGE_ROOTS": "/Users/<you>/Social:/Users/<you>/Pictures"
         }
       }
     }
   }
   ```

4. Quit and restart the desktop app; it reads the file only at startup. In a Cowork session, `contentgun-assets` is now among the connected tools. Asking for the available templates calls `list_templates`.

The app runs the `mcp` folder directly, and Python puts the folder on its import path, so no
`PYTHONPATH` is needed. Every path must be absolute. The key sits in the file in plain text, the same
exposure as the development `.env`.

**Against a deployed service**, set `ASSETS_BASE_URL` to its address, `https://api.contentgun.de`
when the variable is not set, and give the server a key of its own, so it can be revoked without
touching other callers. With a tenants file, create one with `python3 scripts/new_api_key.py`,
add its hash to the `api_keys` of the tenant and restart the service, see
[Tenants](../tenants/README.md#keys). The server sees what that tenant sees: its own bundles and
the built-in ones. It never uses `INTERNAL_KEY`, which belongs to the [remote MCP server](remote.md).

## Tools

### `list_templates`

No arguments. Calls `GET /v1/templates` and returns its `{"templates": [...]}` unchanged: id
(`<bundle>/<template>`), bundle, name, description, formats with pixel sizes, fields, required
fields, defaults, template-specific `limits`, `accepts_image` and `accepts_icon`. Read-only.

### `render_card`

Renders one card and saves it as a PNG. Returns the saved path, pixel size, file size and request
id, plus a JPEG preview 540 px wide.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `output_path` | yes | Relative to `ASSETS_OUTPUT_ROOT`, or absolute inside it. Must end in `.png`. Missing folders are created |
| `headline` | no | Passed through. Required by the templates that list it in `required`, which `list_templates` reports |
| `subline` | no | Passed through |
| `handle` | no | Passed through, never filled in |
| `template` | no | A template id from `list_templates`, `<bundle>/<template>`. Omitted means the service's `DEFAULT_TEMPLATE` |
| `format` | no | Omitted means the template's first format |
| `image_path` | no | An image inside `ASSETS_IMAGE_ROOTS`, sent base64-encoded. Only templates with `accepts_image`; the template's description says what it does with the image |
| `icon_path` | no | An SVG pictogram inside `ASSETS_IMAGE_ROOTS`, sent base64-encoded. Only templates with `accepts_icon`. Its color goes into `extra_fields` as `icon_color`. Together with `image_path` it is refused |
| `code` | no | Source code for a template with `accepts_code`, such as `demo/code`. Passed through; the service checks and highlights it, see [Code](../render.md#code) |
| `language` | no | The language of the code, a short name of Pygments such as `python`. Passed through |
| `line_numbers` | no | `true` numbers the lines of the code. Passed through |
| `highlight_lines` | no | The lines of the code to highlight, counted from 1. Passed through |
| `extra_fields` | no | Further fields the template declares, such as `body`, `cta`, `topic`, `pagination`, `icon_color`, `quote` or `filename`, as `list_templates` reports them. A key that duplicates a parameter above, or `image` or `icon`, is refused |
| `overwrite` | no | Default `false`: an existing file is an error |

### `render_carousel`

Renders a whole carousel into one folder, one request per slide, with the carousel templates
of one bundle:

| File | Template | Content |
|------|----------|---------|
| `01.png` | `<bundle>/carousel-cover` | `cover.headline`, `cover.topic`, `cover.subline` |
| `02.png` to the second last | `<bundle>/carousel-content` | `headline` and `body` of each entry in `slides`, in order, and its `icon_path` or `image_path` |
| last | `<bundle>/carousel-closing` | `closing.headline`, `closing.cta` |

A bundle offers carousels by having templates with these three names.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `output_dir` | yes | Relative to `ASSETS_OUTPUT_ROOT`, or absolute inside it, but not the root itself. Created if missing |
| `cover`, `slides`, `closing` | yes | `slides` holds 1 to 18 entries, so a carousel has 3 to 20 slides. Each entry may carry an `icon_path` or an `image_path`, both inside `ASSETS_IMAGE_ROOTS`, not both |
| `handle` | no | The same on every slide, never filled in |
| `format` | no | The same on every slide, `4:5` by default |
| `pagination` | no | Pattern for content and closing slides with `{page}` and `{pages}`, default `{page}/{pages}`: `2/7`. `{page} von {pages}` gives `2 von 7`; an empty string leaves the pagination out. The cover counts as page 1 and shows the swipe arrow instead |
| `overwrite` | no | Default `false`: an existing `01.png`, `02.png`, … in the folder is an error |
| `icon_color` | no | `accent`, the bundle's accent color and the default, or `text`. Applies to every icon of the carousel and is sent only with a slide that has one |
| `bundle` | no | The bundle whose carousel templates render the slides. Without it, `ASSETS_BUNDLE`; without both the tool refuses before any request |
| `pdf` | no | Default `false`. Also saves `carousel.pdf` with all slides, one page each, rendered by `POST /v1/document`, for a LinkedIn document post. An existing `carousel.pdf` needs `overwrite` like a slide file. The images and icons of all slides count together against the service's `MAX_IMAGE_BYTES` there, which the PNGs alone never do |

Every slide, and the PDF when asked for, is rendered before the first file is written, so a
failure leaves the folder as it was and the error names the slide or the PDF. Files numbered beyond the new carousel, left from a
longer earlier run, are not deleted; the result lists them. The result carries the paths and one
JPEG contact sheet of all slides, four per row.

Both render tools carry the service's limits in their descriptions, so the model sees them
without reading these docs. Those lines mirror `app/schemas.py`; change both together. The tool
descriptions name no template: what a template is for and how to fill its fields belongs into the
template's `description`, which `list_templates` hands to the model.

## Configuration

Read from the process environment, never from `.env`.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ASSETS_API_KEY` | yes | | Sent as `Authorization: Bearer` |
| `ASSETS_BASE_URL` | no | `https://api.contentgun.de` | `http://` or `https://` with a host; a trailing `/` is dropped |
| `ASSETS_TIMEOUT_S` | no | `30` | Seconds per request, above 0. The service may wait 10 s for a slot and render for 15 s |
| `ASSETS_OUTPUT_ROOT` | yes | | Absolute, existing folder. Files are written only below it |
| `ASSETS_BUNDLE` | no | none | The bundle `render_carousel` uses when the call names none, such as `demo` |
| `ASSETS_IMAGE_ROOTS` | no | the output root | Absolute, existing folders images and icons may come from, separated by `:`. A relative `image_path` or `icon_path` resolves against the first |

A missing or invalid value stops the server at startup with a sentence naming the variable.

## Paths

Paths are resolved, symlinks included, before they are checked, so a link inside a root that
points outside it is refused like a path with `..`. Refused before any request is sent:

- An output path or folder outside `ASSETS_OUTPUT_ROOT`
- An output path that does not end in `.png`, or that names a folder
- An existing output file, or an existing slide file, without `overwrite`
- An image or icon outside every `ASSETS_IMAGE_ROOTS` entry, missing, or not a regular file, on any slide
- A card or slide with both an image and an icon

Files are written to a temporary file next to the target and moved into place, so a failure never
leaves half a PNG behind. The image or icon itself is only checked for being a file; format, size
and the SVG rules are the service's call, answered with `400` or `413`.

## Errors

Every failure reaches the model as an error result with one sentence it can act on:

| Cause | Message |
|-------|---------|
| Connection refused | Could not reach the base URL. Locally the service runs with `bin/development` |
| No answer in time | The base URL did not answer within the timeout |
| `401` | The service at the base URL rejected `ASSETS_API_KEY` |
| `400` | The service's sentence, plus that retrying the same request fails again |
| `413` | The service's sentence, plus a hint to use smaller images or icons, which a PDF counts together |
| `503`, `504` | The service's sentence, plus that retrying is safe |
| Any other status with the error envelope | The service's sentence |
| Anything else | Unexpected response from the base URL, with the status |

The key never appears in a message or a log line.

## Logs and troubleshooting

The desktop app collects the server's stderr in
`~/Library/Logs/Claude/mcp-server-contentgun-assets.log`. Logged are counts, template, format, headline
length, image and icon size and request id, never card text or the key.

| Symptom | Cause |
|---------|-------|
| The server does not appear or shows as failed | The log names the cause: a configuration sentence, or `ModuleNotFoundError` when the dependencies are missing or `args` does not point at the `mcp` folder |
| "Could not reach http://127.0.0.1:8010" | `bin/development` is not running |
| "rejected ASSETS_API_KEY" | The key belongs to no tenant of the service it talks to: it is neither in `API_KEYS` nor hashed in its tenants file |
| Changes to the configuration have no effect | The desktop app was not restarted |

## Development

| Module | Role |
|--------|------|
| `mcp/__main__.py` | Entry point when the folder is run |
| `mcp/config.py` | Environment to a frozen `Config`, validated at startup |
| `mcp/client.py` | HTTP calls to `/v1/templates`, `/v1/render` and `/v1/document`, every failure as `AssetsApiError`; shared with the remote server |
| `mcp/paths.py` | Path confinement and the atomic file write |
| `mcp/preview.py` | JPEG preview and the carousel contact sheet, with Pillow |
| `mcp/tools.py` | The three tools as plain async methods, independent of the SDK |
| `mcp/cards.py` | The limits, the carousel models and the request bodies, shared with the remote server |
| `mcp/server.py` | `MCPServer` wiring, tool descriptions, `main()` |
| `mcp/errors.py` | `AssetsError` and `ConfigError`, which both servers use |

The modules of the remote server are listed in [remote.md](remote.md#modules).

The folder is not a Python package: it has no `__init__.py`, which would shadow the SDK of the
same name, and its modules import each other flat, as `from config import ...`. pytest finds them
through `pythonpath` in `pyproject.toml`.

`bin/test` runs the tests in `tests/test_mcp_*.py` with the rest, without network and without
Chromium. `test_mcp_server.py` talks to the server through `mcp.Client`, once in-process and
once as the subprocess the desktop app would start, from a foreign working directory.

The constraints that are easy to break are listed under "Things that will bite you" in
`CLAUDE.md`.
