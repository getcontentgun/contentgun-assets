#!/usr/bin/env bash
#
# Renders one sample card against a running service and writes it to sample.png.
# For the card templates (it always sends a subline); carousels use render_carousel.sh.
#
# Usage: API_KEY=... bash scripts/render_sample.sh ["headline"] ["subline"]
#
# TEMPLATE takes a template id. Set IMAGE to a local image file to render it as the
# background, which also switches the template to demo/card-image unless TEMPLATE names
# another. TEMPLATE=demo/card-image without IMAGE renders the template's fallback image.

set -euo pipefail

readonly base_url="${BASE_URL:-http://127.0.0.1:8010}"
readonly api_key="${API_KEY:?Set API_KEY to one of the keys from .env}"
readonly headline="${1:-Gute Vorlagen sparen jede Woche Stunden.}"
readonly subline="${2:-Text rein, fertige Karte raus.}"
readonly handle="${HANDLE:-@contentgun}"
readonly image="${IMAGE:-}"
readonly template="${TEMPLATE:-}"
readonly target="${TARGET:-sample.png}"

# Through a file rather than an argument: a base64 image is quickly larger than the
# maximum length of a command line.
payload_file="$(mktemp)"
trap 'rm -f "${payload_file}"' EXIT

python3 -c '
import base64
import json
import sys

headline, subline, handle, image_path, template = sys.argv[1:6]

payload = {
    "format": "4:5",
    "headline": headline,
    "subline": subline,
    "handle": handle,
}

if image_path:
    with open(image_path, "rb") as stream:
        payload["image"] = base64.b64encode(stream.read()).decode("ascii")
    payload["template"] = template or "demo/card-image"
elif template:
    payload["template"] = template

print(json.dumps(payload))
' "${headline}" "${subline}" "${handle}" "${image}" "${template}" >"${payload_file}"

status="$(curl -sS -o "${target}" -w '%{http_code}' \
    -X POST "${base_url}/v1/render" \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    --data-binary "@${payload_file}")"

if [[ "${status}" != "200" ]]; then
    echo "Render failed with HTTP ${status}:" >&2
    cat "${target}" >&2
    echo >&2
    rm -f "${target}"
    exit 1
fi

echo "Wrote ${target}"
