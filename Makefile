# ---------------------------------------------------------------------------
# clanker container
#
#   make build     build the image (stamped; no-ops when nothing changed)
#   make agent     ephemeral interactive Claude Code session
#   make up/down   long-lived container you can join from several tmux panes
#   make exec      join the running container with a shell
#   make task      headless one-shot run:  make task PROMPT="fix the ..."
#
# Requires GNU make. Recipe lines are TABS.
# ---------------------------------------------------------------------------

SHELL := /bin/bash
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

# --- configuration ---------------------------------------------------------

DOCKER      ?= docker          # make DOCKER=podman ... also works
IMAGE       ?= clanker-cage
TAG         ?= dev
NAME        ?= clanker-cage
IMAGE_REF   := $(IMAGE):$(TAG)

# Mount the project at the SAME absolute path inside and outside the
# container so compile_commands.json, backtraces and ccache paths resolve
# on both sides. Do not "tidy" this into /workspace.
PROJECT_DIR := $(CURDIR)

UID := $(shell id -u)
GID := $(shell id -g)

# Per-project state. Auth token + session history live here; keep it scoped
# so a session in one project cannot read another's transcripts.
STATE_VOL := claude-state-$(notdir $(PROJECT_DIR))
CACHE_VOL := claude-cache-$(notdir $(PROJECT_DIR))

MEMORY    ?= 8g
CPUS      ?= 4
PIDS      ?= 256

# Phase 3 hardening: set NETWORK=none once the credential proxy exists.
NETWORK   ?= bridge

# Empty this in CI where there is no terminal.
TTY       ?= -t

STAMP := .make/image.stamp

# --- container flags -------------------------------------------------------

SECURITY_FLAGS := \
  --user $(UID):$(GID) \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,nosuid,size=512m \
  --pids-limit $(PIDS) \
  --memory $(MEMORY) \
  --cpus $(CPUS) \
  --network $(NETWORK)

MOUNT_FLAGS := \
  -v $(PROJECT_DIR):$(PROJECT_DIR) \
  -v $(STATE_VOL):/home/agent/.claude \
  -v $(CACHE_VOL):/home/agent/.cache \
  -v $(HOME)/.claude/skills:/home/agent/.claude/skills:ro \
  -w $(PROJECT_DIR)

# CLAUDE_CODE_OAUTH_TOKEN is passed by name only: the value is inherited from
# your shell at run time and never baked into an image layer.
ENV_FLAGS := \
  -e CLAUDE_CODE_OAUTH_TOKEN \
  -e HOME=/home/agent \
  -e CCACHE_DIR=/home/agent/.cache/ccache \
  -e CARGO_HOME=/home/agent/.cache/cargo \
  -e TERM=$(TERM)

RUN_FLAGS := $(SECURITY_FLAGS) $(MOUNT_FLAGS) $(ENV_FLAGS)

# --- build -----------------------------------------------------------------

.PHONY: build
build: $(STAMP)  ## Build the image if Dockerfile or policy changed

$(STAMP): Dockerfile managed-settings.json | .make
	$(DOCKER) build \
	  --build-arg UID=$(UID) \
	  --build-arg GID=$(GID) \
	  -t $(IMAGE_REF) .
	@$(MAKE) --no-print-directory doctor
	@touch $@

.make:
	@mkdir -p $@

.PHONY: rebuild
rebuild:  ## Force a clean rebuild, ignoring the layer cache
	$(DOCKER) build --no-cache \
	  --build-arg UID=$(UID) --build-arg GID=$(GID) \
	  -t $(IMAGE_REF) .
	@mkdir -p .make && touch $(STAMP)

.PHONY: doctor
doctor:  ## Validate the baked managed-settings.json inside the image
	@echo ">> claude doctor (managed policy validation)"
	@$(DOCKER) run --rm $(IMAGE_REF) claude doctor

# --- running ---------------------------------------------------------------

.PHONY: agent
agent: build check-auth  ## Ephemeral interactive session (the usual entry point)
	$(DOCKER) run --rm -i $(TTY) $(RUN_FLAGS) $(IMAGE_REF) claude

.PHONY: shell
shell: build  ## Ephemeral shell in the image, no agent
	$(DOCKER) run --rm -i $(TTY) $(RUN_FLAGS) $(IMAGE_REF) bash

.PHONY: up
up: build check-auth  ## Start a long-lived detached container
	@if [ -n "$$($(DOCKER) ps -q -f name=^$(NAME)$$)" ]; then \
	  echo ">> $(NAME) already running"; \
	else \
	  $(DOCKER) run -d --name $(NAME) $(RUN_FLAGS) $(IMAGE_REF) sleep infinity; \
	  echo ">> $(NAME) up"; \
	fi

.PHONY: down
down:  ## Stop and remove the long-lived container
	-$(DOCKER) rm -f $(NAME) 2>/dev/null
	@echo ">> $(NAME) down"

.PHONY: restart
restart: down up  ## Recycle the long-lived container

.PHONY: exec
exec: require-running  ## Join the running container with a shell
	$(DOCKER) exec -i $(TTY) -w $(PROJECT_DIR) $(NAME) bash

.PHONY: attach
attach: require-running  ## Join the running container with a Claude session
	$(DOCKER) exec -i $(TTY) -w $(PROJECT_DIR) $(NAME) claude

# --- headless --------------------------------------------------------------

.PHONY: task
task: build check-auth  ## Headless run:  make task PROMPT="..."
	@test -n "$(PROMPT)" || { echo "error: set PROMPT=\"...\""; exit 2; }
	$(DOCKER) run --rm -i $(RUN_FLAGS) $(IMAGE_REF) \
	  claude -p "$(PROMPT)" --output-format stream-json --verbose

# --- inspection ------------------------------------------------------------

.PHONY: ps
ps:  ## Show container and volume state
	@$(DOCKER) ps -a -f name=^$(NAME)$$ --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
	@$(DOCKER) volume ls -f name=$(STATE_VOL) -f name=$(CACHE_VOL)

.PHONY: logs
logs:  ## Follow logs from the long-lived container
	$(DOCKER) logs -f $(NAME)

# --- teardown --------------------------------------------------------------

.PHONY: clean
clean: down  ## Remove container and build stamp (keeps auth + caches)
	-rm -f $(STAMP)

.PHONY: purge
purge: clean  ## DESTRUCTIVE: also delete state volumes; forces re-authentication
	@read -r -p "Delete $(STATE_VOL) and $(CACHE_VOL)? [y/N] " a; \
	  [ "$$a" = "y" ] || { echo "aborted"; exit 1; }
	-$(DOCKER) volume rm $(STATE_VOL) $(CACHE_VOL)

# --- guards ----------------------------------------------------------------

.PHONY: check-auth
check-auth:
	@if [ -z "$$CLAUDE_CODE_OAUTH_TOKEN" ] \
	   && ! $(DOCKER) volume inspect $(STATE_VOL) >/dev/null 2>&1; then \
	  echo "warning: no CLAUDE_CODE_OAUTH_TOKEN and no saved state in $(STATE_VOL)."; \
	  echo "         you will be prompted to log in; run 'claude setup-token'"; \
	  echo "         on the host for a non-interactive token."; \
	fi

.PHONY: require-running
require-running:
	@test -n "$$($(DOCKER) ps -q -f name=^$(NAME)$$)" \
	  || { echo "error: $(NAME) is not running; try 'make up'"; exit 1; }

# --- help ------------------------------------------------------------------

.PHONY: help
help:  ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'
