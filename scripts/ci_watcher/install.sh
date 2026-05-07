#!/usr/bin/env bash
# CI Watcher — one-time install on m4-air.
# Run with sudo. Idempotent — safe to re-run.

set -euo pipefail

readonly USER_NAME="_ci-watcher"
readonly OPT_DIR="/opt/ci-watcher"
readonly VAR_DIR="/var/lib/ci-watcher"
readonly ETC_DIR="/etc/ci-watcher"
readonly LOG_FILE="/var/log/ci-watcher.log"
readonly REPO_URL="git@github.com:dubnubdubnub/dubIS.git"
readonly REPO_DIR="$VAR_DIR/repo"
readonly LD_DIR="/Library/LaunchDaemons"

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (sudo $0)" >&2
  exit 1
fi

echo "==> Step 1: Create _ci-watcher user (if not exists)"
if ! dscl . -read "/Users/$USER_NAME" >/dev/null 2>&1; then
  next_uid=$(($(dscl . -list /Users UniqueID | awk '{print $2}' | sort -n | tail -1) + 1))
  dscl . -create "/Users/$USER_NAME"
  dscl . -create "/Users/$USER_NAME" UserShell /usr/bin/false
  dscl . -create "/Users/$USER_NAME" RealName "CI Watcher"
  dscl . -create "/Users/$USER_NAME" UniqueID "$next_uid"
  dscl . -create "/Users/$USER_NAME" PrimaryGroupID 20
  dscl . -create "/Users/$USER_NAME" NFSHomeDirectory "$VAR_DIR"
  echo "  created user $USER_NAME (uid=$next_uid)"
else
  echo "  user $USER_NAME already exists"
fi

echo "==> Step 2: Create directories"
install -d -m 0755 "$OPT_DIR"
install -d -m 0750 -o "$USER_NAME" -g staff "$VAR_DIR"
install -d -m 0750 -o "$USER_NAME" -g staff "$ETC_DIR"
touch "$LOG_FILE"
chown "$USER_NAME":staff "$LOG_FILE"
chmod 0640 "$LOG_FILE"

echo "==> Step 3: Generate webhook secret (if not exists)"
if [[ ! -s "$ETC_DIR/secret" ]]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$ETC_DIR/secret"
  chmod 0600 "$ETC_DIR/secret"
  chown "$USER_NAME":staff "$ETC_DIR/secret"
  echo "  generated new secret"
else
  echo "  secret already exists"
fi

echo "==> Step 4: Generate SSH key (if not exists)"
sudo -u "$USER_NAME" mkdir -p "$VAR_DIR/.ssh"
sudo -u "$USER_NAME" chmod 700 "$VAR_DIR/.ssh"
if [[ ! -f "$VAR_DIR/.ssh/id_ed25519" ]]; then
  sudo -u "$USER_NAME" ssh-keygen -t ed25519 -N "" -C "ci-watcher@m4-air" -f "$VAR_DIR/.ssh/id_ed25519"
  echo
  echo "  ===================================================================="
  echo "  ADD THIS PUBLIC KEY AS A DEPLOY KEY ON dubnubdubnub/dubIS (with write access):"
  cat "$VAR_DIR/.ssh/id_ed25519.pub"
  echo "  ===================================================================="
  echo
  read -r -p "  Press Enter once the deploy key is registered..."
fi

echo "==> Step 5: Configure SSH to trust github.com"
sudo -u "$USER_NAME" ssh-keyscan -t ed25519 github.com >> "$VAR_DIR/.ssh/known_hosts" 2>/dev/null || true

echo "==> Step 6: Clone repo (if not exists)"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  sudo -u "$USER_NAME" git clone "$REPO_URL" "$REPO_DIR"
  sudo -u "$USER_NAME" git -C "$REPO_DIR" config user.name "Isaac Chiu (CI watcher)"
  sudo -u "$USER_NAME" git -C "$REPO_DIR" config user.email "isaac.chiu+ci-watcher@impossible.place"
  echo "  cloned $REPO_URL"
else
  sudo -u "$USER_NAME" git -C "$REPO_DIR" fetch --all --prune
  echo "  repo already cloned, fetched latest"
fi

