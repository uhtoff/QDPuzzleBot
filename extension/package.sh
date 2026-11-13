#!/usr/bin/env bash
# Build dist/puzzle-chat-<version>.zip (Chrome) from an explicit include list.
# `package.sh firefox` instead builds dist/firefox/ (load it via about:debugging) and
# dist/puzzle-chat-firefox-<version>.zip (upload to AMO), with a manifest derived from
# manifest.json by scripts/firefoxManifest.js.
set -euo pipefail
cd "$(dirname "$0")"

target=${1:-}
if [[ -n "$target" && "$target" != firefox ]]; then
  echo "usage: $0 [firefox]" >&2
  exit 2
fi

version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')

INCLUDE=(manifest.json LICENSE-NOTICE.txt)
while IFS= read -r f; do INCLUDE+=("$f"); done < <(find src -type f \( -name '*.js' -o -name '*.html' -o -name '*.css' \) | sort)
while IFS= read -r f; do INCLUDE+=("$f"); done < <(find icons -type f -name '*.png' | sort)

mkdir -p dist

if [[ "$target" == firefox ]]; then
  dir="dist/firefox"
  out="dist/puzzle-chat-firefox-${version}.zip"
  rm -rf "$dir"
  mkdir -p "$dir"
  for f in "${INCLUDE[@]}"; do
    mkdir -p "$dir/$(dirname "$f")"
    cp "$f" "$dir/$f"
  done
  node scripts/write-firefox-manifest.js "$dir/manifest.json"
  cp ../LICENSE "$dir/LICENSE"
  rm -f "$out"
  (cd "$dir" && zip -X -q -r "$OLDPWD/$out" .)
  echo "$dir"
  echo "$out"
  exit 0
fi

out="dist/puzzle-chat-${version}.zip"

# Full licence text from the repository root, shipped as LICENSE.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp ../LICENSE "$stage/LICENSE"

rm -f "$out"
zip -X -q "$out" "${INCLUDE[@]}"
(cd "$stage" && zip -X -q "$OLDPWD/$out" LICENSE)
echo "$out"
