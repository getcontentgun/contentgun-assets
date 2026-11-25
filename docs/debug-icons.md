# GET /v1/debug/icons

Shows the icon set of a bundle on one page, every icon with its name and tinted the way a card
tints it: every opaque part in one color. That is where an icon with a white area shows up as a
blob, which no startup check catches, see [Icons](bundles/README.md#icons).

Only mounted when `DEBUG_ROUTES=true`, and not authenticated, like the other debug routes.
[`GET /v1/debug`](debug-index.md) links the page under every bundle that has a set.

## Request

**Method:** `GET`

| Query parameter | Default | Description |
|-----------------|---------|-------------|
| `bundle` | none, required | The bundle whose set to show |
| `tenant` | the only tenant, when there is just one | Whose bundles to look in, see [Tenants](tenants/README.md) |

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | HTML | The icons, sorted by name; an empty page for a bundle without a set |
| `400` | `{"ok": false, "error": "..."}` | No `bundle`, or one the tenant cannot use, with the list of its bundles; a missing or unknown `tenant` as on the other debug routes |

```
open "http://127.0.0.1:8010/v1/debug/icons?bundle=acme-brand&tenant=acme"
```
