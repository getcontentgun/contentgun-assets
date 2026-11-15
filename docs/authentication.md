# Authentication

All endpoints except `/health` require a bearer token.

```
Authorization: Bearer <key>
```

Keys are configured in `.env` as `API_KEYS`, comma-separated, and compared in constant time.
A key passed as a query parameter is never accepted, so keys stay out of access logs and proxies.

## Generating a key

```
openssl rand -hex 32
```

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `401` | `{"ok": false, "error": "Unauthorized."}` | Missing header, wrong scheme, or unknown key. The response carries `WWW-Authenticate: Bearer` |
| `500` | `{"ok": false, "error": "API keys are not configured on the server."}` | `API_KEYS` is empty in the environment |

## Notes

- **Server-side callers only.** The service is meant for n8n and other backends. A key in browser code would be public and the endpoint would become an open renderer.
- `DEBUG_ROUTES=true` additionally mounts unauthenticated routes: the overview, debug route and sample files under `/v1/debug`, plus `/docs` and `/openapi.json`. They are off by default and the service binds to `127.0.0.1` in development. Leave them off anywhere the service is reachable from outside the host.
- `API_KEYS` is empty in `.env.example` on purpose, so a copied-but-unedited configuration fails with a `500` instead of running on a publicly known key.
