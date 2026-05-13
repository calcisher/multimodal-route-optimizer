#!/usr/bin/env bash
# Cloudflare Tunnel setup for spacedevelopers.net → local Flask app on :5001
#
# PRE-REQUISITES (do these in order before running):
#   1. Sign up at https://dash.cloudflare.com (free).
#   2. Add the site "spacedevelopers.net" to your Cloudflare account.
#   3. Copy the two Cloudflare nameservers Cloudflare gives you, then in
#      Squarespace switch the domain's nameservers to those two.
#      Wait until Cloudflare shows the site as "Active" (usually <15 min,
#      can take up to 24h). Refresh the dashboard until it flips.
#   4. Run:  cloudflared tunnel login
#      This opens a browser; pick spacedevelopers.net to authorize.
#
# Then run this script:
#   bash deploy/setup_tunnel.sh

set -euo pipefail

DOMAIN="spacedevelopers.net"
TUNNEL_NAME="spacedevelopers-prod"
LOCAL_URL="http://127.0.0.1:5001"
CONFIG_DIR="$HOME/.cloudflared"
LAUNCHAGENT_DIR="$HOME/Library/LaunchAgents"
LAUNCHAGENT_LABEL="net.spacedevelopers.cloudflared"
LAUNCHAGENT_PLIST="$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/spacedevelopers"

mkdir -p "$LOG_DIR" "$LAUNCHAGENT_DIR" "$CONFIG_DIR"

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "ERROR: cloudflared not on PATH. Run: brew install cloudflared" >&2
    exit 1
fi

if [ ! -f "$CONFIG_DIR/cert.pem" ]; then
    echo "ERROR: $CONFIG_DIR/cert.pem missing. Run 'cloudflared tunnel login' first." >&2
    exit 1
fi

echo "==> Checking for existing tunnel '$TUNNEL_NAME'..."
EXISTING_ID=$(cloudflared tunnel list --output json 2>/dev/null \
    | python3 -c "import json,sys; data=json.load(sys.stdin); print(next((t['id'] for t in data if t['name']=='$TUNNEL_NAME'), ''))")

if [ -n "$EXISTING_ID" ]; then
    echo "    Found tunnel $TUNNEL_NAME ($EXISTING_ID) — reusing."
    TUNNEL_ID="$EXISTING_ID"
else
    echo "==> Creating tunnel '$TUNNEL_NAME'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list --output json \
        | python3 -c "import json,sys; data=json.load(sys.stdin); print(next(t['id'] for t in data if t['name']=='$TUNNEL_NAME'))")
fi

CREDS_FILE="$CONFIG_DIR/$TUNNEL_ID.json"
if [ ! -f "$CREDS_FILE" ]; then
    echo "ERROR: tunnel credentials file $CREDS_FILE not found." >&2
    exit 1
fi

echo "==> Writing $CONFIG_DIR/config.yml"
cat > "$CONFIG_DIR/config.yml" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CREDS_FILE

ingress:
  - hostname: $DOMAIN
    service: $LOCAL_URL
  - hostname: www.$DOMAIN
    service: $LOCAL_URL
  - service: http_status:404
EOF

echo "==> Creating DNS records (CNAME $DOMAIN → tunnel)"
cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN" || echo "  (record may already exist — continuing)"
cloudflared tunnel route dns "$TUNNEL_NAME" "www.$DOMAIN" || echo "  (record may already exist — continuing)"

echo "==> Writing LaunchAgent plist for cloudflared"
CLOUDFLARED_PATH=$(command -v cloudflared)
cat > "$LAUNCHAGENT_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LAUNCHAGENT_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$CLOUDFLARED_PATH</string>
        <string>--no-autoupdate</string>
        <string>tunnel</string>
        <string>--config</string>
        <string>$CONFIG_DIR/config.yml</string>
        <string>run</string>
        <string>$TUNNEL_NAME</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>TUNNEL_METRICS</key>
        <string>127.0.0.1:36500</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/cloudflared.out.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/cloudflared.err.log</string>
</dict>
</plist>
EOF

echo "==> Loading LaunchAgent"
launchctl bootout "gui/$(id -u)/$LAUNCHAGENT_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$LAUNCHAGENT_PLIST"

sleep 4
echo "==> Status:"
launchctl print "gui/$(id -u)/$LAUNCHAGENT_LABEL" 2>/dev/null | grep -E "state|pid|last exit code" | head -3 || true

echo ""
echo "==> Done. The tunnel is up. Test from another machine or your phone (off Wi-Fi):"
echo "    curl https://$DOMAIN/api/health"
echo ""
echo "Logs:"
echo "    tail -f $LOG_DIR/cloudflared.err.log"
echo "    tail -f $LOG_DIR/error.log    # gunicorn"
