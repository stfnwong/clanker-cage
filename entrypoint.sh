#!/bin/bash
# entrypoint.sh
# If CLANKER_UID is set, create a user with that UID
if [ -n "${CLANKER_UID}" ] && [ -n "${CLANKER_GID}" ]; then
    # Modify existing user's UID/GID or create new
    usermod -u "${CLANKER_UID}" clanker 2>/dev/null || \
        useradd -m -s /bin/bash -u "${CLANKER_UID}" -g "${CLANKER_GID}" clanker
    # Adjust ownership of home and workspace
    chown -R clanker:clanker /home/clanker /workspace 2>/dev/null || true
fi

# Drop privileges and run the provided command
exec gosu clanker "$@"
