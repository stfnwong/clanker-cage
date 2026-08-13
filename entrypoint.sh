#!/bin/bash
# entrypoint.sh - Adapt container user to match host UID/GID at runtime
set -e

USERNAME="${USERNAME:-clanker}"
WORKSPACE="${WORKSPACE:-/workspace}"

# If CLANKER_UID/GID not set, just use the default user
if [ -z "${CLANKER_UID}" ] || [ -z "${CLANKER_GID}" ]; then
    exec gosu "${USERNAME}" "$@"
fi

# If already running as the target UID, skip modifications
if [ "$(id -u)" = "${CLANKER_UID}" ]; then
    exec gosu "${USERNAME}" "$@"
fi

# Modify the existing user to match the host
usermod -u "${CLANKER_UID}" "${USERNAME}" 2>/dev/null || true
groupmod -g "${CLANKER_GID}" "${USERNAME}" 2>/dev/null || true
usermod -g "${CLANKER_GID}" "${USERNAME}" 2>/dev/null || true

# Fix home directory ownership
chown -R "${CLANKER_UID}:${CLANKER_GID}" "/home/${USERNAME}" 2>/dev/null || true

# Drop to the user
exec gosu "${USERNAME}" "$@"
