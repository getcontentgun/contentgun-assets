# POST /v1/render

Renders one social media card and returns it as a PNG. The response body is the image itself,
not JSON. With `"output": "url"` the service stores the PNG instead and answers with JSON that
says where, see [Output as a URL](#output-as-a-url).

## Request

**Method:** `POST`
**Content-Type:** `application/json`
**Auth:** `Authorization: Bearer <key>`

### Parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `headline` | string | per template | Up to 140 characters, or fewer where a template sets a limit of its own. Required by every template that lists it in `required`, as every template of `demo` except `demo/quote` does; `GET /v1/templates` reports the required fields of each template. A literal newline forces a line break |
| `subline` | string | no | Up to 200 characters. Omitted or empty removes the element. Declared by `demo/card`, `demo/card-image` and `demo/carousel-cover` |
| `format` | string | no | `4:5` (1080x1350) on every template, `1:1` (1080x1080) on the carousel templates. The service also knows `9:16` (1080x1920) and `16:9` (1920x1080) for templates that declare them. Defaults to the template's first format |
| `template` | string | no | A template id, `<bundle>/<template>`, see [Template bundles](bundles/README.md). Defaults to `DEFAULT_TEMPLATE` from the environment (`demo/card`). A name without its bundle is an unknown template |
| `handle` | string | no | In the header row at the top of the card, for example `@contentgun`. Up to 60 characters. Omitted or empty removes the element |
| `topic` | string | no | The label above the hook, in capitals. Up to 60 characters. Omitted or empty removes the element. Declared by `demo/carousel-cover` |
| `body` | string | no | The text under the headline of a content slide. Up to 200 characters. Omitted or empty removes the element. Declared by `demo/carousel-content` |
| `cta` | string | no | The open question under the headline of the closing slide. Up to 200 characters. Omitted or empty removes the element. Declared by `demo/carousel-closing` |
| `quote` | string | per template | The quote itself, without quotation marks: the card draws its own. Up to 200 characters. Required by `demo/quote`, see [Quote card](#quote-card) |
| `author` | string | no | Who said the quote. Up to 60 characters. Omitted or empty removes the element. Declared by `demo/quote` |
| `role` | string | no | The author's role, company or the source of the quote, such as a talk. Up to 80 characters. Omitted or empty removes the element. Declared by `demo/quote` |
| `pagination` | string | no | Free text at the right end of the header row, shown exactly as sent, for example `"3/7"`, `"3 von 7"` or `"3"`. Up to 16 characters, never wrapped. A JSON number is a `400`. Omitted or empty removes the element. Declared by `demo/carousel-content`, `demo/carousel-closing` and `demo/quote`, see [Pagination](#pagination) |
| `image` | string | no | Base64 image for `demo/card-image`, `demo/carousel-content` and `demo/quote`, with or without a `data:image/...;base64,` prefix. PNG, JPEG or WebP, up to `MAX_IMAGE_BYTES` (8 MB by default) after decoding. `demo/card-image` uses it as its background and falls back to a fallback image of its own without one; `demo/carousel-content` shows it under the text, see [Image or icon on a content slide](#image-or-icon-on-a-content-slide); `demo/quote` crops it to a round portrait next to the author |
| `icon` | string | no | Base64 SVG pictogram for `demo/carousel-content`, with or without a `data:image/svg+xml;base64,` prefix, up to 256 KB after decoding. Drawn under the text as a silhouette in one color. Never together with `image`. See [Image or icon on a content slide](#image-or-icon-on-a-content-slide) for what an SVG has to meet |
| `icon_color` | string | no | The color of the icon: `accent`, the amber `#f2b33d` and the default, or `text`, the off-white text color `#f3efe6`. Anything else is a `400`. Without an icon it has no effect. Declared by `demo/carousel-content` |
| `image_upload` | string | no | The id of an uploaded image, in place of `image`, see [Uploads](#uploads). Needs storage |
| `icon_upload` | string | no | The id of an uploaded SVG icon, in place of `icon`. Needs storage |
| `image_url` | string | no | An `https` URL of the image, in place of `image`, up to 2048 characters. The service fetches it itself, see [Images and icons from a URL](#images-and-icons-from-a-url) |
| `icon_url` | string | no | An `https` URL of the SVG icon, in place of `icon`, fetched the same way |
| `output` | string | no | `binary`, the default, answers with the PNG; `url` stores it and answers with JSON, see [Output as a URL](#output-as-a-url). Needs storage |

A template of another bundle may declare fields of its own: `GET /v1/templates` lists them, and
they take up to 200 characters unless the template sets a tighter limit. A field the selected
template does not declare is rejected with `400`, listing the accepted fields, even when its value
is empty or `null`; so is a field that is not a string.

### Images and icons

`image` and `icon` are not template fields but capabilities. Only a template whose
`template.toml` sets `image = true` accepts an image, today `demo/card-image`,
`demo/carousel-content` and `demo/quote`, and only one that sets `icon = true` accepts an icon, today
`demo/carousel-content`. Sending either to a template without the capability is a `400` rather
than a silently ignored field, and `GET /v1/templates` reports the capabilities as
`accepts_image` and `accepts_icon`. A request carries an image or an icon, never both: both is a
`400`, and an empty string counts as not sent.

Each of them comes from one of three sources: base64 in the body (`image`, `icon`), an
[upload](#uploads) (`image_upload`, `icon_upload`) or a [URL](#images-and-icons-from-a-url)
(`image_url`, `icon_url`). Two sources for the same graphic are a `400`, and so is an image from
any source next to an icon from any source.

`demo/card-image` places the image full-bleed, cropped to the format with `object-fit: cover`,
under a slate gradient that darkens towards the bottom, where its text sits. The gradient is part
of the design, not a request parameter, so the same image always produces the same card.

A template may declare a fallback, as `fallback_image` in its `template.toml`, naming a file in
the `assets/` folder of its bundle. `demo/card-image` uses `backdrop.jpg` of the `demo` bundle, a
gradient drawn by `scripts/make_demo_backdrop.py`, so a request without an `image` renders on it
instead of failing. A flow that stops sending images therefore keeps producing usable cards — and
keeps doing so silently, so the `image_bytes` value in the log line is what tells you whether the
card carried a real image. The log line carries `icon_bytes` the same way.

### Uploads

With [storage](storage/README.md) an image or an icon can come from an upload instead of base64
in the body: `POST /v1/uploads` hands out an upload URL, the caller puts the file there with a
plain `PUT`, and the request names it by its id, as described in [Uploads](uploads.md):

```json
{ "template": "demo/card-image", "headline": "Hallo", "image_upload": "up_Q2x8m0Wq1cR7nJb3TzVkLa5e" }
```

- **An upload is used as what it is.** An uploaded PNG, JPEG or WebP goes into `image_upload`, an uploaded SVG into `icon_upload`; the other way round is a `400` that names the right one.
- **An upload belongs to its tenant** and lives for `STORAGE_TTL_HOURS`, 24 hours by default. An id of another tenant, an expired one and one that never existed are all `400` "Unknown upload".
- An upload goes through the same checks as an `image` or `icon` in the body, when it arrives and again when it is used, so a card can only carry what passed them.

### Images and icons from a URL

With `image_url` or `icon_url` the service fetches the file itself, so the caller does not have
to download and encode it first. It needs no storage:

```json
{ "template": "demo/card-image", "headline": "Hallo", "image_url": "https://images.example/photo.jpg" }
```

A URL makes the service connect somewhere on the caller's behalf, from inside the network it runs
in. So every URL has to pass these rules:

| Rule | Otherwise |
|------|-----------|
| `https` only, on port 443, with no user name or password in the URL | `400` |
| Every address the host name resolves to must be globally reachable. Refused are the private, loopback, link-local, carrier-grade NAT, documentation, benchmarking, reserved and multicast ranges of IPv4 and IPv6, every IPv6 address that carries an IPv4 address (IPv4-mapped, IPv4-compatible, 6to4, Teredo, NAT64), and the metadata addresses of the cloud providers, such as `169.254.169.254`, `fd00:ec2::254`, `100.100.100.200`, `192.0.0.192` and `168.63.129.16`. One such address among public ones refuses the URL | `400` |
| At most three redirects. Every target meets the same rules, with a lookup of its own | `400` for a target against the rules, `502` for a fourth redirect |
| 10 seconds for the whole fetch, redirects and body included | `502` |
| The last answer is a `200` | `502` |
| At most `MAX_IMAGE_BYTES` (8 MB by default) for an image and 256 KB for an icon, counted as the body arrives, whatever `Content-Length` says | `413` |
| The bytes are a PNG, JPEG or WebP image, or an SVG that meets the [SVG rules](#image-or-icon-on-a-content-slide). The `Content-Type` the other side sends does not count | `400` |

- **The connection goes to the address that was checked.** The service resolves the host name once per hop, checks every address and connects to the first one, with the host name in the `Host` header and in TLS, so the certificate is still checked against the name. A DNS answer that changes a moment later cannot send the request elsewhere. Proxy settings of the environment are ignored.
- **The errors give nothing away.** A URL against the rules is a `400` "The image URL points to an address that is not allowed.", a fetch that fails for any reason, from an unknown host to a `404`, is a `502` "The image URL could not be fetched."; for an icon both say "icon URL". Neither names an address the host resolved to.
- **The template is checked first.** A URL for a template without the capability is a `400` before anything is resolved.
- **Nothing is kept.** The file is fetched for this one request and checked and embedded like base64 in the body; there is no cache. The log line of the fetch names its kind, size and redirects, never the URL, which often carries a token.
- The service asks for the file uncompressed, with `Accept` naming the formats it takes, so an image CDN that picks the format by that header does not answer with AVIF.

### Output as a URL

With `"output": "url"` and [storage](storage/README.md), the service stores the PNG and answers
with JSON instead of the image:

```json
{
  "ok": true,
  "url": "https://files.contentgun.de/o/acme/k3E…/demo-card_4x5_1a2b3c4d.png",
  "content_type": "image/png",
  "width": 1080,
  "height": 1350,
  "bytes": 214733,
  "expires_at": "2026-11-17T10:01:12Z"
}
```

The URL needs no key: its 192 random bits are the permission, so it can go to whoever needs the
image, such as a publishing API that fetches images from a public address. It answers until
`expires_at`, `STORAGE_TTL_HOURS` after the render, see [`GET /o/…`](files.md).

### Field resolution

A field takes its value from the request, and from the template default when the request omits it.
There is no server-side directory of accounts: the caller passes the handle it wants to see on the
card, so the API never claims an identity the caller did not send. A required field that resolves
to empty is a `400`.

### Text limits

Four mechanisms keep text inside the card, in this order.

1. **Character limits, enforced before rendering.** Headline up to 140, subline up to 200, handle, topic and author up to 60, role up to 80, pagination up to 16. Over the limit is a `400` and no image. A field a template declares without a typed counterpart, such as `body`, `cta` or `quote`, is capped at 200. A template may set tighter limits of its own in the `[limits]` table of its `template.toml`, which `GET /v1/templates` reports as `limits`.
2. **Hyphenation.** Every text field except the handle and the pagination is given German break points as soft hyphens before rendering, so a long compound breaks at a syllable and shows a hyphen.
3. **Automatic font sizing.** On `demo/card` and `demo/card-image` the headline shrinks between 104px and 40px and the subline between 40px and 24px, until the text fits its box. The carousel templates and `demo/quote` have ranges of their own, listed under [Carousel text limits](#carousel-text-limits) and [Quote text limits](#quote-text-limits). Nothing is truncated.
4. **Clipping**, if the text still does not fit at the minimum size. The boxes are `overflow: hidden`.

**Step 4 is not reachable through the API**, because the character limit always bites first. The
one exception is text that forces its own line breaks, see the newline note under [Notes](#notes).
Measured in the real renderer on `demo/card` at 1080x1350:

| Headline characters | Rendered at | Lines | Clipped |
|---|---|---|---|
| 10 | 104px | 1 | no |
| 40 | 104px | 3 | no |
| 100 | 83px | 5 | no |
| 140 (the limit) | 73px | 6 | no |

The subline behaves the same way: 200 characters, its limit, render at 33px across four lines
without clipping.

A single word with no spaces is the worst case for wrapping, and the dictionary has nothing to
offer for one. Such a run is chopped every 12 characters instead, so 60 unbroken characters
render at 97px across five lines, each ending in a hyphen, rather than running off the card.
The tallest text a field can hold is such a run of wide capitals at the full limit: 140 × `W`
lands at 45px, with 200 × `W` in the subline at its 24px floor, and still nothing is clipped.

These limits are what the card can take, not what reads well on it. 140 characters fill six
lines and look like a wall of text. A headline of about six words and a subline of about eight,
roughly 40 to 60 characters each, read well.

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | PNG bytes | `Content-Type: image/png`, plus `Content-Disposition: inline; filename="<bundle>-<template>_<format>_<id>.png"`, for example `demo-card_4x5_<id>.png`, and `Cache-Control: no-store` |
| `200` | JSON | With `"output": "url"`: where the PNG is stored, see [Output as a URL](#output-as-a-url) |
| `400` | `{"ok": false, "error": "..."}` | Invalid JSON, failed validation, unknown template, unsupported format, field the template does not accept, field that is not a string, field over the template's own limit, an image or icon the template does not take, an image and an icon together, a graphic from two sources, an image that is no PNG, JPEG or WebP, an icon that breaks one of the [SVG rules](#image-or-icon-on-a-content-slide), an unknown upload or one of the other kind, a URL against the [rules for URLs](#images-and-icons-from-a-url) |
| `401` | `{"ok": false, "error": "Unauthorized."}` | Missing or wrong key |
| `413` | `{"ok": false, "error": "..."}` | The decoded `image` or the file at `image_url` exceeds `MAX_IMAGE_BYTES`, or the decoded `icon` or the file at `icon_url` exceeds 256 KB |
| `500` | `{"ok": false, "error": "..."}` | Render failure, or a font of the template's bundle that did not load while `STRICT_FONTS` is on |
| `501` | `{"ok": false, "error": "Storage is not configured on this server, so … cannot be used."}` | `"output": "url"`, `image_upload` or `icon_upload` without storage |
| `502` | `{"ok": false, "error": "The image URL could not be fetched."}` | The file at `image_url` or `icon_url` could not be fetched: unknown host, no connection, a certificate that does not match, no `200`, more than three redirects or more than 10 seconds |
| `503` | `{"ok": false, "error": "..."}` | The browser is not available, the service is shutting down, all render slots were occupied longer than `QUEUE_TIMEOUT_MS`, or the bucket did not answer |
| `504` | `{"ok": false, "error": "Render timed out."}` | The render exceeded `RENDER_TIMEOUT_MS` |

A `503` is worth retrying, and so is a `502` when the other side may have been down; a `400` never
is.

## Example (curl)

```
curl -sS --fail-with-body -o sample.png \
    -X POST http://127.0.0.1:8010/v1/render \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"template":"demo/card","headline":"Gute Vorlagen sparen jede Woche Stunden.","subline":"Text rein, fertige Karte raus.","handle":"@contentgun"}'
```

With a background image, where the base64 payload makes a file the easier route than a command
line argument:

```
IMAGE=photo.jpg API_KEY=$API_KEY bash scripts/render_sample.sh
```

## Example (n8n)

Use an **HTTP Request** node:

- Method `POST`, URL `http://127.0.0.1:8010/v1/render`
- Authentication: generic credential, **Header Auth**, name `Authorization`, value `Bearer <key>`
- Body: JSON, for example `={{ { "template": "demo/card", "headline": $json.image_headline, "subline": $json.image_subline, "handle": "@contentgun" } }}`
- With a background image: `={{ { "template": "demo/card-image", "headline": $json.image_headline, "subline": $json.image_subline, "handle": "@contentgun", "image": $binary.image_4x5.data } }}`
- Options, Response: **Format: File** — the node then holds the PNG as a binary property, ready
  for an upload node.

## Carousel

An Instagram carousel is not a request of its own. Every slide is one `POST /v1/render` that
returns one PNG, against one of three templates made to follow each other:

| Template | Fields (required in bold) | Look |
|----------|---------------------------|------|
| `demo/carousel-cover` | **`headline`** (the hook), `topic`, `subline`, `handle` | Slate with off-white text: the topic in an amber label above the hook, the subline under it, and an amber swipe button at the bottom right. Without a topic, a short amber bar takes the label's place |
| `demo/carousel-content` | **`headline`**, `body`, `pagination`, `handle`, `icon_color`, plus `image` or `icon` | Slate like the cover: a short amber bar above the headline, the body under it and an optional image or icon under the body, centered vertically, so the headline sits higher on a slide with more text |
| `demo/carousel-closing` | **`headline`** (the resolution), `cta` (the open question), `pagination`, `handle` | Slate like the cover, with the amber bar above the headline and the question under it |

The cover comes first, then one content slide per point, then the closing slide. All three open
with a header row: the handle and, on the content and closing slides, the pagination at its right
end, so the handle stays in place across the series.

- **`pagination` is text the caller writes**, see [Pagination](#pagination) below.
- **Every slide of a carousel uses the same `format`.** Instagram crops all slides of a carousel to the aspect ratio of the first one, so a slide in another format comes out cropped.
- **Every slide repeats the `handle`.** The service fills nothing in, so a slide rendered without one simply has none.

### Pagination

`pagination` is one piece of text that the slide shows at the right end of its header row,
exactly as sent. The service does not count, pad or check anything, so the pattern is the
caller's choice. For a carousel of seven slides:

| Slide | Template | `pagination`, for example | Header row, right end |
|-------|----------|---------------------------|-----------------------|
| 1 | `demo/carousel-cover` | not sent | empty; the swipe button sits at the bottom right instead |
| 2 to 6 | `demo/carousel-content` | `"2/7"` to `"6/7"`, or `"2 von 7"` to `"6 von 7"` | the text as sent |
| 7 | `demo/carousel-closing` | `"7/7"`, or `"7 von 7"` | the text as sent |

- **It is a string of up to 16 characters.** A JSON number is a `400` (`pagination: Input should be a valid string.`), and so is a longer text. The text never wraps: next to a long handle, the handle is shortened with an ellipsis and the pagination stays whole.
- **The cover counts as slide 1 but shows no pagination.** It does not declare the field, so sending it to the cover is a `400`.
- **Empty or omitted removes the element.** The text is HTML-escaped and gets no soft hyphens.
- **Every slide of a carousel should use the same pattern.** Nothing enforces it; the flow that builds the bodies decides it once, as in the example below.

### Example

The five request bodies of a carousel with three content slides, in slide order:

```json
[
  { "template": "demo/carousel-cover", "format": "4:5", "topic": "Arbeitsweise", "headline": "Drei Gewohnheiten für bessere Beiträge.", "subline": "Und wie du sie in deinen Alltag holst.", "handle": "@contentgun" },
  { "template": "demo/carousel-content", "format": "4:5", "headline": "Erst das Ziel, dann der Text", "body": "Leg vorher fest, was ein Beitrag erreichen soll.", "pagination": "2/5", "handle": "@contentgun" },
  { "template": "demo/carousel-content", "format": "4:5", "headline": "Ein Gedanke pro Beitrag", "body": "Lass eine Idee tragen und heb dir die anderen auf.", "pagination": "3/5", "handle": "@contentgun" },
  { "template": "demo/carousel-content", "format": "4:5", "headline": "Fester Rhythmus statt Zufall", "body": "Veröffentliche jede Woche zur selben Zeit.", "pagination": "4/5", "handle": "@contentgun" },
  { "template": "demo/carousel-closing", "format": "4:5", "headline": "Klein anfangen, regelmäßig liefern.", "cta": "Welche Gewohnheit probierst du als Erstes aus?", "pagination": "5/5", "handle": "@contentgun" }
]
```

The second one with curl:

```
curl -sS --fail-with-body -o slide-02.png \
    -X POST http://127.0.0.1:8010/v1/render \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"template":"demo/carousel-content","format":"4:5","headline":"Erst das Ziel, dann der Text","body":"Leg vorher fest, was ein Beitrag erreichen soll.","pagination":"2/5","handle":"@contentgun"}'
```

`scripts/render_carousel.sh` renders a carousel like this one into `carousel/01.png` to
`carousel/05.png`, with `FORMAT=1:1` in the square format, and with `ICON` set to an SVG file with
that icon under the text of every content slide, in the text color with `ICON_COLOR=text`.
`BUNDLE` picks another bundle with the same three templates:

```
API_KEY=$API_KEY bash scripts/render_carousel.sh
FORMAT=1:1 API_KEY=$API_KEY bash scripts/render_carousel.sh
ICON=lightbulb.svg API_KEY=$API_KEY bash scripts/render_carousel.sh
```

### Image or icon on a content slide

A content slide can carry one graphic under its body, on the same left edge as the text and
40px below it at `4:5`, 32px at `1:1`, the gap between all the other elements of the stage:

- **`icon`** is an SVG pictogram, drawn as a silhouette in one color: every opaque part takes the color, the amber accent by default or the text color with `"icon_color": "text"`. An icon therefore needs a transparent background, and a detail stays light only where the SVG cuts it out, as icon sets do. It is 240px high at `4:5` and 180px at `1:1`, as wide as its proportions make it, and never wider than the 888px of the text column.
- **`image`** is a picture shown as it is, in its own proportions: at most 300px high at `4:5` and 225px at `1:1`, never wider than the 888px of the text column, never enlarged, and scaled down further when the text leaves less room. Send it at least 300px high, so that it reaches that height.

The whole block of bar, headline, body and graphic is centered vertically. With a graphic, the
headline and the body get less room, so that the icon height stays free even with both fields at
their limits; see [Carousel text limits](#carousel-text-limits).

An icon has to be an SVG document that scales and that draws the same on every host:

| Rule | Why |
|------|-----|
| The root is `<svg xmlns="http://www.w3.org/2000/svg" ...>` | Without the namespace the browser draws nothing |
| It has a `viewBox` with a positive width and height | Without one the SVG does not scale and stays at its own, tiny size |
| No `<animate>`, `<animateTransform>`, `<animateMotion>`, `<set>` or CSS `@keyframes` | The screenshot would catch whatever frame the animation is at |
| No `<text>` or `<foreignObject>`; convert text to paths | Text in an SVG is set in the host's system fonts, which differ between development and production |
| At most 256 KB after decoding, and never more than `MAX_IMAGE_BYTES` | An icon is a few kilobytes; the cap keeps requests small |

A broken rule is a `400` that names it. The SVG is drawn as an image and never inserted into the
page, so it runs no script and loads nothing: an external reference inside it stays empty.
Bootstrap Icons and Lucide meet every rule as they are.

```json
{ "template": "demo/carousel-content", "format": "4:5", "headline": "Erst das Ziel, dann der Text", "body": "Leg vorher fest, was ein Beitrag erreichen soll.", "icon": "<base64 SVG>", "icon_color": "accent", "pagination": "2/5", "handle": "@contentgun" }
```

### Icons from an icon library

The service keeps no icon library: the caller sends the SVG itself. In n8n, an **HTTP Request**
node fetches it by name before the render call, for example from Bootstrap Icons on jsDelivr,
with the version pinned so that a name always gives the same icon:

- Method `GET`, URL `https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/icons/{{ $json.icon_name }}.svg`
- Options, Response: **Format: File**, which stores the SVG as the binary property `data`
- In the render body: `"icon": $binary.data.data`

The names are the ones on icons.getbootstrap.com, such as `lightbulb`, `graph-up-arrow` or
`people`. An unknown name is a `404` from jsDelivr before the service is involved, so the flow
can drop the icon for that slide instead of failing the whole post.

The service can also fetch the icon itself: `"icon_url": "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/icons/{{ $json.icon_name }}.svg"`
saves the extra node, and an unknown name is then a `502` from the render call.

### In n8n

1. A **Code** node in **Run Once for All Items** mode turns the post into one item per slide, in slide order. Each item's JSON is the complete request body of its slide, `template` and `format` included, and nothing else: a key the slide's template does not declare is a `400`, even when it is empty or `null`. The pagination comes from the position in the slide list, as below. `$itemIndex` does not work for it: n8n does not offer it in the Code node, and in this mode every slide would get the same index.
2. One **HTTP Request** node, set up as in [Example (n8n)](#example-n8n) with the body `={{ $json }}`, runs once per item and, with Response **Format: File**, returns the slides as binaries in item order. n8n sends the requests for all items at once unless **Options → Batching** spaces them out. They queue for a render slot, and one that waits longer than `QUEUE_TIMEOUT_MS` gets a `503`, which **Retry On Fail** in the node settings takes care of.
3. Publishing belongs to the flow, not to the service: one media container per slide (`POST /<ig-user-id>/media` with `is_carousel_item=true`), then a container with `media_type=CAROUSEL` and `children` set to the slide containers' ids in order, then `POST /<ig-user-id>/media_publish` with that container's id as `creation_id`. Meta's content publishing documentation has the Graph API fetch every image from a public URL, names JPEG as the only supported image format and allows at most ten slides, so the flow converts the PNGs to JPEG and hosts them before it creates the containers.

The Code node of step 1, for a post with a hook, a topic, a list of content slides, a resolution
and a closing question. The input field names are placeholders for whatever the generating node
produces:

```
const post = $input.first().json;
const handle = "@contentgun";
const format = "4:5";
const pages = post.slides.length + 2;
const pagination = (slide) => `${slide}/${pages}`;

return [
  {
    json: {
      template: "demo/carousel-cover",
      format,
      headline: post.hook,
      topic: post.topic,
      subline: post.subline,
      handle,
    },
  },
  ...post.slides.map((slide, index) => ({
    json: {
      template: "demo/carousel-content",
      format,
      headline: slide.headline,
      body: slide.body,
      pagination: pagination(index + 2),
      handle,
    },
  })),
  {
    json: {
      template: "demo/carousel-closing",
      format,
      headline: post.resolution,
      cta: post.cta,
      pagination: pagination(pages),
      handle,
    },
  },
];
```

`index + 2` because the cover is slide 1 and the first content slide is slide 2, and the closing
slide is the last one. For "3 von 7" instead of "3/7", change the one line that defines
`pagination`.

### For LinkedIn

LinkedIn takes a carousel as a PDF document with one page per slide. The same slide bodies,
collected into one list, go to [`POST /v1/document`](document.md), which renders them as one PDF.
The PNGs are not needed for it. Images and icons go along, all of them together up to
`MAX_IMAGE_BYTES`.

### Carousel text limits

The carousel templates fit their text like the cards do, within ranges of their own:

| Template | Headline | Second field |
|----------|----------|--------------|
| `demo/carousel-cover` | 104px down to 40px | `subline`: 40px down to 24px. `topic`: fixed at 26px, at `1:1` 24px, not fitted, up to 60 characters |
| `demo/carousel-content` | 80px down to 40px, with an image or icon as well | `body`: 40px down to 24px |
| `demo/carousel-closing` | 96px down to 40px | `cta`: 40px down to 24px |

Measured in the real renderer, in px, with running text (`Automatisierung` repeated to the
length) and with the tallest text a field can hold, a run of `W` at every limit: 140 in the
headline, 200 in `subline`, `body` and `cta`, 60 in `topic`. Where a cell holds two numbers, they
are headline / second field. On the cover, "both" and "every limit" include the topic as well.
Nothing was clipped in any case, and the stage never overflowed:

| Template | Format | Headline at 140 | Second field at its limit | Both at their limit | `W` at every limit |
|----------|--------|-----------------|---------------------------|---------------------|--------------------|
| `demo/carousel-cover` | `4:5` | 71 | 33 | 71 / 33 | 41 / 24 |
| `demo/carousel-cover` | `1:1` | 63 | 33 | 51 / 33 | 41 / 24 |
| `demo/carousel-content` | `4:5` | 66 | 40 | 66 / 40 | 41 / 28 |
| `demo/carousel-content` | `1:1` | 59 | 36 | 59 / 36 | 41 / 24 |
| `demo/carousel-content` with a graphic | `4:5` | 59 | 39 | 59 / 39 | 41 / 25 |
| `demo/carousel-content` with a graphic | `1:1` | 49 | 33 | 49 / 33 | 41 / 24 |
| `demo/carousel-closing` | `4:5` | 73 | 34 | 73 / 34 | 45 / 24 |
| `demo/carousel-closing` | `1:1` | 68 | 33 | 68 / 33 | 41 / 24 |

Running text stays well above every floor: the headline at 49px or more, the second field at 33px
or more. A run of `W` is chopped every 12 characters. At 41px and below two chunks share a line of
Space Grotesk, at 24px three share a line of Inter, so at the floors such a run takes six lines:
269px for a headline of 140 × `W`, 214px for a second field of 200 × `W`. Every box leaves that
much room, in both formats, so the run fits; `1:1` gets there with smaller gaps, and on the cover
with a smaller swipe button.

An image or icon on a content slide takes its room from the text: the headline box shrinks to 36%
of the stage at `4:5` and 35% at `1:1`, the body box to 28%, which keeps 240px or 180px free under
the body with both fields at their limits. The measurements are the same for an icon and for an
image, because an image only ever shrinks into the room the text leaves, and the icon keeps its
full size in every case.

**Clipping is therefore unreachable through the API on the carousel templates as well, in both
formats and with or without a graphic**, with the same exception for forced line breaks.
Re-measure both formats, with and without a graphic, after changing a box, a size, a font or a cap;
`tests/test_text_limits.py` fails once a run of `W` at the limits no longer fits.

## Quote card

`demo/quote` sets a quote for testimonials, customer voices and quotes from talks or interviews,
in `4:5` and `1:1`, so it also works as a slide of a carousel next to the carousel templates:

| Field | Required | On the card |
|-------|----------|-------------|
| `headline` | no | The hook above the quote, such as `"Vier Stunden pro Woche gespart"`. Up to 80 characters, the template's own limit. Without it the quote takes its room |
| `quote` | yes | The quote, without quotation marks: an amber quotation mark stands above the card's text |
| `author` | no | Under the quote, in the headline font |
| `role` | no | Under the author, in the muted text color: role and company, or the source of the quote |
| `image` | no | A round portrait left of the author and role, cropped to fill the circle. `image_upload` and `image_url` work as well |
| `handle`, `pagination` | no | The header row, as on the content slides |

Every element but the quote disappears when its field is omitted or empty, so a card with only a
quote is a valid request:

```json
{ "template": "demo/quote", "format": "1:1", "quote": "Seit wir unsere Karten aus Vorlagen bauen, geht kein Beitrag mehr in die dritte Abstimmungsrunde.", "author": "Maria Beispiel", "role": "Leiterin Marketing, Beispiel GmbH", "handle": "@contentgun" }
```

### Quote text limits

The headline is fitted from 64px down to 40px, the quote from 56px down to 24px, in Inter.
`author` and `role` are not fitted: they wrap at a fixed 30px and 24px, and their room comes out of
the quote's, so their caps are part of the measurement. A portrait narrows them, which makes it the
worst case.

Measured in the real renderer, in px, with the portrait and with every field at its limit (80 in
the headline, 200 in the quote, 60 in `author`, 80 in `role`), once with running text and once as
a run of `W`. Where a cell holds two numbers, they are headline / quote. Nothing was clipped in any
case, and the stage never overflowed:

| Card | Format | Running text at every limit | `W` at every limit |
|------|--------|-----------------------------|--------------------|
| with a headline | `4:5` | 64 / 43 | 44 / 27 |
| with a headline | `1:1` | 55 / 33 | 41 / 24 |
| quote only | `4:5` | 56 | 35 |
| quote only | `1:1` | 47 | 35 |

Without a headline the quote box grows from 31% of the stage to 62% at `4:5`, and from 27% to 54%
at `1:1`. `tests/test_text_limits.py` measures both cards in both formats, with and without the
portrait.

## Notes

- **Text is fitted to its box, not truncated.** See [Text limits](#text-limits) and [Carousel text limits](#carousel-text-limits) for the sizes and where each limit bites.
- **A literal newline forces a line break** in every text field except the handle and the pagination, and is kept as written. Use it when the automatic wrap splits a phrase badly. The character limits do not account for it: every newline costs a full line at any font size, so a field that is mostly line breaks, a long list in `body` for example, can still reach its floor and be clipped.
- **Hyphenation happens in the service, not in the browser.** Chromium's own `hyphens: auto` needs dictionaries that the Linux headless shell does not ship, so a card would break differently in development and in production. The soft hyphens the service inserts are invisible unless a line actually breaks there.
- **The handle is whatever the request sends.** Nothing fills it in, so a flow that forgets it gets a card without one instead of a wrong account. Keep it in the flow's configuration next to the target account.
- **`$binary.<name>.data` is only base64 while n8n keeps binaries in memory.** With filesystem binary storage the property holds a reference instead, and the flow needs a node that converts the binary to base64 before the render call.
- **An image or icon in the body or from a URL lives in the request and nowhere else.** Nothing is written to disk, and the service keeps no image or icon directory. Only an upload and a result with `"output": "url"` are stored, in the bucket, for `STORAGE_TTL_HOURS`.
- **Emoji are not supported.** They would render differently on macOS and Linux and are not part of the shipped font subsets.
- The fonts of the template's bundle are embedded in the page as data URIs and the screenshot waits for them to load, so a card never renders in a fallback font. With `STRICT_FONTS=true` a font that fails to load is a `500` rather than a wrong-looking image.
- Every text field is HTML-escaped. Markup in the request text appears as literal characters.
- Each call renders fresh. Without `"output": "url"` the PNG exists only in the response.
