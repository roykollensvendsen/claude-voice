#!/usr/bin/env bash
# Install claude-voice as a systemd user service listening on 127.0.0.1:8811.
# Creates ~/.config/claude-voice/env (mode 600) with a fresh token on first run.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
conf="$HOME/.config/claude-voice/env"
unit="$HOME/.config/systemd/user/claude-voice.service"

if systemctl --user show-environment | grep -q '^ANTHROPIC_API_KEY='; then
  echo "ANTHROPIC_API_KEY is set in the systemd user environment; the bridge would refuse to start." >&2
  exit 1
fi

(cd "$repo" && uv sync --locked -q)

# Funnel serves ports 443, 8443 and 10000; use 443, since some clients (claude.ai) cannot reach other ports.
public="${CLAUDE_VOICE_PUBLIC_HOSTS:-}"
if [[ -z "$public" ]] && command -v tailscale >/dev/null; then
  dns="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  public="$dns"
fi

if [[ ! -f "$conf" ]]; then
  mkdir -p "$(dirname "$conf")"
  ( umask 077
    {
      echo "CLAUDE_VOICE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
      echo "CLAUDE_VOICE_ROOT=${CLAUDE_VOICE_ROOT:-$HOME/src}"
      echo "CLAUDE_VOICE_PUBLIC_HOSTS=$public"
    } > "$conf" )
  echo "wrote $conf"
fi

mkdir -p "$(dirname "$unit")"
install -m 644 "$repo/deploy/claude-voice.service" "$unit"
systemctl --user daemon-reload
systemctl --user enable --now claude-voice.service
systemctl --user restart claude-voice.service

for _ in $(seq 25); do
  curl -fs http://127.0.0.1:8811/healthz >/dev/null && break
  sleep 0.2
done
echo "health: $(curl -fs http://127.0.0.1:8811/healthz || echo DOWN)"
echo "without token: HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8811/mcp) (expect 401)"
