# clanker CLI — usage & testing guide

This guide walks through the **python CLI** (`cli/clanker/cli.py`), the
`clanker` entrypoint that owns the full session lifecycle:

```
preflight -> proxy management -> session creation -> docker launch -> cleanup
```

Everything below is derived from the current source. Commands assume the
package is installed (e.g. `uv sync` in `cli/`, which exposes the
`clanker` console script from `pyproject.toml`) and that Docker is
available.

---

## Command surface

| Command                          | What it does                                             |
|----------------------------------|----------------------------------------------------------|
| `clanker` / `clanker run`        | Drop into an interactive shell inside the container       |
| `clanker run --no-proxy`         | Same, but offline (no provider proxy)                     |
| `clanker run --stop-proxy`       | Start session, then stop proxy when it exits              |
| `clanker prompt <text>`          | One-shot agent run; exits after answering                 |
| `clanker prompt --pipe`          | Read one-shot prompt from stdin                           |
| `clanker prompt -e`              | Compose one-shot prompt in `$EDITOR`                      |
| `clanker resume <session-id>`    | Continue a previous session                               |
| `clanker log <session-id>`       | Render a session transcript to stdout                     |
| `clanker sessions`               | List sessions, newest first, with status                  |
| `clanker status`                 | Show proxy health + resolved config                       |
| `clanker stop`                   | Stop the provider proxy service                           |

Every command (`run`, `prompt`, `resume`, `log`, `sessions`, `status`,
`stop`) accepts `-p, --project PATH` — the project root mounted as
`/workspace` (default `.`). Model/provider overrides and proxy flags
are per-command:

| Option                 | Commands                 | Meaning                                          |
|------------------------|--------------------------|--------------------------------------------------|
| `-p, --project PATH`   | all                      | Project root mounted as `/workspace` (default `.`) |
| `--model NAME`         | `run`, `prompt`          | Override model       (env `CLANKER_MODEL`)       |
| `--provider NAME`      | `run`, `prompt`          | Override provider    (env `CLANKER_PROVIDER`)    |
| `--no-proxy`           | `run`, `prompt`, `resume`| Run offline, no proxy socket/TCP                 |
| `--stop-proxy`         | `run`, `resume`          | Stop proxy after the session exits               |
| `--pipe`               | `prompt`                 | Read one-shot prompt from stdin                  |
| `-e, --editor`         | `prompt`                 | Compose one-shot prompt in `$EDITOR`             |

---

## Configuration & environment

Defaults come from `cli/clanker/config.py`. Overridable via env vars:

| Env var              | Default                                   | Used for                          |
|----------------------|-------------------------------------------|-----------------------------------|
| `CLANKER_MODEL`      | `deepseek-chat`                           | default model                     |
| `CLANKER_PROVIDER`   | `deepseek`                                | default provider                  |
| `CLANKER_CACHE`      | `~/.cache/clanker`                        | session dirs, pip/npm caches, socket |
| `CLANKER_SECRETS_DIR`| `~/.config/clanker/secrets`               | `provider.key` + socket location  |

Platform behavior (set from the host automatically):

* **Linux** — provider reached over a **unix socket**
  (`~/.cache/clanker/provider.sock`), container runs with
  `--network none`. Proxy managed via a **systemd user service**
  `clanker-proxy.service`.
* **macOS** — provider reached over **TCP**
  (`http://host.docker.internal:11434`). Proxy managed via a
  **launchd agent** `com.clanker.provider-proxy`.

---

## First-run prerequisites

1. **Docker** installed and the daemon running.
2. **Provider API key** in a file at
   `$CLANKER_SECRETS_DIR/provider.key` (default
   `~/.config/clanker/secrets/provider.key`). This is mounted read-only
   into the container at `/run/secrets/provider.key`.
3. **(Optional) provider proxy** running. If it is healthy
   (`/health` → `ok`, or the socket exists) the CLI reuses it;
   otherwise it starts it, and falls back to offline mode if that fails.

The image `clanker:latest` is built **automatically** the first time if
it does not exist (build context = repo root).

---

## Quick start — interactive shell

```bash
# From your project directory:
clanker

# Equivalent explicit form:
clanker run

# Different project, offline (no proxy needed):
clanker run -p /path/to/project --no-proxy
```

You'll see a banner with the project, provider, model, mode, session id,
container name (`clanker-<session-id>`), and proxy state, then a bash
shell inside `/workspace`.

---

## Quick start — one-shot prompt

```bash
# Inline prompt:
clanker prompt "Summarize the files in the current directory"

# Multi-word argument list is joined into one prompt:
clanker prompt List the top-level crates and their roles

# From stdin (e.g. pipe a file or context):
cat context.md | clanker prompt --pipe

# Compose the prompt in $EDITOR:
clanker prompt -e

# All offline variations work too:
clanker prompt --no-proxy "List the files"
```

---

## Resuming & inspecting

```bash
# See what sessions exist (prints status / created / model / session-id):
clanker sessions

# Copy a session id from the list, then:
clanker resume 20260816-143022-3f2a

# Render a session's transcript as plain text:
clanker log 20260816-143022-3f2a
```

`resume` refuses to restart a session already marked `finished`.

---

## Health & lifecycle

```bash
# Show the resolved config and whether the proxy is healthy:
clanker status
#   platform     : linux
#   project      : /current/dir
#   provider     : deepseek
#   model        : deepseek-chat
#   proxy mode   : socket
#   proxy health : down            (<-- exit code 1 when down)

# Stop the provider proxy service:
clanker stop

# Start a session, then stop the proxy when it exits:
clanker run --stop-proxy
```

---

## Suggested testing flow

1. `clanker status` — confirm the platform resolves and the proxy is
   down (healthy `false` → exit code 1). This is the cheapest smoke
   test and requires no proxy/Docker image.
2. `clanker sessions` — confirms session dir resolution; prints
   `No sessions yet.` on first run.
3. `clanker run --no-proxy` — exercises image build, config, session
   creation, and the mount logic without needing a provider key or
   proxy. You should land in a shell with `/workspace` = your project.
4. `clanker prompt --no-proxy "Hello"` — exercises the one-shot agent
   path (note: the agent-loop needs a live provider to answer).
5. `clanker sessions` again — verify a new session was created.
6. `clanker log <session-id>` — verify the transcript renders.
7. With a proxy + key in place: `clanker run` then `clanker prompt ...`,
   and finally `clanker run --stop-proxy` to verify proxy cleanup.

### Exit codes
* `0` — success.
* `1` — generic failure; `status` uses it when the proxy is unhealthy;
  `resume`/`log` use it when a session id is missing or already finished.
* `2` — `prompt` given no text (no arg, no `--pipe`, no `-e`).
