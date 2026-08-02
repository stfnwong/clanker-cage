#!/usr/bin/env bash
# clanker — run a containerised Claude Code session against the current project.
#
#   clanker              ephemeral interactive session in this project
#   clanker shell        ephemeral shell, no agent
#   clanker up           long-lived container for this project
#   clanker exec [cmd]   join the long-lived container (default: bash)
#   clanker attach       join it with a Claude session
#   clanker task "..."   headless one-shot, stream-json on stdout
#   clanker down         stop and remove the long-lived container
#   clanker ps           show container and volumes for this project
#   clanker purge        delete this project's state volumes (destructive)
#
# The image is built elsewhere (see the tooling repo's Makefile). This script
# only decides *what to mount* and *under what constraints*.

set -euo pipefail


IMAGE=${AGENT_IMAGE:-clanker-cage:dev}
DOCKER=${AGENT_DOCKER:-docker}
NETWORK=${AGENT_NETWORK:-bridge}     # set to 'none' once a proxy exists
MEMORY=${AGENT_MEMORY:-8g}
CPUS=${AGENT_CPUS:-4}
PIDS=${AGENT_PIDS:-256}

die() { printf 'clanker: %s\n' "$*" >&2; exit 1; }

# --- identify the project --------------------------------------------------
# Prefer the git toplevel so `clanker` works from any subdirectory.
PROJECT_DIR=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_DIR=$(cd "$PROJECT_DIR" && pwd -P)

[[ "$PROJECT_DIR" == "$HOME" || "$PROJECT_DIR" == "/" ]] &&
  die "refusing to mount $PROJECT_DIR; run from inside a project"

# Path hash keeps two checkouts of the same repo (worktrees!) distinct.
SLUG=$(basename "$PROJECT_DIR" | tr -c '[:alnum:]._-' '-' | cut -c1-32)
HASH=$(printf '%s' "$PROJECT_DIR" | sha1sum | cut -c1-8)
KEY="${SLUG}-${HASH}"

NAME="clanker-${KEY}"
STATE_VOL="clanker-state-${KEY}"
CACHE_VOL="clanker-cache-${KEY}"

UID_=$(id -u); GID_=$(id -g)
TTY_FLAG=(); [[ -t 1 ]] && TTY_FLAG=(-t)

# --- flags -----------------------------------------------------------------
security=(
  --user "${UID_}:${GID_}"
  --cap-drop ALL
  --security-opt no-new-privileges
  --read-only
  --tmpfs /tmp:rw,nosuid,size=512m
  --pids-limit "$PIDS"
  --memory "$MEMORY"
  --cpus "$CPUS"
  --network "$NETWORK"
)

mounts=(
  # Same absolute path inside and out: compile_commands.json, backtraces and
  # ccache entries stay valid on both sides of the boundary.
  -v "${PROJECT_DIR}:${PROJECT_DIR}"
  # Stable alias so skills and docs can say /project regardless of checkout.
  -v "${PROJECT_DIR}:/project"
  -v "${STATE_VOL}:/home/clanker/.claude"
  -v "${CACHE_VOL}:/home/clanker/.cache"
  -w "${PROJECT_DIR}"
)
# Personal skills, read-only, only if the host directory exists.
[[ -d "$HOME/.claude/skills" ]] &&
  mounts+=( -v "$HOME/.claude/skills:/home/clanker/.claude/skills:ro" )

env_=(
  -e CLAUDE_CODE_OAUTH_TOKEN          # value inherited, never baked in
  -e HOME=/home/clanker
  -e "PROJECT_DIR=${PROJECT_DIR}"
  -e CCACHE_DIR=/home/clanker/.cache/ccache
  -e CARGO_HOME=/home/clanker/.cache/cargo
  -e "TERM=${TERM:-xterm-256color}"
)

run_flags=( "${security[@]}" "${mounts[@]}" "${env_[@]}" )

running() { [[ -n "$($DOCKER ps -q -f "name=^${NAME}$")" ]]; }

require_image() {
  $DOCKER image inspect "$IMAGE" >/dev/null 2>&1 ||
    die "image $IMAGE not found; run 'make build' in the tooling repo"
}

check_auth() {
  [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]] && return 0
  $DOCKER volume inspect "$STATE_VOL" >/dev/null 2>&1 && return 0
  printf 'clanker: no token and no saved state; you will be asked to log in.\n' >&2
  printf "clanker: for headless use, run 'claude setup-token' on the host.\n" >&2
}

# --- subcommands -----------------------------------------------------------
cmd=${1:-run}; shift || true

case "$cmd" in
  run)
    require_image; check_auth
    exec $DOCKER run --rm -i "${TTY_FLAG[@]}" "${run_flags[@]}" "$IMAGE" claude "$@"
    ;;
  shell)
    require_image
    exec $DOCKER run --rm -i "${TTY_FLAG[@]}" "${run_flags[@]}" "$IMAGE" bash "$@"
    ;;
  up)
    require_image; check_auth
    running && { echo "clanker: ${NAME} already running"; exit 0; }
    $DOCKER run -d --name "$NAME" "${run_flags[@]}" "$IMAGE" sleep infinity >/dev/null
    echo "clanker: ${NAME} up  (${PROJECT_DIR})"
    ;;
  down)
    $DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
    echo "clanker: ${NAME} down"
    ;;
  exec)
    running || die "${NAME} is not running; try 'clanker up'"
    exec $DOCKER exec -i "${TTY_FLAG[@]}" -w "$PROJECT_DIR" "$NAME" "${@:-bash}"
    ;;
  attach)
    running || die "${NAME} is not running; try 'clanker up'"
    exec $DOCKER exec -i "${TTY_FLAG[@]}" -w "$PROJECT_DIR" "$NAME" claude "$@"
    ;;
  task)
    [[ $# -ge 1 ]] || die 'usage: clanker task "prompt"'
    require_image; check_auth
    exec $DOCKER run --rm -i "${run_flags[@]}" "$IMAGE" \
      claude -p "$*" --output-format stream-json --verbose
    ;;
  ps)
    echo "project: $PROJECT_DIR"
    echo "key:     $KEY"
    $DOCKER ps -a -f "name=^${NAME}$" \
      --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
    $DOCKER volume ls -f "name=${STATE_VOL}" -f "name=${CACHE_VOL}"
    ;;
  purge)
    read -r -p "delete ${STATE_VOL} and ${CACHE_VOL}? [y/N] " a
    [[ "$a" == "y" ]] || die "aborted"
    $DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
    $DOCKER volume rm "$STATE_VOL" "$CACHE_VOL" >/dev/null
    ;;
  help|-h|--help)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "unknown subcommand '$cmd' (try: clanker help)"
    ;;
esac

