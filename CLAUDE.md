# CLAUDE.md

## What this is

The asset module of Contentgun: a FastAPI service that renders social media cards to PNG, and
carousels to one PDF. A request carries text, the service fills a Jinja2 HTML template, loads it
into headless Chromium and screenshots it. There is no AI in the rendering path and nothing is
stored: the PNG is the response body.

It replaced the Gemini image nodes in the contentgun n8n flows, which produced non-deterministic
cards with typography errors.

## Commands

```
.venv/bin/pip install -r requirements-dev.txt     # deps
.venv/bin/python -m playwright install chromium   # browser
bin/development                                   # checks, then uvicorn
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
bin/test                                          # ruff, then pytest
bin/test --suite -k render                        # pytest only
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
docker compose -f compose.dev.yaml up --build     # the service in a container
```

Startup prerequisites: `.env` exists with a non-empty `API_KEYS`, the bundles in `BUNDLES_DIR`
(`bundles/` by default) are valid, and Playwright's Chromium is installed. A broken bundle fails at
import time with a message naming the file; a missing Chromium fails when the lifespan starts.

## Architecture

| File | Role |
|------|------|
| `app/main.py` | App factory, lifespan (browser start/stop), middleware, router wiring |
| `app/config.py` | `Settings` from `.env`, the bundle directory among them |
| `app/auth.py` | Bearer dependency, constant-time comparison against every configured key |
| `app/errors.py` | `ApiError` and the handlers that turn everything into the JSON envelope |
| `app/request_id.py` | Per-request UUID: log context and `X-Request-Id` header |
| `app/formats.py` | Format enum and pixel dimensions |
| `app/fonts.py` | The font faces and checks of a bundle, validated from its `bundle.toml`, and their `@font-face` CSS with base64 data URIs |
| `app/hyphenation.py` | German break points as soft hyphens, plus the last-resort chopping |
| `app/images.py` | Images and icons: base64 decoding, format sniffing, SVG validation, size limits, data URIs, and the expat check at startup |
| `app/templating.py` | Bundle and template discovery and validation, one Jinja environment per bundle, `render_html()` |
| `app/rendering.py` | Resolves template, format, fields and image from a request, and turns render failures into API errors |
| `app/browser.py` | Chromium lifecycle, concurrency semaphore, screenshot and PDF print, font verification |
| `app/document.py` | Joins the one-page PDFs of `POST /v1/document` into one file with pypdf |
| `app/routers/` | One module per endpoint group |
| `bundles/<bundle>/` | A template bundle: `bundle.toml`, `fonts/`, `assets/` and one folder per template with `template.toml` + `template.html.j2` + `template.css`. Format in `docs/bundles/README.md` |
| `bundles/demo/_base/` | Shared skeleton and CSS of every template of the demo bundle. Not a template: the registry skips it because of the leading underscore |
| `mcp/` | Local stdio MCP server for the Claude desktop app, a client of the HTTP API. A plain folder, not a package; never deployed. See `docs/mcp/README.md` |
| `bin/test` | Linters and the test suite as stages, with a summary and `--lint`/`--suite`/`--bail` |
| `docker/assets.Dockerfile` | The image of the service, on Microsoft's Playwright image, without bundles. See `docs/docker.md` |
| `compose.dev.yaml` | Runs the image locally with `./bundles` mounted read-only and the configuration from `.env` |

`app/rendering.py` is shared by the render, document and debug routes on purpose: the browser
screenshots and prints exactly the HTML the debug route serves, so the design preview cannot drift
from the output.

## The demo bundle

`bundles/demo/` is the only bundle in this repository: a neutral design in slate and amber with
Space Grotesk headlines and Inter for every other text, whose design, font sources and measured
limits are in `bundles/demo/README.md`. The suite tests it, and every template of it extends its
`_base/`. Bundles of a brand or a customer live outside the repository, in a folder `BUNDLES_DIR`
names.

