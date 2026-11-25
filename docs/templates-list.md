# GET /v1/templates

Lists the bundles and templates the calling tenant can use, with the supported formats, fields
and defaults: its own bundles and the built-in ones, or the ones its bundle list names. Use it to
discover what `POST /v1/render` accepts.

## Request

**Method:** `GET`
**Auth:** `Authorization: Bearer <key>`

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | see below | Template list |
| `401` | `{"ok": false, "error": "Unauthorized."}` | Missing or wrong key |

```json
{
  "ok": true,
  "bundles": [
    {
      "name": "demo",
      "description": "A neutral demo bundle: slate cards with an amber accent, Space Grotesk headlines, Inter text and JetBrains Mono code."
    }
  ],
  "templates": [
    {
      "id": "demo/card",
      "bundle": "demo",
      "name": "card",
      "description": "Slate card: an amber bar above a large left-aligned headline, a subline under it, and the handle in the header row.",
      "formats": [{ "format": "4:5", "width": 1080, "height": 1350 }],
      "fields": ["headline", "subline", "handle"],
      "required": ["headline"],
      "defaults": { "subline": "", "handle": "" },
      "limits": {},
      "accepts_image": false,
      "accepts_icon": false,
      "accepts_code": false,
      "icons": []
    },
    {
      "id": "demo/card-image",
      "bundle": "demo",
      "name": "card-image",
      "description": "Card on a full-bleed background image under a slate gradient: the headline and subline at the bottom, the handle in the header row. Without an image it uses the bundle's fallback image.",
      "formats": [{ "format": "4:5", "width": 1080, "height": 1350 }],
      "fields": ["headline", "subline", "handle"],
      "required": ["headline"],
      "defaults": { "subline": "", "handle": "" },
      "limits": {},
      "accepts_image": true,
      "accepts_icon": false,
      "accepts_code": false,
      "icons": []
    },
    {
      "id": "demo/carousel-closing",
      "bundle": "demo",
      "name": "carousel-closing",
      "description": "Last carousel slide: an amber bar, the resolution headline, the call-to-action question, and the header row with handle and pagination.",
      "formats": [
        { "format": "4:5", "width": 1080, "height": 1350 },
        { "format": "1:1", "width": 1080, "height": 1080 }
      ],
      "fields": ["headline", "cta", "pagination", "handle"],
      "required": ["headline"],
      "defaults": { "cta": "", "pagination": "", "handle": "" },
      "limits": {},
      "accepts_image": false,
      "accepts_icon": false,
      "accepts_code": false,
      "icons": []
    },
    {
      "id": "demo/carousel-content",
      "bundle": "demo",
      "name": "carousel-content",
      "description": "Carousel content slide: an amber bar, the headline, body text, an optional image or icon under it, and the header row with handle and pagination. An icon is drawn in amber, or with icon_color 'text' in the text color; an image is shown as sent.",
      "formats": [
        { "format": "4:5", "width": 1080, "height": 1350 },
        { "format": "1:1", "width": 1080, "height": 1080 }
      ],
      "fields": ["headline", "body", "pagination", "handle", "icon_color"],
      "required": ["headline"],
      "defaults": { "body": "", "pagination": "", "handle": "", "icon_color": "accent" },
      "limits": {},
      "accepts_image": true,
      "accepts_icon": true,
      "accepts_code": false,
      "icons": []
    },
    {
      "id": "demo/carousel-cover",
      "bundle": "demo",
      "name": "carousel-cover",
      "description": "First carousel slide: the topic in an amber label, the hook headline, a subline, the handle in the header row and an amber swipe button at the bottom right.",
      "formats": [
        { "format": "4:5", "width": 1080, "height": 1350 },
        { "format": "1:1", "width": 1080, "height": 1080 }
      ],
      "fields": ["headline", "topic", "subline", "handle"],
      "required": ["headline"],
      "defaults": { "topic": "", "subline": "", "handle": "" },
      "limits": {},
      "accepts_image": false,
      "accepts_icon": false,
      "accepts_code": false,
      "icons": []
    },
    {
      "id": "demo/code",
      "bundle": "demo",
      "name": "code",
      "description": "Code card: an amber bar, the headline, and under it the code in a dark window with an optional file name in the window's bar, highlighted in the bundle's colors. The code comes as code with its language, a short name such as python, javascript or bash, and optionally line_numbers and highlight_lines. At most 14 lines of 48 columns, so cut the snippet down to the lines that make the point.",
      "formats": [
        { "format": "4:5", "width": 1080, "height": 1350 },
        { "format": "1:1", "width": 1080, "height": 1080 }
      ],
      "fields": ["headline", "filename", "handle", "pagination"],
      "required": ["headline"],
      "defaults": { "filename": "", "handle": "", "pagination": "" },
      "limits": { "headline": 60 },
      "accepts_image": false,
      "accepts_icon": false,
      "accepts_code": true,
      "icons": []
    },
    {
      "id": "demo/quote",
      "bundle": "demo",
      "name": "quote",
      "description": "Quote card for testimonials, customer voices and quotes from talks: an amber quotation mark, an optional headline as the hook, the quote, and under it the author with role or source and an optional round portrait from the image. Send the quote without quotation marks; the card draws its own.",
      "formats": [
        { "format": "4:5", "width": 1080, "height": 1350 },
        { "format": "1:1", "width": 1080, "height": 1080 }
      ],
      "fields": ["headline", "quote", "author", "role", "handle", "pagination"],
      "required": ["quote"],
      "defaults": { "headline": "", "author": "", "role": "", "handle": "", "pagination": "" },
      "limits": { "headline": 80 },
      "accepts_image": true,
      "accepts_icon": false,
      "accepts_code": false,
      "icons": []
    }
  ]
}
```

