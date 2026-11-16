# GET /o/…

Serves a result that [`POST /v1/render`](render.md#output-as-a-url) or
[`POST /v1/document`](document.md) stored with `"output": "url"`. The URL comes from their answer
and is used as it is:

```
https://files.contentgun.de/o/<tenant>/<random>/<file name>
```

No key: the 192 random bits of the URL are the permission, so a URL can go to whoever needs the
file, a publishing API that fetches images from a public address for example. Needs
[storage](storage/README.md).

## Request

**Method:** `GET`, or `HEAD` for the headers alone.

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | PNG or PDF bytes | Streamed from the bucket |
| `404` | `{"ok": false, "error": "Not found."}` | Unknown, expired, a path in another form, or no storage on the server |
| `503` | `{"ok": false, "error": "The file storage is not available, retry later."}` | The bucket did not answer |

A `200` carries:

| Header | Value |
|--------|-------|
| `Content-Type` | `image/png` or `application/pdf`, from the file name, never from the bucket |
| `Content-Length` | The size of the file |
| `Content-Disposition` | `inline; filename="<file name>"` |
| `Cache-Control` | `public, max-age=<seconds until it expires>, immutable` |
| `X-Content-Type-Options` | `nosniff` |

## Example

```
curl -sS -o card.png "https://files.contentgun.de/o/acme/k3E…/demo-card_4x5_1a2b3c4d.png"
```

## Notes

- A result is available for `STORAGE_TTL_HOURS`, 24 hours by default; after that its URL is a `404`, even before the lifecycle rule of the bucket deletes it.
- Only results are served: a URL can never reach an upload or a bundle in the bucket.
