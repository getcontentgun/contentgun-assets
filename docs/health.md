# GET /health

Liveness probe. No authentication.

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | `{"ok": true, "version": "0.3.0", "browser": "ready", "templates": ["demo/card", "demo/card-image", "demo/carousel-closing", "demo/carousel-content", "demo/carousel-cover"]}` | Service is up and Chromium is connected. `templates` holds the ids of the built-in templates |
| `503` | `{"ok": false, "error": "Browser not ready."}` | Chromium is not connected |

## Example

```
curl -s http://127.0.0.1:8010/health
```

## Notes

- Startup blocks until Chromium is launched and fails loudly if it cannot be, so the service never accepts connections in a half-started state. A `503` therefore means the browser died while running or the service is shutting down.
- The next render after a crash relaunches Chromium automatically, so the probe can recover on its own.
- The probe is the only endpoint that works without a key, so it is safe to point a monitor at it. For the same reason it names no tenant and no template of one: `templates` lists only the templates of the built-in bundles. What a tenant can use, it learns from [`GET /v1/templates`](templates-list.md) with its key.
