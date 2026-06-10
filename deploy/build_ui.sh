#!/usr/bin/env bash
# Bundle the React UI for production: concatenate the sources in the exact
# order the old <script> tags loaded them, strip JSX + minify with esbuild,
# and stamp a cache-busting ?v=hash into the HTML (Cloudflare edge-caches .js
# for ~2h when the origin sends no Cache-Control, so the hash is required for
# updates to reach users promptly).
#
#   bash deploy/build_ui.sh
#
# Output: UI/js/app.min.js (committed to git — the Mac mini deploy does a
# plain git pull and must not need node/npx). Run this after ANY edit under
# UI/js/, then commit the rebuilt bundle together with the source change.
set -euo pipefail
cd "$(dirname "$0")/.."

# Same order as the original script tags — later files reference earlier ones.
SRC=(
  UI/js/i18n.js
  UI/js/constants.js
  UI/js/utils.js
  UI/js/components/Filter.jsx
  UI/js/components/InlineMap.jsx
  UI/js/components/JourneyDetail.jsx
  UI/js/components/DetailShells.jsx
  UI/js/components/Badges.jsx
  UI/js/components/Share.jsx
  UI/js/components/AiSuggest.jsx
  UI/js/components/Cards.jsx
  UI/js/components/CityInput.jsx
  UI/js/components/Hub.jsx
  UI/js/components/BusFlightBusCard.jsx
  UI/js/components/SectionContent.jsx
  UI/js/app.jsx
)

TMP="$(mktemp -t multiroute-concat).jsx"
trap 'rm -f "$TMP"' EXIT
for f in "${SRC[@]}"; do
  cat "$f"
  printf '\n;\n'
done > "$TMP"

# --format=iife keeps the shared top-level scope the files rely on while
# letting --minify rename symbols safely inside the closure. React/ReactDOM/L
# stay as external globals (loaded from UI/js/vendor/ before the bundle).
npx -y esbuild "$TMP" \
  --format=iife --minify --target=es2018 \
  --outfile=UI/js/app.min.js --log-level=warning

HASH=$(shasum UI/js/app.min.js | cut -c1-10)
sed -i '' -E "s|js/app\.min\.js(\?v=[0-9a-f]*)?|js/app.min.js?v=$HASH|" "UI/Multi Route.html"
echo "Built UI/js/app.min.js ($(du -h UI/js/app.min.js | cut -f1 | tr -d ' ')) v=$HASH"
