# POST /v1/admin/reload

Loads the tenants and their bundles again, without a restart: the tenants file, and the bundles
of the tenants from the bucket or from `BUNDLES_DIR`. Everything is validated before the service
switches, so a broken bundle leaves the tenants and bundles in use active.

## Request

**Method:** `POST`, no body.
**Auth:** `Authorization: Bearer <INTERNAL_KEY>`. No header `X-Contentgun-Tenant`: a reload is
for the whole service. A key of a tenant is a `401`, and so is every key while `INTERNAL_KEY` is
empty.

## What it reads

| | With storage and an empty `BUNDLES_DIR` | Otherwise |
|---|---|---|
| Bundles of the tenants | Copied from `templates/` of the bucket into a new local folder | Read from `BUNDLES_DIR` again |
| Tenants | Read from `TENANTS_FILE` again | the same |
| Built-in bundles | Read from `bundles/` again | the same |

`API_KEYS`, `INTERNAL_KEY` and the other settings come from the environment and need a restart.
Reloads run one after the other; a second request waits for the first.

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | see below | The new tenants and bundles are active |
| `401` | `{"ok": false, "error": "Unauthorized."}` | No internal key |
| `409` | `{"ok": false, "error": "Nothing was reloaded, the tenants and bundles in use stay active. <reason>"}` | A tenant or a bundle failed the [startup checks](tenants/README.md#startup-validation); the reason names the file, a bundle in the bucket by its key |
| `503` | `{"ok": false, "error": "The file storage is not available, retry later."}` | The bucket did not answer; nothing changed |

```json
{ "ok": true, "tenants": [{ "name": "acme", "templates": 11 }, { "name": "studio", "templates": 6 }] }
```

## Example

```
curl -sS -X POST -H "Authorization: Bearer $INTERNAL_KEY" http://127.0.0.1:8010/v1/admin/reload
```

## Notes

- **Requests that run during a reload finish with what they started with.** The local copy before the new one stays on disk until the next reload, so a request that started earlier can still read its templates.
- **Each instance reloads itself.** With several instances of the service, each one needs its own request.
