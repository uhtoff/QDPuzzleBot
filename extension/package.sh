#!/usr/bin/env bash
# Build dist/puzzle-chat-<version>.zip from an explicit include list.
set -euo pipefail
cd "$(dirname "$0")"

version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
out="dist/puzzle-chat-${version}.zip"

# Full licence text from the repository root, shipped as LICENSE.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp ../LICENSE "$stage/LICENSE"

INCLUDE=(manifest.json LICENSE-NOTICE.txt)
while IFS= read -r f; do INCLUDE+=("$f"); done < <(find src -type f \( -name '*.js' -o -name '*.html' -o -name '*.css' \) | sort)
while IFS= read -r f; do INCLUDE+=("$f"); done < <(find icons -type f -name '*.png' | sort)

mkdir -p dist
rm -f "$out"
zip -X -q "$out" "${INCLUDE[@]}"
(cd "$stage" && zip -X -q "$OLDPWD/$out" LICENSE)
echo "$out"
