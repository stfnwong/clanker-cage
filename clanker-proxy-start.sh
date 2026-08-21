#!/bin/bash
# clanker-proxy-start — loads API key and launches provider proxy
KEY_FILE="${CLANKER_KEY_FILE:-$HOME/.config/clanker/secrets/deepseek.key}"
if [[ -f "$KEY_FILE" ]]; then
    export DEEPSEEK_API_KEY="$(cat "$KEY_FILE")"
else
    echo "No API key file found at $KEY_FILE" >&2
    exit 1
fi

export CLANKER_PROXY_PORT=11434
# Adjust path to your actual binary
exec "$HOME/dev/agentic/clanker_cage/target/release/clanker-provider-proxy" --tcp --port $CLANKER_PROXY_PORT