The field is called `handle` rather than by position, so moving it in a template does not make the
API lie. The handle is a plain request field: the service keeps no account directory and never
fills one in, so a card can only carry the identity its caller sent.

## Things that will bite you

- **Fonts must be data URIs, not `file://`.** After `page.set_content()` the document sits on an opaque origin. Font requests are CORS requests, so a `file://` URL is blocked and Chromium silently falls back to a system font. The card would still render, just wrong.
- **`document.fonts.load()` is called explicitly in the template.** Because the faces carry a `unicode-range`, a face that no visible character needs is never loaded. The bundle's check list is passed into the page as `font_checks`, so Python and JavaScript agree on what must be there.
- **The checks of a bundle must mirror what its CSS actually draws.** A check for a weight no rule uses passes on a substituted or synthesised face and stops meaning anything. The registry refuses to start when a check names a weight no face covers, but it cannot see what the CSS draws. When a template changes font, update the checks in its `bundle.toml`: in `demo`, every template draws Space Grotesk 700 for the headline and Inter 400 for every other text, so both are checked.
- **A template only gets the fonts of its own bundle.** The page embeds only that bundle's faces, and `capture()` hands only that bundle's checks to the browser, so a template that draws a family of another bundle renders in a fallback font, and with `STRICT_FONTS` fails with `500`.
- **A face of a variable font declares the full range of its weight axis.** `demo` declares Space Grotesk `300 700` and Inter `100 900`, the ranges the files really span. Narrow a declaration and a request for a weight outside it is silently clamped to its nearest end: the text turns lighter or bolder than the CSS asks, and every check still passes.
- **`document.fonts.check()` alone proves nothing.** It answers true for a family that has no `@font-face` at all, because the browser is content to substitute. `app/browser.py` therefore pairs it with a loaded `FontFace` of the same family; drop that pairing and the strict-font gate silently becomes a no-op.
- **The headline needs vertical padding.** Space Grotesk's inline box is 1.27em tall while `.headline` sets `line-height: 1.08`, so without `padding: 0.12em 0` the first and last line spill out of the h1's border box. That both shaves accents and makes `scrollHeight` permanently exceed the box, which pins the fit-text search to its 40px minimum on every card. A bundle with another headline font has to measure its own value.
- **The image and the icon are deliberately not template fields.** `resolve_fields()` caps every declared field at 200 characters, which no real image survives. Each travels as its own top-level request key, `image` or `icon`, is validated in `app/images.py` and reaches the template as the separate `image` or `icon` context value. Move either into `fields` and every request carrying one breaks. `icon_color`, by contrast, is a normal field of `carousel-content`, typed in `RenderRequest` so that only `accent` and `text` get through.
- **A caller's SVG is always an `<img>`, never inlined.** As an image Chromium runs no script in it and loads nothing it references; inlined into the page it would run its scripts and fetch its external references from the server's network. The tint is therefore an SVG filter on the `<img>`, `#tint` in `carousel-content`: flood with the color, composite `in` with `SourceAlpha`. Keep `color-interpolation-filters="sRGB"`, or the color drifts, and keep the filter's `<svg>` out of `display: none`: there the filter stops applying and the icon renders black while the endpoint still answers `200`. A CSS mask would tint as well, but it is a resource of its own that the readiness gate cannot wait for. In a PDF, Chromium prints the filtered icon as a 300 dpi bitmap.
- **Icons are parsed with the stdlib, which is only safe on expat 2.4.1 or newer.** Older expat expands nested entities without limit. `verify_xml_parser()` refuses to start on such a build, next to the font check; `ElementTree` resolves no external entities on any version.
- **The render and debug routes dump the request with `exclude_unset=True`.** `RenderRequest` types `subline` and `handle` as optional fields, so a plain `model_dump()` hands `resolve_fields()` a `subline: None` the caller never sent, and `resolve_fields()` rejects undeclared keys before it maps `None` to a default. Without the flag every template that does not declare `subline`, today the three carousel templates, answers every request with `400` "Field 'subline' is not accepted". An explicit `"subline": null` still gets through and is rightly rejected.
- **A fallback image is embedded as a data URI too, and cached.** The same opaque-origin rule that rules out `file://` for fonts rules it out for images, so `file_data_uri()` inlines the bytes; it is cached because a photo can be half a megabyte and never changes at runtime. A fallback that a template declares but that is missing from its bundle's `assets/`, or not a PNG, JPEG or WebP file, fails at startup: the registry runs every fallback through `file_data_uri()` once, next to the other template validation.
- **Every image is decoded before the page reports ready.** `_base/base.html.j2` chains `img.decode()` for every `<img>` of the page after the fonts, the backdrop of `card-image` included. A failed decode still marks the page ready, so a broken image gives a card without it instead of a 504; the broken `<img>` is removed, so it leaves no gap and does not switch on the content slide's graphic mode. Removing the chain lets the screenshot catch the card before the image is painted.
- **Hyphenation is done in Python, and the CSS must not fight it.** `app/hyphenation.py` inserts soft hyphens because Chromium's `hyphens: auto` relies on dictionaries that are present on macOS and absent in the Linux headless shell, which would make development and production break words differently. The templates therefore must not set `overflow-wrap: anywhere` or `break-word`: both turn every position into a break opportunity, and Chromium then prefers the fuller line over the offered hyphen, so the soft hyphens are silently ignored. What keeps an unbreakable run inside the card instead is `MAX_UNBROKEN`, which chops anything longer than 12 characters that has no break opportunity at all.
- **Every fitted field must go through `| hyphenate`, or `| highlight`, which hyphenates every part around the marked word.** The filter is also where `MAX_UNBROKEN` chops long runs, and since the templates may not set `overflow-wrap`, that chopping is the only thing that keeps an unbreakable run inside its box. A fitted field without the filter pins the fit-text search to its floor as soon as it holds a long unbroken run, and is clipped at the side.
- **The character limits and the minimum font sizes are one system.** The caps in `app/schemas.py` (headline 140, subline 200, handle and topic 60, pagination 16), and the 200 that `resolve_fields()` puts on every other field, are tight enough that running text never reaches a floor (`--headline-min`, `--subline-min`, `--body-min`, `--cta-min`), and that even the tallest text a field can hold still fits at its floor. That text is a run of wide capitals such as `W` at the full limit: `hyphenate()` chops it every 12 characters, and only below a certain size do chunks share a line, at 41px two of them in Space Grotesk 700 and at 24px three in Inter, which is where the floors of `demo` sit. Measured in the renderer, a 140-character headline of running text lands at 49px or more and a 200-character second field at 33px or more on every template of `demo` in every format it offers, and 140 × `W` lands between 41px and 45px without clipping. Clipping is therefore unreachable through the API today, short of text that is mostly literal newlines: each one costs a full line at any size, which no character limit accounts for. Raise a cap, raise a floor, shrink a box or switch to a wider font, and clipping becomes reachable — silently, because the endpoint still answers `200` with a cut-off card. `1:1` leaves the least room, which is why `demo` shrinks the gaps there and the cover's swipe button; `topic` is capped at 60 because the label is not fitted and a longer run of capitals would squeeze the cover's headline below its floor. The content slide's image or icon belongs to the system as well: with one, the headline and body boxes shrink so that `--icon-size` stays free with both fields at their limits, and running text in the headline still lands at 49px or more. Re-measure both formats when you touch any of these, with and without a graphic. The numbers are documented in `docs/render.md`, and `tests/test_text_limits.py` fails once a run of `W` at the limits no longer fits. A template whose design needs tighter caps sets them in the `[limits]` table of its `template.toml`, which `resolve_fields()` enforces on top of the typed ones; the `SHOWCASE` template in `tests/conftest.py` covers that, together with the `highlight` and `percent` filters and the `9:16` format, which no template of `demo` uses.
- **The readiness gate is `document.documentElement.dataset.ready`.** The screenshot waits for it. It is set only after the fonts resolved, every image decoded and the fit-text pass ran, across two animation frames. Removing the wait produces cards with unfitted text.
- **The graphic mode of `carousel-content` is one rule per format.** `:root:has(.graphic)` is exactly as specific as `:root[data-format="1:1"]`, so one shared rule would override the `1:1` values by source order; `template.css` therefore writes `:root[data-format="4:5"]:has(.graphic)` and its `1:1` twin. In that mode the headline box does not shrink: the image is the only stage item that gives way, otherwise the fit pass measures a squeezed headline box and picks a smaller headline than the text needs.
- **With a centered stage, `scrollHeight` misses overflow.** Half of an overflow goes above the stage's top edge, where `scrollHeight` does not count it, so `tests/test_text_limits.py` compares the rectangles of the stage's items with the stage instead.
- **Fit-text runs after `document.fonts.ready`.** Text metrics before that are the fallback font's and the binary search would pick the wrong size.
- **A `data-fit` element needs both `--<name>-min` and `--<name>-max`, in px.** `_base/base.html.j2` derives the names from the attribute value and skips an element whose pair is incomplete, with a `console.warn` that only the debug route's browser console ever shows: the text keeps its CSS size, can be clipped by its box, and the endpoint still answers `200`. The values are read with `parseFloat`, so `2rem` would silently mean 2px.
- **The images and icons of a document share one budget.** Without the old ban on images in `/v1/document`, a request could otherwise carry 20 images of `MAX_IMAGE_BYTES` each. The router adds up their estimated sizes before anything is decoded and answers `413` above `MAX_IMAGE_BYTES` for the whole document.
- **A PDF page needs screen media and printed backgrounds.** `render_pdf` switches the page to screen media and prints with `print_background=True`; without them every card comes out on white. `page_ranges="1"` keeps a slide on one page. A document is one print per slide, joined by `app/document.py`, because every slide is an HTML document of its own with its own `:root` variables, so they cannot share one print.
- **`--force-color-profile=srgb` is not optional on macOS.** Without it Chromium renders through the display profile and every color comes out shifted, the exact values the pixel tests check included.
- **The Playwright pin and the browser revision belong together.** `playwright==1.62.0` in `requirements.txt` decides which Chromium `playwright install` fetches, and `docker/assets.Dockerfile` builds on `mcr.microsoft.com/playwright/python:v1.62.0-noble`, whose browsers belong to the same version. Bump both together; `tests/test_docker.py` fails when they drift apart.
- **`.dockerignore` is a list of what the build may see.** Everything else is excluded, `.env` and the bundles included, so a file the Dockerfile is meant to copy has to be added there as well.
- **Compose sets `BUNDLES_DIR` although the image does.** `env_file: .env` passes every variable of `.env` into the container, so a `BUNDLES_DIR` there, a folder of the host, would replace `/bundles`; `environment:` wins over `env_file:`. The image holds no bundles at all: without the volume the container stops at startup.
- **Relaunching Chromium happens outside the per-render timeout.** A cold start inside the render budget would bill the launch to whichever request triggered it and could return 504 for a perfectly good template.
- **`template.css` is read on every request, the font CSS once at startup.** The CSS is a few kilobytes and the design loop depends on re-reading it; the base64 font blob is 250KB, never changes at runtime and is built by `load_font_set()` when the bundle loads. A changed font file needs a restart.
- **`_base/base.css` goes through Jinja.** The skeleton pulls it in with `{% include %}`, so `{{`, `{%` and `{#` in it are template syntax and either break the render or vanish from the CSS. It also reaches the page through Jinja's template cache, kept fresh by `auto_reload=True`, not through the uncached read that `template.css` gets; turn `auto_reload` off and every edit to `base.css` or a `.j2` file needs a restart.
- **A folder whose name starts with `_` or `.` is neither a bundle nor a template.** The registry skips it in the bundle root and inside a bundle, where it also skips `fonts/` and `assets/`; that is how `_base/` holds shared files without a `template.toml`. A template given such a name does not fail at startup: it is missing from `GET /v1/templates`, and every request for it is a `400` for an unknown template. Every other folder must follow the naming rule, `^[a-z0-9][a-z0-9-]*$`, or the start fails.
- **Every bundle has a Jinja environment of its own, and its loader stays inside the bundle.** `{% extends "_base/base.html.j2" %}` therefore names the skeleton of the template's own bundle. Jinja refuses `..` in a template name, and `BundleLoader` refuses a file whose real path leaves the bundle, as through a symlink; `inline_svg()` and `fallback_image` apply the same check to the bundle's `assets/`. A skeleton two bundles share is copied into both: there is no include across bundles.
- **Template ids are `<bundle>/<template>` everywhere.** The render, document and debug routes, `GET /v1/templates`, `/health` and `DEFAULT_TEMPLATE` speak only ids, and a bare template name is an unknown template. The PNG's file name joins the two with a hyphen, `demo-card_4x5_<id>.png`, because a `/` cannot be part of it.
- **`TemplateRegistry.load()` takes the directory and has no default.** The app passes `settings.bundles_dir`, the tests pass their own, and two registries on two directories share nothing, so a later directory per tenant needs no change in the registry.
- **The suite pins `BUNDLES_DIR` and `DEFAULT_TEMPLATE` in `tests/conftest.py`,** like `API_KEYS`, so a variable in the shell or in `.env` cannot change what it tests. The tests address templates through the id constants there, and build small bundles of their own with `write_bundle()`, which borrows a font file from the bundle under test.
- **A SIGKILLed uvicorn leaves Chromium behind.** Clean up with `pkill -f chromium_headless_shell`.
- **`mcp/` is a plain folder, not a package.** The desktop app runs the folder directly, which puts it on the import path, so its modules import each other flat (`from config import ...`). An `__init__.py` in it would make it a package named `mcp` that shadows the SDK of the same name; without one, Python prefers the installed SDK. Its module names must therefore not collide with `app`, `tests` or an installed package, and `pyproject.toml` keeps `mcp` third-party for ruff and puts the folder on pytest's path.
- **stdout belongs to the MCP protocol.** Nothing in `mcp/` may print: a stray line corrupts the stream and the desktop app drops the server. `main()` logs to stderr, which the app collects in `~/Library/Logs/Claude/mcp-server-contentgun-assets.log`.
- **`mcp/` must not import from `app`.** `app.config` reads this repository's `.env` and `app.main` loads the bundles at import time; the MCP server runs elsewhere and talks to the service over HTTP only.
- **`render_carousel` needs a bundle,** from its `bundle` parameter or `ASSETS_BUNDLE`, and refuses before any request without one. It renders `<bundle>/carousel-cover`, `<bundle>/carousel-content` and `<bundle>/carousel-closing`, so a bundle offers carousels by having templates of these names. The tool descriptions name no template; hints for a single template belong into its `description`.
- **`MCPServer.call_tool()` raises in-process.** A `ToolError` becomes an `is_error` result only over the protocol, so the server tests go through `mcp.Client`, not through `call_tool()` directly.
- **The limits in the MCP tool descriptions mirror `app/schemas.py`.** The model never reads the docs, so `LIMITS` in `mcp/server.py` repeats every cap; `tests/test_mcp_server.py` fails when the two drift apart.
- **Python 3.11 is the floor.** Development runs 3.13. `tomllib` is the newest feature in use, and ruff's `target-version` is `py311` so the `UP` rules cannot introduce newer syntax.

