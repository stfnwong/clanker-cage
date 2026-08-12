# clanker/Makefile
# The control room for your agent container image.

# ─── Configuration ───────────────────────────────────────────
IMAGE_NAME     := clanker
REGISTRY       ?= ghcr.io/your-username   # Set your default registry
GIT_HASH       := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")
DATE_TAG       := $(shell date +%Y%m%d)
IMAGE_TAG      ?= $(DATE_TAG)-$(GIT_HASH)
PLATFORM       ?= linux/amd64
CONTAINER_TOOL ?= docker

# ─── Build ───────────────────────────────────────────────────
.PHONY: build
build: ## Build the container image
	$(CONTAINER_TOOL) build \
		--platform $(PLATFORM) \
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

.PHONY: prune
prune: ## Aggressively remove all unused Docker data (careful!)
	$(CONTAINER_TOOL) system prune -a --volumes

# ─── Install the clanker host script ─────────────────────────
.PHONY: install
install: ## Install the clanker script to ~/local/bin
	@mkdir -p $(HOME)/local/bin
	@cp clanker $(HOME)/local/bin/clanker
	@chmod +x $(HOME)/local/bin/clanker
	@echo "clanker installed to $(HOME)/local/bin/clanker"
	@echo "Make sure $(HOME)/local/bin is on your PATH."

# ─── Help ────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
