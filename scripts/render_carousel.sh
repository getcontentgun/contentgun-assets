#!/usr/bin/env bash
#
# Renders a five-slide sample carousel against a running service, one request per slide:
# the cover, three content slides and the closing slide, written to carousel/01.png to
# carousel/05.png.
#
# Usage: API_KEY=... bash scripts/render_carousel.sh
#        FORMAT=1:1 API_KEY=... bash scripts/render_carousel.sh
#
# BUNDLE names the bundle whose carousel templates render the slides, HANDLE replaces the
# handle on every slide, TARGET_DIR the output directory and BASE_URL the service address.
# ICON names an SVG file that every content slide shows under its text, and ICON_COLOR=text
# draws it in the text color instead of the accent color.

set -euo pipefail

readonly base_url="${BASE_URL:-http://127.0.0.1:8010}"
readonly api_key="${API_KEY:?Set API_KEY to one of the keys from .env}"
readonly bundle="${BUNDLE:-demo}"
readonly handle="${HANDLE:-@contentgun}"
readonly format="${FORMAT:-4:5}"
readonly target_dir="${TARGET_DIR:-carousel}"
readonly icon="${ICON:-}"
readonly icon_color="${ICON_COLOR:-}"
readonly pages=5

# Through a file rather than an argument: a base64 icon is quickly larger than the maximum
# length of a single command line argument.
payload_file="$(mktemp)"
trap 'rm -f "${payload_file}"' EXIT

render_slide() {
    local number="$1"
    local template="$2"
    shift 2

    local target
    printf -v target '%s/%02d.png' "${target_dir}" "${number}"

    python3 -c '
import base64
import json
import sys

template, fmt, handle, icon_path, icon_color, *fields = sys.argv[1:]

payload = {"template": template, "format": fmt}
payload.update(field.split("=", 1) for field in fields)
payload["handle"] = handle

if icon_path and template.endswith("/carousel-content"):
    with open(icon_path, "rb") as stream:
        payload["icon"] = base64.b64encode(stream.read()).decode("ascii")
    if icon_color:
        payload["icon_color"] = icon_color

print(json.dumps(payload))
' "${template}" "${format}" "${handle}" "${icon}" "${icon_color}" "$@" >"${payload_file}"

    local status
    status="$(curl -sS -o "${target}" -w '%{http_code}' \
        -X POST "${base_url}/v1/render" \
        -H "Authorization: Bearer ${api_key}" \
        -H "Content-Type: application/json" \
        --data-binary "@${payload_file}")"

    if [[ "${status}" != "200" ]]; then
        echo "Slide ${number} (${template}) failed with HTTP ${status}:" >&2
        cat "${target}" >&2
        echo >&2
        rm -f "${target}"
        exit 1
    fi

    echo "Wrote ${target}"
}

mkdir -p "${target_dir}"

render_slide 1 "${bundle}/carousel-cover" \
    "topic=Arbeitsweise" \
    "headline=Drei Gewohnheiten für bessere Beiträge." \
    "subline=Und wie du sie in deinen Alltag holst."

render_slide 2 "${bundle}/carousel-content" \
    "pagination=2/${pages}" \
    "headline=Erst das Ziel, dann der Text" \
    "body=Wer vor dem Schreiben festlegt, was ein Beitrag erreichen soll, braucht weniger Runden in der Abstimmung und trifft den Ton schneller."

render_slide 3 "${bundle}/carousel-content" \
    "pagination=3/${pages}" \
    "headline=Ein Gedanke pro Beitrag" \
    "body=Drei Botschaften auf einmal bleiben als keine hängen. Lass eine Idee tragen und heb dir die anderen für die nächsten Beiträge auf."

render_slide 4 "${bundle}/carousel-content" \
    "pagination=4/${pages}" \
    "headline=Fester Rhythmus statt Zufall" \
    "body=Wer jede Woche zur selben Zeit veröffentlicht, plant leichter voraus und gibt dem Publikum einen Grund, wiederzukommen."

render_slide 5 "${bundle}/carousel-closing" \
    "pagination=5/${pages}" \
    "headline=Klein anfangen, regelmäßig liefern." \
    "cta=Welche Gewohnheit probierst du als Erstes aus?"