## Adding a template

1. Create `bundles/<bundle>/<name>/` with `template.toml` (`name` must equal the directory name), `template.html.j2` and `template.css`. The name follows `^[a-z0-9][a-z0-9-]*$`; a leading `_` marks shared files such as `_base/`. A new bundle also needs a `bundle.toml` with its font faces and checks and its fonts in `fonts/`, see `docs/bundles/README.md`.
2. Declare `formats`, `fields`, `required`, `[defaults]` and `[samples]`. The registry validates that `required`, `defaults` and `samples` only mention declared fields. `[samples]` is the preview content of the debug route and never exposed by the API; it must give every required field a non-blank value, or the registry refuses to start. A default does not count, because the debug route validates the samples through the request model, which demands a headline before any default applies. `image = true` additionally lets the template take the request's image; without it an `image` in the request is a `400`. `fallback_image = "<file>"` names an image file in the bundle's `assets/` that is used when the request sends none. `icon = true` lets the template take an SVG `icon` the same way; such a template may not declare a fallback image, or a card with an icon would carry the fallback image as well. `[limits]` caps declared fields below the service-wide limits, from 1 to 200, and the samples must respect them. The template gets the fonts of its bundle; there is no font setting per template.
3. The template receives `width`, `height`, `format`, `format_token`, `fields`, `image`, `icon`, `css`, `font_css`, `font_checks` and `fit_text`. Colors live in the bundle's CSS. In `demo`, extend `_base/base.html.j2`, as every template there does: fill `{% block stage %}`, and where needed `{% block meta_end %}` for the right end of the header row, `{% block backdrop %}` for a full-bleed image and `{% block footer %}` for a row under the stage. Put every fitted element alone in a `.fit-box` wrapper whose `max-height` is the space it gets, mark the element `data-fit="<name>"` and define `--<name>-min` and `--<name>-max`. `template.css` comes after `base.css`, so it overrides the shared rules and the `--color-*` variables every color goes through, and `:root[data-format="1:1"]` scopes a value to one format. A skeleton of your own needs the same readiness gate, font loading and fit-text pass.
4. `GET /v1/templates` picks it up at the next start, and `POST /v1/render` accepts its declared fields. No code change, but every field that is new to the API gets a row in the parameter table of `docs/render.md`, with the templates that declare it: every element on a card is a request parameter, whether or not `RenderRequest` types it.
5. Look at it with the debug route, `/v1/debug/html?template=<bundle>/<name>`, or pick it from the overview at `/v1/debug`, which links every template in every format and variant. The debug route takes every declared field as a query parameter on top of its sample. It calls `TemplateRegistry.render_html`, the same function the screenshot uses.

Logos go into the bundle's `assets/`; `inline_svg("file.svg")` is available as a Jinja global
and inlines the SVG from there, so it stays crisp and can inherit `currentColor`.

## Conventions

- Identifiers, comments, log lines and `docs/` are English. There is no user-facing German text in this service; German only appears as sample content.
- Error envelope: `{"ok": false, "error": "A full sentence."}`. Error strings end with a period.
- Versioned paths `/v1/<resource>`. `/health` stays unversioned.
- Bearer only. A key in a query parameter is never accepted.
- Dependencies are pinned with `==` in `requirements.txt`; dev-only tools live in `requirements-dev.txt`, the MCP server's in `requirements-mcp.txt`, which `requirements-dev.txt` includes.
- Never log the rendered text or the key. Template name, format and headline length are fine.
- Changing an endpoint means updating the table in `docs/README.md` and the endpoint's own page.
- A feature beyond the HTTP API gets a folder of its own under `docs/`, such as `docs/mcp/`: its implementation plan while it is being built, its documentation as `README.md` once it exists. The API reference stays in `docs/` itself, one page per endpoint.
- Commits follow Conventional Commits (`feat:`, `fix:`, `chore:`).
