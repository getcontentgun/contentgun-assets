# Template bundles

A bundle is a folder that holds everything its templates need: the fonts, the logos and fallback
images, shared partials, and one folder per template. The renderer has no fonts, logos or images
of its own; everything a card shows comes from the bundle of its template.

## Layout

```
<bundle>/
  bundle.toml          name, description, font faces and font checks
  fonts/               woff2 files and their license texts
  assets/              logos, fallback images
  _base/               optional: shared skeleton and CSS
  <template>/
    template.toml
    template.html.j2
    template.css
```

Bundles live in two places, described in [Tenants](../tenants/README.md):

- **Built-in bundles** in `bundles/` of this repository, `bundles/demo/` today. They ship with the service and the Docker image, and every tenant can use them.
- **Bundles of a tenant** in its folder under `BUNDLES_DIR`, `<BUNDLES_DIR>/<tenant>/<bundle>/`. Only that tenant can use them.

In either root every folder is a bundle, unless its name starts with `_` or `.`. Inside a bundle,
every folder is a template, except `fonts`, `assets` and folders whose name starts with `_` or `.`.
Files next to them, such as a `README.md`, are ignored.

## Names and ids

Bundle and template names use lowercase letters, digits and hyphens, and start with a letter or a
digit: `^[a-z0-9][a-z0-9-]*$`. A folder with any other name stops the start, except the ones
skipped above.

A template is addressed as `<bundle>/<template>`, for example `demo/card`: in the
`template` field of [`POST /v1/render`](../render.md) and [`POST /v1/document`](../document.md),
in [`GET /v1/templates`](../templates-list.md), on the debug routes, in [`/health`](../health.md),
in `DEFAULT_TEMPLATE` and in the `default_template` of a tenant. A name without its bundle is an
unknown template, and so is the id of a template that belongs to another tenant. The file name of
a rendered PNG joins the two with a hyphen: `demo-card_4x5_<id>.png`.

A bundle of a tenant must not have the name of a built-in bundle, so an id means the same template
for every tenant that can use it.

## bundle.toml

```
name = "demo"
description = "A neutral demo bundle: slate cards with an amber accent."

[fonts]
checks = ['700 40px "Space Grotesk"', '400 20px "Inter"']

[[fonts.faces]]
family = "Space Grotesk"
file = "SpaceGrotesk-latin.woff2"
weight = "300 700"
subset = "latin"

[[fonts.faces]]
family = "Inter"
file = "Inter-latin.woff2"
weight = "100 900"
subset = "latin"
```

| Key | Required | Description |
|-----|----------|-------------|
| `name` | no | Must equal the folder name when given |
| `description` | no | Listed by `GET /v1/templates` and on the debug overview |
| `[fonts]` `checks` | yes | The faces the browser must have loaded before the screenshot, at least one |
| `[[fonts.faces]]` | yes | One table per font file, at least one |

Every face is a table of its own:

| Key | Required | Description |
|-----|----------|-------------|
| `family` | yes | The family name the CSS uses, without quotes |
| `file` | yes | A file name in `fonts/`, without a path |
| `weight` | yes | A weight such as `"400"`, or a range such as `"200 900"` for a variable font whose axis spans it |
| `subset` | no | `latin` or `latin-ext`, which sets the `unicode-range` of that Google Fonts subset |
| `unicode_range` | no | A `unicode-range` of its own, such as `"U+0000-00FF, U+0131"` |

A face takes at most one of `subset` and `unicode_range`; without either, its file applies to
every character. Any other key in `bundle.toml`, in `[fonts]` or in a face stops the start, so a
typo such as `unicode-range` cannot silently widen a face to every character.

A check has the form `<weight> <size>px "<family>"`. It must name a declared family and a weight
that one of that family's faces covers, or the service refuses to start. It should also be a
weight the bundle's CSS really draws: a check for a weight no rule uses passes on a substituted or
synthesised face and proves nothing.

Every template of a bundle gets all of the bundle's faces, embedded in the page as data URIs, and
its page loads every checked face before it reports ready. After that the service probes each
check, paired with a loaded face of the same family; with `STRICT_FONTS=true` a check that fails is
a `500` instead of a card in a fallback font.

A face whose file is a variable font declares the full range of its weight axis. A narrower range
clamps a request for a weight outside it to the nearest end, silently, and every check still
passes.

Put the license of every font next to it in `fonts/`.

## template.toml

| Key | Required | Description |
|-----|----------|-------------|
| `name` | no | Must equal the folder name when given |
| `description` | no | Listed by `GET /v1/templates`. Say what the template is for and how to fill its fields: the MCP server shows it to the model as the template's documentation |
| `formats` | yes | One or more of `4:5`, `1:1`, `9:16`, `16:9`. The first is the default |
| `fields` | yes | The fields a request may send |
| `required` | no | Fields a request must send non-blank |
| `[defaults]` | no | Values for fields a request omits |
| `[samples]` | yes | Preview content of the debug route, never used by the render endpoint. Every required field needs a non-blank sample |
| `[limits]` | no | Character limits below the service-wide ones, from 1 to 200 |
| `image` | no | `true` lets the template take the request's image |
| `fallback_image` | no | A file in the bundle's `assets/`, used when a request sends no image. Needs `image = true` |
| `icon` | no | `true` lets the template take an SVG icon. Not together with `fallback_image` |
| `code` | no | `true` lets the template take source code, and makes every request carry some. Needs a `[code_sample]` |
| `[code_sample]` | with `code` | The code of the debug route's preview: `code`, a string, and optionally `language`, `line_numbers` and `highlight_lines`, as in a render request. It must pass the same checks, or the start fails |

