# clanker/Makefile
# The control room for your agent container image.

# ─── Configuration ───────────────────────────────────────────
IMAGE_NAME     := clanker
REGISTRY       ?= ghcr.io/your-username   # Set your default registry
GIT_HASH       := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
DATE_TAG       := $(shell date +%Y%m%d)
IMAGE_TAG      ?= $(DATE_TAG)-$(GIT_HASH)
CONTAINER_TOOL ?= docker

INSTALL_DIR ?= $(HOME)/.local/bin

# Detect host operating system
UNAME_S := $(shell uname -s)

# Detect host architecture (optional)
UNAME_M := $(shell uname -m)

# Conditionally set variables based on OS
ifeq ($(UNAME_S),Darwin)
    CONTAINER_TOOL ?= docker          # Could also be podman with machine
    MOUNT_OPTS   := :delegated
    # On Apple Silicon, the default docker platform is linux/arm64
    # PLATFORM?=linux/arm64            # if you want to enforce
else ifeq ($(UNAME_S),Linux)
    CONTAINER_TOOL ?= docker
    MOUNT_OPTS   :=
    # PLATFORM?=linux/amd64
endif

# If you want to auto-set PLATFORM based on architecture:
# map uname -m to Docker platform (simplified)
ifeq ($(UNAME_M),x86_64)
    HOST_PLATFORM := linux/amd64
else ifeq ($(UNAME_M),aarch64)
    HOST_PLATFORM := linux/arm64
else ifeq ($(UNAME_M),arm64)
    HOST_PLATFORM := linux/arm64
endif

# Use the auto-detected PLATFORM unless overridden
PLATFORM ?= $(HOST_PLATFORM)



# ─── Provider Proxy ───────────────────────────────────────────────────

# Platform detection for Rust builds
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

# Map uname to Rust target
ifeq ($(UNAME_S),Darwin)
    RUST_TARGET := aarch64-apple-darwin
    ifeq ($(UNAME_M),x86_64)
        RUST_TARGET := x86_64-apple-darwin
    endif
else
    RUST_TARGET := x86_64-unknown-linux-gnu
    ifeq ($(UNAME_M),aarch64)
        RUST_TARGET := aarch64-unknown-linux-gnu
    endif
endif


PROXY_SRC := src/main.rs
PROXY_BINARY := target/release/clanker-provider-proxy

.PHONY: build-proxy
build-proxy: $(PROXY_BINARY)

$(PROXY_BINARY): $(PROXY_SRC) Cargo.toml Cargo.lock
	@echo "Building provider proxy for $(RUST_TARGET)..."
	@cargo build --release


# ─── Build ───────────────────────────────────────────────────
.PHONY: build
build: build-proxy   ## Build the container image
	$(CONTAINER_TOOL) build \
		--platform $(PLATFORM) \
		-f docker/Dockerfile \
		-t $(IMAGE_NAME):$(IMAGE_TAG) \
		-t $(IMAGE_NAME):latest \
		.

.PHONY: build-multi
build-multi: ## Build for both amd64 and arm64 (requires buildx)
	$(CONTAINER_TOOL) buildx build \
		--platform linux/amd64,linux/arm64 \
		-t $(IMAGE_NAME):$(IMAGE_TAG) \
		-t $(IMAGE_NAME):latest \
		--push \
		.


# ─── Tag & Push ─────────────────────────────────────────────
.PHONY: tag
tag: ## Tag the current latest image with a version tag
	@echo "Tagging $(IMAGE_NAME):latest as $(IMAGE_NAME):$(IMAGE_TAG)"
	$(CONTAINER_TOOL) tag $(IMAGE_NAME):latest $(IMAGE_NAME):$(IMAGE_TAG)

.PHONY: push
push: ## Push the version-tagged and latest images to registry
	@[ -n "$(REGISTRY)" ] || (echo "REGISTRY not set" && exit 1)
	$(CONTAINER_TOOL) tag $(IMAGE_NAME):latest $(REGISTRY)/$(IMAGE_NAME):latest
	$(CONTAINER_TOOL) tag $(IMAGE_NAME):$(IMAGE_TAG) $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
	$(CONTAINER_TOOL) push $(REGISTRY)/$(IMAGE_NAME):latest
	$(CONTAINER_TOOL) push $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)

# ─── Testing & Shell ────────────────────────────────────────
.PHONY: shell
shell: ## Open a shell in a fresh container using the latest image
	$(CONTAINER_TOOL) run --rm -it \
		-v $(PWD):/workspace:delegated \
		--network none \
		$(IMAGE_NAME):latest /bin/bash

.PHONY: test-agent
test-agent: ## Run the agent-loop with a simple prompt (smoke test)
	$(CONTAINER_TOOL) run --rm \
		-v $(PWD):/workspace:ro \
		-e CLANKER_PROVIDER_MODE=offline \
		$(IMAGE_NAME):latest \
		agent-loop --oneshot "Say 'clanker operational' if you can read this."

.PHONY: lint
lint: ## Lint the Dockerfile and scripts (requires hadolint, shellcheck)
	hadolint Dockerfile
	shellcheck agent-loop entrypoint.sh
	# Add more checks as needed

# ─── Cleanup ─────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove the built images (keeps registry copies safe)
	$(CONTAINER_TOOL) rmi $(IMAGE_NAME):$(IMAGE_TAG) || true
	$(CONTAINER_TOOL) rmi $(IMAGE_NAME):latest || true
	cargo clean

.PHONY: prune
prune: ## Aggressively remove all unused Docker data (careful!)
	$(CONTAINER_TOOL) system prune -a --volumes


# ─── Installation ───────────────────────────────────────────
.PHONY: install
install: build-proxy ## Build and install everything (container image + host tools)
	@mkdir -p $(INSTALL_DIR)
	@ln -sf $(CURDIR)/clanker $(INSTALL_DIR)/clanker
	@ln -sf $(CURDIR)/$(PROXY_BINARY) $(INSTALL_DIR)/provider-proxy
	@echo "Installed:"
	@echo "  clanker        -> $(INSTALL_DIR)/clanker"
	@echo "  provider-proxy -> $(INSTALL_DIR)/provider-proxy"
	@echo ""
	@echo "Make sure $(INSTALL_DIR) is on your PATH."


# ─── Help ────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
