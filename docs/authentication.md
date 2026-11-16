# Authentication

All endpoints except `/health` require a bearer token.

```
Authorization: Bearer <key>
```

Every key belongs to a tenant, and a request is handled in the scope of the tenant whose key it
carries: it sees that tenant's bundles and the built-in ones, nothing else. A key passed as a query
parameter is never accepted, so keys stay out of access logs and proxies.

The keys come from one of two places, described in [Tenants](tenants/README.md):

- **`API_KEYS`** in `.env`: comma-separated keys in plain text, which make up the one tenant `default`. This is how a fresh clone runs.
- **`TENANTS_FILE`**: a TOML file with one table per tenant, holding the SHA-256 hashes of its keys, never the keys themselves.

The service hashes a presented key and compares it with every stored hash in constant time.

## Generating a key

For `API_KEYS`:

```
openssl rand -hex 32
```

For a tenants file, a key with the hash that goes into the file:

```
python3 scripts/new_api_key.py
```

## The internal key

`INTERNAL_KEY` is the key of a service that acts for every tenant, the remote MCP server. It sends
the tenant along:

```
Authorization: Bearer <INTERNAL_KEY>
X-Contentgun-Tenant: <tenant>
```

With a key of a tenant the header is ignored. Details in [Tenants](tenants/README.md#the-internal-key).

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `401` | `{"ok": false, "error": "Unauthorized."}` | Missing header, wrong scheme, or unknown key. The response carries `WWW-Authenticate: Bearer` |
| `400` | `{"ok": false, "error": "The internal key needs the header X-Contentgun-Tenant."}` | The internal key without the header |
| `400` | `{"ok": false, "error": "The header X-Contentgun-Tenant names no known tenant."}` | The internal key with a tenant that does not exist |
| `500` | `{"ok": false, "error": "API keys are not configured on the server."}` | No key at all: `API_KEYS` empty, no key in the tenants file, no `INTERNAL_KEY` |

A template of another tenant is not an authentication error: it is a `400` for an unknown
template, as if it did not exist.

## Notes

- **Server-side callers only.** The service is meant for n8n and other backends. A key in browser code would be public and the endpoint would become an open renderer.
- `DEBUG_ROUTES=true` additionally mounts unauthenticated routes: the overview, debug route and sample files under `/v1/debug`, plus `/docs` and `/openapi.json`. They are off by default and the service binds to `127.0.0.1` in development. Leave them off anywhere the service is reachable from outside the host.
- `API_KEYS` is empty in `.env.example` on purpose, so a copied-but-unedited configuration fails with a `500` instead of running on a publicly known key. For the same reason the hashes in `tenants.example.toml` are placeholders that stop the start.
- The logs name the tenant of a request, never a key or a hash.
