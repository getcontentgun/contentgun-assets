# GET /v1/debug/html

Returns the rendered template as HTML instead of a screenshot. This is the design loop: run the
service with `--reload`, open the URL in Chrome at a 1080px wide viewport, edit `template.css`,
reload.

Only mounted when `DEBUG_ROUTES=true`. Not authenticated, because it is meant to be opened in a
browser; keep the service bound to `127.0.0.1` when it is on.

The overview at [`GET /v1/debug`](debug-index.md) links this route for every template, format and
variant, so most of the time there is no URL to type.

## Request

**Method:** `GET`

| Query parameter | Default | Description |
|-----------------|---------|-------------|
| `tenant` | the only tenant, when there is just one | Whose templates to preview. With several tenants it is required, see [Tenants](tenants/README.md) |
| `template`, `format` | as in render, the tenant's default template included | `template` takes a template id, `<bundle>/<template>`, of a template the tenant can use |
| `image_url` | the template's fallback image | Image for a template that accepts one, as a URL that the browser loads. Ignored by templates without the capability |
| `icon_url` | none | SVG icon for a template that accepts one, as a URL that the browser loads, for example from jsDelivr. Ignored by templates without the capability. Together with `image_url` it is a `400`, as on the render route |
| `code`, `language`, `line_numbers`, `highlight_lines` | the `[code_sample]` table of the template's `template.toml` | Code for a template with `code = true`, checked as in render. `highlight_lines` takes line numbers separated by commas, such as `3,4`, and empty for none; `line_numbers` takes `true` or `false`. Other `code` drops the highlighted lines of the sample, which belong to the sample's lines. A template without code answers any of them with a `400` |
| `fit` | `1` | `0` disables the fit-text script, so the CSS font sizes apply unchanged |
| any field the template declares | the field's value in the `[samples]` table of the template's `template.toml`, else its default | The same limits as in the render request: `headline` up to 140 characters, `handle`, `topic` and `author` up to 60, `role` up to 80, `pagination` up to 16, every other field up to 200, and whatever tighter limits the template sets in `[limits]`. Empty removes an optional element |

Every query parameter other than the ten above is taken as a field. One the template does not
declare, such as a `subline` left over after switching to `demo/carousel-content`, is a `400`
listing the accepted fields, exactly as on the render route.

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | HTML | The exact markup the screenshot is taken from |
| `400` | `{"ok": false, "error": "..."}` | Same length limits and field resolution as the render endpoint, including a field the template does not declare, and an `image_url` next to an `icon_url`. Also code the template does not take or cannot show, and a `highlight_lines` that is not a list of numbers. Also a missing `tenant` while there are several, an unknown one, and a template the tenant cannot use |
| `404` | `{"ok": false, "error": "Not found."}` | `DEBUG_ROUTES` is off |

## Example

```
open "http://127.0.0.1:8010/v1/debug/html?headline=Kurz%20und%20klar&subline=Zwei%20Zeilen%20reichen"
```

A carousel content slide in `1:1`, with the sample copy and `3 von 7` as its pagination:

```
open "http://127.0.0.1:8010/v1/debug/html?template=demo/carousel-content&format=1:1&pagination=3%20von%207"
```

The same slide with a Bootstrap icon under the text, in the accent color and then in the text
color:

```
open "http://127.0.0.1:8010/v1/debug/html?template=demo/carousel-content&icon_url=https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/icons/lightbulb.svg"
open "http://127.0.0.1:8010/v1/debug/html?template=demo/carousel-content&icon_url=https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/icons/lightbulb.svg&icon_color=text"
```

A card on a local photo, served from any folder with `python3 -m http.server 8020`:

```
open "http://127.0.0.1:8010/v1/debug/html?template=demo/card-image&image_url=http://127.0.0.1:8020/photo.jpg"
```

## Notes

- The HTML comes from the same code path as the screenshot, so what the browser shows and what Chromium captures cannot drift apart.
- **The defaults are preview content, not API defaults.** `[samples]` exists for this route only: `GET /v1/templates` does not report it, and the render endpoint never falls back to it. `template.toml` is read at startup, so a changed sample needs a restart, which `bin/development` does on its own when a `.toml` file changes.
- **Here the designer's browser loads `image_url` and `icon_url`, not the service.** The render endpoint takes parameters of the same names but fetches the file itself, under [the rules for URLs](render.md#images-and-icons-from-a-url): `https` only, and never an address of the network the service runs in. This route returns HTML and never involves the service's Chromium or its fetching, so it takes any URL the browser can load, a local `http://127.0.0.1:8020/photo.jpg` included, which the render endpoint refuses. Without `image_url` the page falls back to the template's own image, exactly as the screenshot does. An `icon_url` skips the SVG checks of the render endpoint, so an icon that previews fine can still be refused there.
- `template.css` is read from disk on every request, so a CSS edit needs only a browser reload, not a service restart. Edits to the `.j2` files, those under a bundle's `_base/` included, are picked up the same way, through Jinja's auto-reload.
- The page is about 250KB because the fonts are embedded as data URIs. That is expected.
- Never enable this route on a publicly reachable deployment. It renders arbitrary text without a key.
