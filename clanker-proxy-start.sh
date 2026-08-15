#!/bin/bash
# clanker-proxy-start — loads API key and launches provider proxy
KEY_FILE="${CLANKER_KEY_FILE:-$HOME/.config/clanker/secrets/deepseek.key}"
if [[ -f "$KEY_FILE" ]]; then
    export DEEPSEEK_API_KEY="$(cat "$KEY_FILE")"
else
    echo "No API key file found at $KEY_FILE" >&2
    exit 1
fi

# Adjust path to your actual binary
exec "$HOME/.local/bin/clanker-provider-proxy" --tcp --port 11434
