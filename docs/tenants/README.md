# Tenants

Every request belongs to a tenant. A tenant has API keys of its own and bundles of its own, and it
sees its own bundles and the built-in ones, nothing else: a template of another tenant is an unknown
template.

## Terms

- **Tenant:** a customer of the service, with its keys, its email addresses and the folder of its bundles.
- **Built-in bundle:** a bundle in `bundles/` of this repository. It ships with the service and with the Docker image, and every tenant can use it unless its bundle list leaves it out. Today that is `demo`.
- **Bundle of a tenant:** a bundle in the folder of a tenant under `BUNDLES_DIR`. Only that tenant can use it.

## One tenant: API_KEYS

Without `TENANTS_FILE` the service has exactly one tenant, named `default`. Its keys are the
comma-separated `API_KEYS` in `.env`, in plain text; the service hashes them at startup and keeps
only the hashes. Its bundles, if it has any beyond the built-in ones, lie in
`<BUNDLES_DIR>/default/`.

This is how a fresh clone runs: `.env` with a key in `API_KEYS`, nothing else.

## Several tenants: the tenants file

`TENANTS_FILE` names a TOML file with one table per tenant. `tenants.example.toml` in the
repository shows the form:

```
[tenants.acme]
api_keys = ["sha256:3f0a…"]
emails = ["team@acme.example"]

[tenants.studio]
api_keys = ["sha256:9c41…", "sha256:07be…"]
bundles = ["studio-cards"]
default_template = "studio-cards/card"
```