echo "==> Step 7: Ensure ci-watcher-log branch exists"
if ! sudo -u "$USER_NAME" git -C "$REPO_DIR" rev-parse --verify origin/ci-watcher-log >/dev/null 2>&1; then
  sudo -u "$USER_NAME" git -C "$REPO_DIR" checkout --orphan ci-watcher-log
  sudo -u "$USER_NAME" git -C "$REPO_DIR" rm -rf . >/dev/null 2>&1 || true
  sudo -u "$USER_NAME" mkdir -p "$REPO_DIR/data"
  sudo -u "$USER_NAME" touch "$REPO_DIR/data/ci-watcher-log.jsonl"
  sudo -u "$USER_NAME" git -C "$REPO_DIR" add data/ci-watcher-log.jsonl
  sudo -u "$USER_NAME" git -C "$REPO_DIR" commit -m "init: ci-watcher-log branch"
  sudo -u "$USER_NAME" git -C "$REPO_DIR" push -u origin ci-watcher-log
  sudo -u "$USER_NAME" git -C "$REPO_DIR" checkout main
  echo "  created ci-watcher-log branch on origin"
else
  echo "  ci-watcher-log branch already exists"
fi

echo "==> Step 8: Create Python venv and install deps"
if [[ ! -d "$OPT_DIR/venv" ]]; then
  /opt/homebrew/bin/python3.12 -m venv "$OPT_DIR/venv"
fi
"$OPT_DIR/venv/bin/pip" install --quiet --upgrade pip
"$OPT_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/scripts/ci_watcher/requirements.txt"

# scripts/ci_watcher is a regular Python package — no symlink needed.

echo "==> Step 9: Install claude CLI (if not present)"
if ! command -v claude >/dev/null 2>&1; then
  /opt/homebrew/bin/npm install -g @anthropic-ai/claude-code
fi
echo "  claude CLI: $(command -v claude || echo NOT FOUND)"

echo
echo "  ===================================================================="
echo "  Now log in to Claude as the _ci-watcher user. Run this in another"
echo "  terminal:"
echo "      sudo -u $USER_NAME -H /opt/homebrew/bin/claude login"
echo "  Open the printed URL in your browser to complete device-code auth."
echo "  ===================================================================="
read -r -p "  Press Enter once 'claude login' has succeeded..."

echo "==> Step 10: Install LaunchDaemons"
install -m 0644 -o root -g wheel \
  "$REPO_DIR/scripts/ci_watcher/place.impossible.ci-watcher-listener.plist" \
  "$LD_DIR/place.impossible.ci-watcher-listener.plist"
install -m 0644 -o root -g wheel \
  "$REPO_DIR/scripts/ci_watcher/place.impossible.ci-watcher-worker.plist" \
  "$LD_DIR/place.impossible.ci-watcher-worker.plist"

launchctl unload "$LD_DIR/place.impossible.ci-watcher-listener.plist" 2>/dev/null || true
launchctl unload "$LD_DIR/place.impossible.ci-watcher-worker.plist" 2>/dev/null || true
launchctl load "$LD_DIR/place.impossible.ci-watcher-worker.plist"
launchctl load "$LD_DIR/place.impossible.ci-watcher-listener.plist"
echo "  daemons loaded"

echo "==> Step 11: Configure Tailscale Funnel"
if ! /opt/homebrew/bin/tailscale serve status 2>/dev/null | grep -q "127.0.0.1:9090"; then
  /opt/homebrew/bin/tailscale serve --bg --https=443 http://127.0.0.1:9090
  /opt/homebrew/bin/tailscale funnel --bg 443
fi
funnel_url=$(/opt/homebrew/bin/tailscale funnel status | awk '/https:/ {print $1; exit}')
echo "  funnel: $funnel_url"

echo
echo "  ===================================================================="
echo "  INSTALL COMPLETE."
echo "  Public webhook URL: ${funnel_url}/webhook"
echo "  Webhook secret (copy into the GitHub webhook config):"
cat "$ETC_DIR/secret"
echo "  ===================================================================="
echo
echo "  Next: configure the webhook on GitHub. From your dev box, run:"
echo
echo "    secret=\$(ssh m4-air sudo cat $ETC_DIR/secret)"
echo "    gh api repos/dubnubdubnub/dubIS/hooks -X POST \\"
echo "      -F config[url]=${funnel_url}/webhook \\"
echo "      -F config[content_type]=json \\"
echo "      -F config[secret]=\"\$secret\" \\"
echo "      -f events[]=workflow_run"
