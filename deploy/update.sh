#!/usr/bin/env bash
# Pull latest code from GitHub, sync deps, restart gunicorn, smoke-test prod.
# Cloudflare Tunnel keeps running across this — it doesn't need to restart.
#
#   bash deploy/update.sh           # standard redeploy
#   bash deploy/update.sh --check   # show what would change without doing it

set -euo pipefail

PROJECT_DIR="/Users/kerembozdag/498/multimodal-route-optimizer"
LABEL="net.spacedevelopers.routeoptimizer"
HEALTH_URL="https://spacedevelopers.net/api/health"

cd "$PROJECT_DIR"

if [ "${1:-}" = "--check" ]; then
    git fetch origin >/dev/null
    echo "==> Local HEAD:  $(git rev-parse --short HEAD)"
    echo "==> Remote HEAD: $(git rev-parse --short origin/main)"
    echo "==> Incoming commits:"
    git log --oneline HEAD..origin/main || echo "    (none — already up to date)"
    echo ""
    echo "==> Local modifications:"
    git status --short
    exit 0
fi

echo "==> Step 1/4: git pull"
git fetch origin
if ! git diff --quiet HEAD origin/main; then
    git pull --ff-only origin main
else
    echo "    Already at origin/main."
fi

echo "==> Step 2/4: uv sync (install/upgrade deps)"
uv sync --quiet

echo "==> Step 3/4: restart gunicorn"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 3
STATE=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | awk '/state = /{print $3; exit}')
PID=$(launchctl print   "gui/$(id -u)/$LABEL" 2>/dev/null | awk '/pid = /{print $3; exit}')
echo "    state=$STATE pid=$PID"

echo "==> Step 4/4: smoke test $HEALTH_URL"
for i in 1 2 3 4 5; do
    CODE=$(curl -s -o /tmp/health.json -w "%{http_code}" "$HEALTH_URL" || echo "000")
    if [ "$CODE" = "200" ]; then
        echo "    HTTP 200 — $(cat /tmp/health.json)"
        echo ""
        echo "✅ Deployed. Now at $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"
        exit 0
    fi
    echo "    attempt $i: HTTP $CODE — waiting 2s..."
    sleep 2
done

echo "❌ Smoke test failed. Check logs:"
echo "    tail -50 ~/Library/Logs/spacedevelopers/error.log"
exit 1