## Example

```
curl -s -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8010/v1/templates
```

## Notes

- The list depends on the key: every tenant gets its own, see [Tenants](tenants/README.md). The example above is what the one tenant of a fresh clone sees, the built-in bundle `demo`.
- Bundles and templates are discovered at startup, the built-in ones in `bundles/` and those of the tenants in `BUNDLES_DIR`, see [Template bundles](bundles/README.md). A new template folder appears here without a code change, and `POST /v1/render` accepts its declared fields without one either. A folder whose name starts with `_`, such as `_base/`, holds shared files and is not listed.
- `id` is what a request names in `template`: the bundle and the template name, joined by `/`. `bundles` lists every bundle with its description, sorted by name.
- The templates are sorted by id. The first entry of a template's `formats` is the one the render uses when the request omits `format`.
- `defaults` are the template's own fallbacks and apply whenever the request omits a field.
- The `[samples]` table in a `template.toml` is not part of this response. It is preview content for [`GET /v1/debug/html`](debug-html.md), not a fallback the render endpoint ever uses.
- `fields` is the allow-list for the render request. Any other field is a `400`.
- `accepts_image` says whether the template takes the `image` parameter of the render request, `accepts_icon` whether it takes `icon`, and `accepts_code` whether it takes `code`, which such a template requires. All three are capabilities, not fields, so they do not appear in `fields`. `icons` lists, sorted, the names a render request may send as `icon_name` to a template that takes icons: the icon set of its bundle, see [Icons](bundles/README.md#icons). `demo` has none, so its lists are empty. A template that takes an image may also declare a fallback image from its bundle's `assets/`, which the render uses when the request sends none; one that takes an icon may not, or a card with an icon would carry the fallback image as well.
- `limits` holds the character limits a template sets on top of the service-wide ones, from the `[limits]` table of its `template.toml`; in the `demo` bundle `demo/quote` and `demo/code` set one each, 80 and 60 characters for their headline. A field not listed keeps the service-wide limit: headline 140, subline 200, handle, topic, author and filename 60, role 80, pagination 16, every other field 200.