`template.html.j2` receives `width`, `height`, `format`, `format_token`, `fields`, `image`, `icon`,
`code`, `css` (the template's `template.css`), `font_css` (the `@font-face` rules of the bundle),
`font_checks` (its checks) and `fit_text`, plus the filters `hyphenate`, `highlight`, `percent` and
`registered` and the global `inline_svg()`. `CLAUDE.md` describes how a template uses them, under
"Adding a template".

### Code

On a template with `code = true`, `code` holds the request's code, checked and highlighted; on
every other template it is `None`. `code.html` is the markup to put into the fitted element,
`code.lines` the number of lines, `code.line_numbers` whether the request asked for line numbers,
and `code.language` the language as sent, in lowercase, or `text`.

The markup is one `<span class="line">` per line, with nothing between them, so the bundle's CSS
must set `.line { display: block; }`, under `white-space: pre`. A highlighted line carries the class
`hl` as well. With line numbers, every line starts with `<span class="ln">` and its number. Inside a
line, every token the bundle may color is a `<span class="tok-<class>">`, and everything else is
plain text, escaped:

| Class | Tokens |
|-------|--------|
| `tok-keyword` | keywords, word operators such as `and` and `not`, preprocessor directives |
| `tok-string` | strings of every kind, docstrings and the file name of an `#include` |
| `tok-number` | numbers |
| `tok-comment` | comments |
| `tok-function` | the name in a function definition |
| `tok-type` | the name in a class definition and exception names |
| `tok-builtin` | builtins, such as `print`, `len` or `echo`, and `self` |
| `tok-decorator` | decorators and annotations |
| `tok-tag` | tags of HTML and XML |
| `tok-attribute` | attributes of HTML and XML |
| `tok-operator` | operators |
| `tok-punctuation` | brackets, commas, colons |
| `tok-inserted`, `tok-deleted` | the added and removed lines of a diff |

A class without a rule in the CSS simply draws in the text color. The code is never hyphenated: the
caps of 14 lines and 48 columns stand in for `| hyphenate`, so a template's floor for the code
must hold 14 lines of 48 columns, line numbers included, see [Code](../render.md#code).

## Isolation

A template reaches the files of its own bundle and nothing else:

- `{% extends %}` and `{% include %}` resolve inside the bundle's folder, so `_base/base.html.j2` names the bundle's own skeleton. Jinja refuses `..` in a template name, and the loader also refuses a file whose real path leaves the bundle, as through a symlink.
- `fallback_image` and `inline_svg("file.svg")` resolve inside the bundle's `assets/`, with the same check.
- A page carries only the fonts of its own bundle, and its screenshot waits only for its bundle's checks.

A bundle linked into the root as a whole works: everything inside it is measured against the
bundle's real location. A template folder or a template file linked in from outside the bundle
stops the start.

## Startup validation

The service does not start while a bundle is broken, a built-in one or one of any tenant. Every
message begins with the file at fault and names the bundle or the template:

- a bundle or template name breaks the naming rule
- `bundle.toml` is missing, is not valid TOML, has an unknown key, names another bundle or has a description that is not a string
- a bundle has no font face, no check, a face with a missing or invalid key, or a check that no face covers
- a font file is missing, smaller than 1 KB, not a woff2 file or linked from outside `fonts/`; all of a bundle's broken files are listed in one message
- a bundle has no template, or a template lacks `template.toml` or `template.html.j2`
- a `template.toml` breaks one of its rules, see the table above
- a fallback image is missing, outside the bundle's `assets/`, or not a PNG, JPEG or WebP file
- a template sets `code = true` without a `[code_sample]`, has one without `code = true`, or has a code sample that a render request could not send

The checks of the tenants come on top, a tenant without a default template it can use among them;
they are listed in [Tenants](../tenants/README.md#startup-validation). `bin/development` loads the
tenants with all their bundles before uvicorn starts, and `bin/test` the built-in bundles before
pytest, so a broken bundle is reported first.

## What is read when

`bundle.toml`, every `template.toml`, the font files and the fallback images are read once, at
startup, like the tenants file. `template.css` is read on every request, and the `.j2` files go
through Jinja's template cache, which reloads a changed file; a CSS or template edit therefore
needs only a browser reload on the debug route, while a change to a `.toml` file, a font or a
fallback image needs a restart. `bin/development` restarts on its own when a `.toml` file changes,
also in `BUNDLES_DIR` and in the folder of the tenants file when they lie outside the repository.
