# Authentication

All endpoints require a bearer token, except `/health` and the two kinds of file URL, whose
permission is the URL itself, see [Signed and random URLs](#signed-and-random-urls).

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

`INTERNAL_KEY` is the key of a service that acts for every tenant, the
[remote MCP server](mcp/remote.md). It sends the tenant along:

```
Authorization: Bearer <INTERNAL_KEY>
X-Contentgun-Tenant: <tenant>
```

With a key of a tenant the header is ignored. Details in [Tenants](tenants/README.md#the-internal-key).

[`POST /v1/admin/reload`](admin-reload.md) takes the internal key alone, without the header: it
acts on the whole service. Every other key is a `401` there.

## Signed and random URLs

With [storage](storage/README.md) two kinds of URL work without a key, because the URL is the
permission:

- **`PUT /u/<token>`** takes one upload. The token names the tenant, the upload and an expiry 15 minutes ahead, signed with a key derived from `S3_SECRET_KEY`; a changed or expired token is a `403`. See [Uploads](uploads.md).
- **`GET /o/…`** serves a stored result. Its path holds 192 random bits, so it cannot be guessed, and it answers until the result expires. See [`GET /o/…`](files.md).

Hand these URLs only to whoever should upload or fetch the file.

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
