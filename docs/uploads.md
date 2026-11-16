# Uploads

An image or an icon can reach a render request as an upload instead of base64 in the body: the
caller asks for an upload URL, puts the file there with a plain `PUT` and names the upload in the
request. That keeps large images out of JSON and lets a client that cannot build base64 send a
file. Uploads need [storage](storage/README.md); without it both endpoints answer `501`.

1. `POST /v1/uploads` with a key returns an `upload_id` and an `upload_url`.
2. `PUT <upload_url>` with the raw bytes of the file, no key and no further header.
3. `"image_upload": "<upload_id>"` or `"icon_upload": "<upload_id>"` in [`POST /v1/render`](render.md#uploads) or on a slide of [`POST /v1/document`](document.md).

```
curl -sS -X POST -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8010/v1/uploads
curl -sS -T photo.jpg "<upload_url>"
```

## POST /v1/uploads

**Auth:** `Authorization: Bearer <key>`. No body.

| Status | Body | Description |
|--------|------|-------------|
| `200` | see below | A new upload URL |
| `401` | `{"ok": false, "error": "Unauthorized."}` | Missing or wrong key |
| `501` | `{"ok": false, "error": "Storage is not configured on this server, so uploads cannot be used."}` | No storage |

```json
{
  "ok": true,
  "upload_id": "up_Q2x8m0Wq1cR7nJb3TzVkLa5e",
  "upload_url": "https://files.contentgun.de/u/acme.up_Q2x8m0Wq1cR7nJb3TzVkLa5e.1795000000.Xr…",
  "expires_at": "2026-11-16T10:15:00Z",
  "max_bytes": 8388608
}
```

The URL is valid for 15 minutes and for one upload. It belongs to the tenant of the key: the
upload is only ever usable by that tenant.

## PUT /u/<token>

No key: the signed URL is the permission. The body is the file, as it is; `Content-Type` and
every other header are ignored, so `curl -T photo.jpg "<upload_url>"` is enough.

The service decides what the file is:

| File | Kind | Limit |
|------|------|-------|
| PNG, JPEG or WebP, by its first bytes | `image` | `MAX_IMAGE_BYTES`, 8 MB by default |
| SVG that meets the [icon rules](render.md#image-or-icon-on-a-content-slide) | `icon` | 256 KB, and never more than `MAX_IMAGE_BYTES` |

Anything else is refused. An upload stays available for `STORAGE_TTL_HOURS`, 24 hours by
default.

| Status | Body | Description |
|--------|------|-------------|
| `201` | see below | The upload is stored |
| `400` | `{"ok": false, "error": "..."}` | Empty, no PNG, JPEG, WebP or SVG, or an SVG that breaks an icon rule |
| `403` | `{"ok": false, "error": "The upload URL is not valid."}` | The URL was changed, or names no tenant of the service |
| `403` | `{"ok": false, "error": "The upload URL has expired. ..."}` | More than 15 minutes old |
| `409` | `{"ok": false, "error": "The upload URL has been used already. ..."}` | The URL took a file before |
| `413` | `{"ok": false, "error": "An upload must be at most 8 MB."}` | Over the limit, refused as soon as the size is known |
| `501` | `{"ok": false, "error": "..."}` | No storage |
| `503` | `{"ok": false, "error": "The file storage is not available, retry later."}` | The bucket did not answer |

```json
{
  "ok": true,
  "upload_id": "up_Q2x8m0Wq1cR7nJb3TzVkLa5e",
  "kind": "image",
  "content_type": "image/jpeg",
  "bytes": 482113,
  "expires_at": "2026-11-17T10:01:12Z"
}
```

## Notes

- **An upload belongs to its tenant.** An upload id of another tenant is an unknown upload, exactly like one that never existed.
- **An upload is used as what it is.** An image goes into `image_upload`, an icon into `icon_upload`; the other way round is a `400` that says which one to use.
- **The token carries the tenant, the upload id and the expiry,** signed with a key derived from `S3_SECRET_KEY`. New S3 credentials therefore invalidate the upload URLs handed out before, which live 15 minutes at most.
- The log lines name the tenant, the upload id, the kind and the size, never the URL.
