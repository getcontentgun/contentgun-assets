# Storage

With an S3-compatible bucket the service can do three more things:

- **Results as a URL.** A render or document request with `"output": "url"` gets JSON with a URL instead of the file, see [`POST /v1/render`](../render.md#output-as-a-url).
- **Uploads.** A caller asks for an upload URL and puts an image or an icon there with a plain `PUT`, then names it in a render request instead of sending base64, see [Uploads](../uploads.md).
- **Bundles from the bucket.** The bundles of the tenants come from the bucket instead of a local folder, and [`POST /v1/admin/reload`](../admin-reload.md) picks up a change without a restart.

The bucket stays private. Nothing is ever fetched from it directly: the service is the only way in
and out, downloads included, through [`/o/…`](../files.md) and [`/u/…`](../uploads.md#put-utoken).
Without storage the service works as before: results come back as the file, the upload endpoints
answer `501`, and the bundles of the tenants come from `BUNDLES_DIR`.

## Configuration

| Variable | Description |
|----------|-------------|
| `S3_ENDPOINT` | The S3 API of the provider, `https://fsn1.your-objectstorage.com` at Hetzner, `http://127.0.0.1:9010` for the RustFS of `compose.dev.yaml` |
| `S3_REGION` | The region, `fsn1` at Hetzner; RustFS takes any, such as `us-east-1` |
| `S3_BUCKET` | The bucket, created beforehand, see [Preparing a bucket](#preparing-a-bucket) |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | The credentials of the bucket |
| `PUBLIC_FILES_URL` | The address under which callers reach `/o/` and `/u/` of this service: `https://files.contentgun.de` in production, `http://127.0.0.1:8010` locally |
| `STORAGE_TTL_HOURS` | How long results and uploads stay available, `24` by default |

The first six belong together: all of them or none. Some but not all of them stop the start with
a message that names the empty ones. `PUBLIC_FILES_URL` only builds the URLs the service hands out;
whatever answers under it, a reverse proxy in production, must pass `/o/` and `/u/` on to the
service.

The service talks to the bucket with path-style addresses and sends checksums only where the S3
API requires them, which S3-compatible providers handle more reliably than the defaults of
boto3.

## What lies in the bucket

```
outputs/<tenant>/<random>/<file name>   results of "output": "url"
uploads/<tenant>/<upload id>            uploads, an image or an icon
templates/<tenant>/<bundle>/…           the bundles of the tenants
```

The random part of an output is 192 bits, so its URL cannot be guessed. Every object under
`outputs/` and `uploads/` carries its expiry as metadata.

## How long files live

Results and uploads are available for `STORAGE_TTL_HOURS` after they were stored. The service
checks the expiry on every access: an expired result is a `404`, an expired upload an unknown
upload. Deleting the objects is left to a lifecycle rule of the bucket, which removes everything
under `outputs/` and `uploads/` after a number of whole days that covers the expiry with a day to
spare, two days for the default of 24 hours. The service itself deletes nothing.

## Preparing a bucket

```
.venv/bin/python scripts/setup_storage.py
```

The script reads the settings from `.env`, creates the bucket unless it exists and sets the
lifecycle rules. Run it once per bucket. Where a provider takes no lifecycle rules through the S3
API, the script says so, and the rule has to be set in the provider's console instead: delete
objects with the prefixes `outputs/` and `uploads/` after the number of days the script names.

## Bundles from the bucket

With storage configured and `BUNDLES_DIR` empty, the bundles of the tenants come from
`templates/<tenant>/<bundle>/` in the bucket, in the layout of `BUNDLES_DIR`. The built-in bundles
still come from the image.

- At startup the service copies `templates/` into a local temporary folder and loads the tenants from it. A broken bundle stops the start like one in `BUNDLES_DIR`, and the message names its key in the bucket, `s3://contentgun/templates/acme/acme-brand/bundle.toml: …`. An unreachable bucket stops the start as well.
- [`POST /v1/admin/reload`](../admin-reload.md) copies again into a new folder, validates everything and only then switches. Until then, and when validation fails, the tenants and bundles in use stay active. The folder before stays on disk until the next reload, for requests that started earlier.
- A key that is no plain path, with `..`, `.` or an empty part, is never copied and stops the start or the reload.
- With `BUNDLES_DIR` set, the bundles come from there, storage or not. That is the way to work on bundles locally while outputs and uploads go to a bucket.

`scripts/put_bundles.py` copies the folder of one tenant into the bucket. The folder's name is the
tenant, `acme` here:

```
.venv/bin/python scripts/put_bundles.py /path/to/tenants/acme
```

It overwrites what is there and deletes nothing. A reload makes the service use it.

## Locally with RustFS

`compose.dev.yaml` has a bucket for development in the profile `storage`, RustFS on
`127.0.0.1:9010`, with its console on `127.0.0.1:9011`. Its credentials are `S3_ACCESS_KEY` and
`S3_SECRET_KEY` from `.env`. Where another program holds one of the two ports, compose answers
`port is already allocated`: `STORAGE_PORT` and `STORAGE_CONSOLE_PORT` in `.env` move them, and
`S3_ENDPOINT` then names the new `STORAGE_PORT`.

1. Put the storage settings into `.env`:

   ```
   S3_ENDPOINT=http://127.0.0.1:9010
   S3_REGION=us-east-1
   S3_BUCKET=contentgun
   S3_ACCESS_KEY=contentgun
   S3_SECRET_KEY=contentgun-dev-secret
   PUBLIC_FILES_URL=http://127.0.0.1:8010
   INTERNAL_KEY=<at least 32 characters, for the reload>
   ```

2. Start RustFS, prepare the bucket and start the service:

   ```
   docker compose -f compose.dev.yaml --profile storage up -d storage
   .venv/bin/python scripts/setup_storage.py
   bin/development
   ```

3. A card as a URL, then the card from it:

   ```
   curl -sS -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{"template": "demo/card", "headline": "Hallo", "output": "url"}' http://127.0.0.1:8010/v1/render
   curl -sS -o card.png "<url from the answer>"
   ```

4. An upload in a card:

   ```
   curl -sS -X POST -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8010/v1/uploads
   curl -sS -T photo.jpg "<upload_url from the answer>"
   curl -sS -o card.png -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{"template": "demo/card-image", "headline": "Hallo", "image_upload": "<upload_id>"}' http://127.0.0.1:8010/v1/render
   ```

5. A bundle from the bucket. `BUNDLES_DIR` stays empty for this, since a folder there wins over the bucket. With `API_KEYS` the one tenant is `default`, so the bundle goes into a folder of that name; any bundle does whose name differs from the built-in ones:

   ```
   mkdir -p /tmp/bucket-check/default
   cp -R /path/to/a-bundle /tmp/bucket-check/default/
   .venv/bin/python scripts/put_bundles.py /tmp/bucket-check/default
   curl -sS -X POST -H "Authorization: Bearer $INTERNAL_KEY" http://127.0.0.1:8010/v1/admin/reload
   curl -sS -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8010/v1/templates
   ```

   The reload answers with every tenant and the number of its templates, and the list holds the templates of the new bundle.

With the service in the container as well, compose hands it `S3_ENDPOINT_IN_COMPOSE` from `.env`
instead of `S3_ENDPOINT`, since `127.0.0.1` there is the container itself:

```
S3_ENDPOINT_IN_COMPOSE=http://storage:9000
```

```
docker compose -f compose.dev.yaml --profile storage up --build
```

`PUBLIC_FILES_URL` stays `http://127.0.0.1:8010`, or the port `PORT` moves compose to. The data
of RustFS lies in the volume `storage` and survives a restart;
`docker compose -f compose.dev.yaml --profile storage down -v` removes it.

## Errors

| Status | Error | Meaning |
|--------|-------|---------|
| `501` | `Storage is not configured on this server, so … cannot be used.` | `"output": "url"`, an upload or an upload id without storage |
| `503` | `The file storage is not available, retry later.` | The bucket did not answer, or answered with an error |

A `503` is worth retrying. The log line of the failure names the action, the start of the key
and the error code of the provider, never a credential and never the random part of a result's
URL.
