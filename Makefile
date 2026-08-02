VERSION := 0.5
IMAGE_NAME := clanker-cage
TAG = $(IMAGE_NAME):$(VERSION)


.PHONY: build
build:
	docker build -f Dockerfile -t $(TAG) .

.PHONY: run
run:
	docker run --rm -it \
	  --user 1000:1000 \
	  --cap-drop ALL \
	  --security-opt no-new-privileges \
	  --read-only \
	  --tmpfs /tmp:rw,nosuid,size=512m \
	  --pids-limit 256 --memory 8g --cpus 4 \
	  -v "$PWD":/workspace:rw \
	  -v claude-state-strategerium:/home/agent/.claude \
	  -v "$HOME/.claude/skills":/home/agent/.claude/skills:ro \
	  -e CLAUDE_CODE_OAUTH_TOKEN \
	  -w /workspace \
	  $(TAG) claude