| Key | Required | Description |
|-----|----------|-------------|
| table name | yes | The name of the tenant and of its folder in `BUNDLES_DIR`, following the naming rule of bundles, `^[a-z0-9][a-z0-9-]*$` |
| `api_keys` | no | The hashes of its keys, see [Keys](#keys). A tenant without keys is reachable through the internal key only |
| `emails` | no | Email addresses of the tenant, kept in lower case. The service only checks their form and that no two tenants share one; the [remote MCP server](../mcp/remote.md) signs people in with them |
| `bundles` | no | The bundles the tenant may use, its own and built-in ones by name. Without the key it may use all of them |
| `default_template` | no | The template id a request without `template` gets. Without the key, `DEFAULT_TEMPLATE` |

Any other key stops the start, so a typo cannot silently drop a setting. `API_KEYS` has to be empty
next to a tenants file: with both the service refuses to start, because it could not tell which
tenant a key in `API_KEYS` belongs to.

A relative `TENANTS_FILE` counts from the repository root. The file holds no secrets, only hashes,
but it does name the customers, so keep it out of public repositories; git ignores `tenants.toml`
in the root of this repository.

## Keys

A tenants file never holds a key, only its SHA-256 hash, written as `sha256:` and 64 hex digits.
The script creates a key and prints both; the key is shown this once:

```
python3 scripts/new_api_key.py
```

```
Key, shown only now:       cg_Jx3…
Hash for the tenants file: sha256:3f0a…
```

The hash goes into `api_keys` of the tenant, the key to the caller, n8n for example. A key that
exists already, from `API_KEYS` for instance, keeps working: the script hashes it, read from the
terminal without echo or from standard input:

```
python3 scripts/new_api_key.py --hash
```

The script needs nothing but Python, no virtualenv. A key of the form `cg_…` carries 256 random
bits, so a plain SHA-256 is enough to store it; a slow password hash would add nothing. The service
hashes a presented key once and compares it with every stored hash in constant time, without
stopping at a match.

The `sha256:` prefix is what tells a hash from a key pasted in by mistake: a key from
`openssl rand -hex 32` is 64 hex digits as well. An entry without it stops the start, and the
message leaves the entry out.

## Bundles

```
bundles/                 the built-in bundles, in the repository and in the image
  demo/
<BUNDLES_DIR>/           one folder per tenant
  acme/
    acme-brand/
  studio/
    studio-cards/
```

- A tenant sees the built-in bundles and the bundles in its folder. With a `bundles` list it sees only the listed ones, which may leave out `demo`.
- A tenant without a folder, or with an empty one, has the built-in bundles only.
- Every bundle in a folder of a tenant is loaded and validated at startup, listed or not. To park one, give its folder a name that starts with `_`.
- A bundle of a tenant must not have the name of a built-in bundle, or the service refuses to start: an id such as `demo/card` has to mean the same template for everyone.
- Every folder in `BUNDLES_DIR` must belong to a tenant. Folders whose names start with `_` or `.` are skipped, files are ignored.
- The format of a bundle is the same as before, see [Template bundles](../bundles/README.md). The built-in bundles are loaded once and shared by every tenant.

`BUNDLES_DIR` is empty by default. A relative path counts from the repository root.

With [storage](../storage/README.md) and an empty `BUNDLES_DIR`, the bundles of the tenants come
from `templates/<tenant>/<bundle>/` in the bucket instead, in the same layout and with the same
checks. [`POST /v1/admin/reload`](../admin-reload.md) reads the tenants file and the bundles of the
tenants again, from the bucket or from `BUNDLES_DIR`, without a restart.

## Default template

A request without `template` gets the tenant's `default_template`, or `DEFAULT_TEMPLATE`
(`demo/card`) if the tenant names none. Every tenant needs a default it can use: a tenant whose
`bundles` list leaves out `demo` has to name a template of its own, or the service refuses to
start.

## The internal key

`INTERNAL_KEY` is the key of a service that acts for every tenant: the
[remote MCP server](../mcp/remote.md), which signs people in and then renders for their tenant.
It reads the `emails` of the same tenants file to know who may sign in, and sends the key with
the tenant's name in a header:

```
Authorization: Bearer <INTERNAL_KEY>
X-Contentgun-Tenant: acme
```

The request is then handled exactly as one with a key of `acme`. The internal key is off while
`INTERNAL_KEY` is empty. It must be at least 32 characters long and must not be a key of a
tenant. Without the header, or with a tenant that does not exist, the answer is a `400`. With a key
of a tenant the header is ignored, so a tenant cannot act as another one.

The local MCP server in `mcp/` does not use the internal key: it sends a key of one tenant, like
any other caller.

## What a tenant sees

- [`GET /v1/templates`](../templates-list.md) lists the bundles and templates of the calling tenant.
- A template of another tenant is a `400` for an unknown template, never a `403`, and the list of available templates in the message names only the caller's own. Nothing tells a tenant what others have.
- [`/health`](../health.md) needs no key, so it lists the built-in templates only, never a tenant or a template of one.
- The log lines of render and document name the tenant, never a key or a hash.
- Uploads and stored results belong to their tenant: an upload of another tenant is an unknown upload, and the bucket keeps them below `uploads/<tenant>/` and `outputs/<tenant>/`.
- The [debug routes](../debug-index.md), unauthenticated and for development only, take the tenant as a query parameter.

## Startup validation

On top of the [checks of every bundle](../bundles/README.md#startup-validation), the service does
not start while the tenants are inconsistent. Every message begins with the file or folder at
fault:

- `API_KEYS` and `TENANTS_FILE` are both set
- the tenants file is missing, is not valid TOML, has an unknown key or names no tenant
- a tenant name breaks the naming rule, a tenant is no table or has an unknown key
- an entry in `api_keys` is no `sha256:` hash, or a hash appears twice, in one tenant or in two
- an email address is malformed or belongs to two tenants
- `bundles` is empty or names a bundle that is neither the tenant's own nor built in
- a tenant has no default template it can use
- `INTERNAL_KEY` is shorter than 32 characters or is a key of a tenant
- `BUNDLES_DIR` does not exist or names the folder of the built-in bundles
- a folder in `BUNDLES_DIR` belongs to no tenant, or is a bundle itself
- a bundle of a tenant has the name of a built-in bundle

`bin/development` loads the tenants before uvicorn starts and reports them, so these problems show
up before the first request. A reload runs the same checks and keeps the tenants in use when one
fails.

## Moving from a flat BUNDLES_DIR

Before tenants, `BUNDLES_DIR` named a folder of bundles. It now names a folder of tenant folders,
and a bundle right inside it stops the start with a message that names it. Move each bundle one
level down:

- With `API_KEYS`: into `<BUNDLES_DIR>/default/`.
- With a tenants file: into the folder of its tenant, `<BUNDLES_DIR>/<tenant>/`.
- A `BUNDLES_DIR` that names `bundles/` of this repository is no longer needed: the built-in bundles load on their own. Leave it empty.

The private bundle repository `contentgun-templates` already has this layout: its `bundles/` is a
`BUNDLES_DIR` with a folder per tenant.
