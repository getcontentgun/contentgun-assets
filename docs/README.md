# API reference

Base URL in development: `http://127.0.0.1:8010`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | [`/health`](health.md) | no | Liveness probe including browser and template state |
| `GET` | [`/v1/templates`](templates-list.md) | yes | Lists the bundles and their templates, with formats and fields |
| `POST` | [`/v1/render`](render.md) | yes | Renders a card and returns the PNG |
| `POST` | [`/v1/document`](document.md) | yes | Renders several slides into one PDF, one page per slide, for LinkedIn |
| `POST` | [`/v1/uploads`](uploads.md) | yes | Hands out a signed URL for one upload of an image or an icon (storage only) |
| `PUT` | [`/u/<token>`](uploads.md#put-utoken) | the URL | Takes the upload (storage only) |
| `GET` | [`/o/…`](files.md) | the URL | Serves a result stored with `"output": "url"` (storage only) |
| `POST` | [`/v1/admin/reload`](admin-reload.md) | internal key | Loads the tenants and their bundles again |
| `GET` | [`/v1/debug`](debug-index.md) | no | Links every template, format and variant into the debug route (development only) |
| `GET` | [`/v1/debug/html`](debug-html.md) | no | Returns the template HTML for design work (development only) |
| `GET` | [`/v1/debug/icons`](debug-icons.md) | no | Shows the icon set of a bundle as a card tints it (development only) |
| `GET` | [`/v1/debug/samples/…`](debug-index.md#sample-files) | no | The sample image and icon the overview's variants use (development only) |

See [authentication.md](authentication.md) for the key handling. Every key belongs to a tenant,
and every request sees only the bundles of its tenant and the built-in ones, see
[tenants/README.md](tenants/README.md). Templates come from template bundles, described in
[bundles/README.md](bundles/README.md), and are addressed as `<bundle>/<template>`. Results as
URLs, uploads and bundles from a bucket need storage, see [storage/README.md](storage/README.md).
An image or icon comes as base64 in the body, as an upload, or as an `https` URL that the service
fetches itself, see [render.md](render.md#images-and-icons-from-a-url); an icon also by name from
the set of its bundle, see [render.md](render.md#icons-of-a-bundle).
Running the service in a container is described in [docker.md](docker.md). Two MCP servers are
clients of this API: the local one for the Claude desktop app, documented in
[mcp/README.md](mcp/README.md), and the remote one that claude.ai connects to, with a sign-in by
mail, documented in [mcp/remote.md](mcp/remote.md).

All error responses use the same envelope:

```json
{ "ok": false, "error": "Unknown template 'nope'. Available: demo/card, demo/card-image, demo/carousel-closing, demo/carousel-content, demo/carousel-cover, demo/code, demo/quote." }
```

On success the render endpoint answers with the raw PNG, the document endpoint with the raw PDF,
both with JSON instead for `"output": "url"`, `/o/…` with the stored file, and the debug routes
with HTML or the sample files. Every other response is JSON.

Every response carries an `X-Request-Id` header, and the same id appears in the server log lines
for that request.
