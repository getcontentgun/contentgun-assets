# GET /v1/debug

An overview page with one link per template, format and variant into
[`GET /v1/debug/html`](debug-html.md), so a design pass can click through every slide instead of
assembling URLs by hand.

Only mounted when `DEBUG_ROUTES=true`, and not authenticated, like the debug route itself.

## Request

**Method:** `GET`, no parameters. `/v1/debug/` redirects here.

## What it lists

Every bundle the registry loaded, with its description, and below it every template of the
bundle, with its id, its description and a table: one row per variant, one column per format the
template offers. The variants follow from what the
template declares, so a new template shows up with the right ones without a code change:

| Variant | Offered for | Link |
|---------|-------------|------|
| Sample | every template | The template's `[samples]` |
| Required fields only | templates with optional fields in `[samples]` | Every optional sample field set to empty, which removes its element |
| With an image | templates with `image = true` and no fallback image, today `demo/carousel-content` | `image_url` pointing at the sample image |
| With an icon | templates with `icon = true`, today `demo/carousel-content` | `icon_url` pointing at the sample icon |
| With an icon in the text color | templates with `icon = true` that declare `icon_color` | The same, plus `icon_color=text` |

A template with a fallback image shows it in its sample already, so it gets no image row.

## Sample files

The image and icon rows point at two files the debug router serves itself, so the previews work
without a network:

| Path | Content |
|------|---------|
| `GET /v1/debug/samples/image.png` | A gradient of 1200 × 675 px, drawn by the service in memory, so the debug routes need no image of any bundle |
| `GET /v1/debug/samples/icon.svg` | A check mark in a circle, drawn with strokes like a Lucide icon, which meets every [icon rule](render.md#image-or-icon-on-a-content-slide) |

## Responses

| Status | Body | Description |
|--------|------|-------------|
| `200` | HTML | The overview, with `Cache-Control: no-store` |
| `404` | `{"ok": false, "error": "Not found."}` | `DEBUG_ROUTES` is off |

## Example

```
open http://127.0.0.1:8010/v1/debug
```
