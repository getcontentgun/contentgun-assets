# POST /v1/document

Renders several slides into one PDF, one page per slide, for LinkedIn document posts, which show
every page of a PDF as a slide. The response body is the PDF itself, not JSON, or with
`"output": "url"` JSON that says where the PDF is stored.

## Request

**Method:** `POST`
**Content-Type:** `application/json`
**Auth:** `Authorization: Bearer <key>`

```json
{
  "slides": [
    { "template": "demo/carousel-cover", "headline": "Drei Fehler", "topic": "Automatisierung" },
    { "template": "demo/carousel-content", "headline": "Kein klares Ziel", "body": "Leg vorher fest, woran du Erfolg erkennst.", "pagination": "2/3" },
    { "template": "demo/carousel-closing", "headline": "Fazit", "cta": "Welcher Fehler passiert dir?", "pagination": "3/3" }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `slides` | array | yes | 1 to 20 slides in page order. Each one is exactly a request body for [`POST /v1/render`](render.md): `template`, `format` and the template's fields, with the same limits |
| `output` | string | no | `binary`, the default, answers with the PDF; `url` stores it and answers with JSON like [a render](render.md#output-as-a-url), with `pages` added. Needs [storage](storage/README.md). It belongs to the document: on a slide it is a `400` |

- **Every slide uses the same format.** A slide without `format` counts as its template's first format. Mixed formats are a `400`, because a PDF has one page size and LinkedIn shows a document in one aspect ratio.
- **Images and icons share one budget.** A slide carries an `image` or an `icon` like its render request does, or an `image_upload` or `icon_upload`, and all of them in a document together may be at most `MAX_IMAGE_BYTES` (8 MB by default) after decoding, or the whole request is a `413`, before any slide is decoded. Uploads count with their stored size, read before any of them is fetched. Fallback images do not count, so `demo/card-image` without an image still works and uses its own.
- **No other top-level keys.** Anything next to `slides` and `output` is a `400`.
- **Errors name the slide.** A field that fails validation is reported with its position counted from 0, as in `slides.2.headline: String should have at most 140 characters.`. An error from the template, such as an unknown template or a field it does not declare, is counted from 1, as in `Slide 3: Field 'cta' is not accepted by template 'demo/carousel-content'. …`.

Every slide is validated before the first one is rendered, so a broken request gets its `400`
without costing render time, even while the browser is down.

## How the pages are made

Each slide is rendered from the same HTML as its PNG, with the same fit-text pass and the same
fonts. Instead of a screenshot, Chromium prints it as a one-page PDF in the slide's size, with its
backgrounds, and the pages are joined in order. A slide of 1080 × 1350 px becomes a page of
810 × 1012.5 pt. The text stays text: sharp at any zoom, searchable, with the bundle's fonts
embedded. An icon does not stay vector: Chromium prints the colored silhouette as a bitmap at
300 dpi, about 900px square for a 240px icon, which LinkedIn shows as sharp as the text.

Each slide takes its own render slot and timeout, like a PNG render. A document of ten slides
therefore takes about as long as ten PNGs.

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | PDF bytes | `Content-Type: application/pdf`, plus `Content-Disposition: inline; filename="document_<format>_<id>.pdf"`, for example `document_4x5_<id>.pdf`, and `Cache-Control: no-store` |
| `200` | JSON | With `"output": "url"`: `url`, `content_type`, `width` and `height` of the format in px, `bytes`, `expires_at` and `pages` |
| `400` | `{"ok": false, "error": "..."}` | Invalid JSON, a slide that fails validation, mixed formats, a key next to `slides` and `output`, `output` on a slide, an unknown upload on a slide |
| `401` | `{"ok": false, "error": "Unauthorized."}` | Missing or wrong key |
| `413` | `{"ok": false, "error": "..."}` | The images and icons of all slides together exceed `MAX_IMAGE_BYTES`, or one icon exceeds 256 KB, reported with its slide |
| `500` | `{"ok": false, "error": "..."}` | Render failure, or a font of a slide's bundle that did not load while `STRICT_FONTS` is on |
| `501` | `{"ok": false, "error": "..."}` | `"output": "url"` or an upload on a slide without storage |
| `503` | `{"ok": false, "error": "..."}` | The browser is not available, the service is shutting down, a render slot stayed occupied longer than `QUEUE_TIMEOUT_MS`, or the bucket did not answer |
| `504` | `{"ok": false, "error": "Render timed out."}` | A slide exceeded `RENDER_TIMEOUT_MS` |

## Example (curl)

```
curl -sS --fail-with-body -o carousel.pdf \
    -X POST http://127.0.0.1:8010/v1/document \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"slides":[{"template":"demo/carousel-cover","headline":"Drei Fehler","topic":"Automatisierung"},{"template":"demo/carousel-content","headline":"Kein klares Ziel","body":"Leg vorher fest, woran du Erfolg erkennst.","pagination":"2/3"},{"template":"demo/carousel-closing","headline":"Fazit","cta":"Welcher Fehler passiert dir?","pagination":"3/3"}]}'
```

## Example (n8n)

1. The Code node that builds the slide bodies for the PNGs (see [Carousel](render.md#in-n8n)) returns them once more as a single item: `return [{ json: { slides: bodies } }]`.
2. An **HTTP Request** node sends it: method `POST`, URL `…/v1/document`, the Header Auth credential, body `={{ $json }}`, Options, Response **Format: File**. The node then holds the PDF as a binary property.
3. Publishing belongs to the flow: it uploads the PDF through LinkedIn's Documents API with HTTP Request nodes of its own, then creates the post with the document's URN.

## Notes

- **The pages look exactly like the PNGs.** Same templates, same limits and the same guarantee that text is never cut off, see [Text limits](render.md#text-limits).
- Each call renders fresh. Without `"output": "url"` the PDF exists only in the response.
