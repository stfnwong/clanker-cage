FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git openssh-client \
      build-essential clang clang-format clang-tidy lld cmake ninja-build ccache \
      python3 python3-venv pipx ripgrep jq gdb \
 && rm -rf /var/lib/apt/lists/*

# rust for linear_mercatoria
RUN curl --proto '=https' -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.xx

# pin the CLI; do NOT let it auto-update inside a supposedly reproducible image
RUN npm install -g @anthropic-ai/claude-code@2.1.x
ENV DISABLE_AUTOUPDATER=1

# your private artifact infrastructure
ENV PIP_INDEX_URL=https://pypi.internal/simple \
    CARGO_REGISTRIES_KELLNR_INDEX=... \
    USE_BUILTIN_RIPGREP=0

# policy the agent cannot edit its way out of
RUN mkdir -p /etc/claude-code
COPY managed-settings.json /etc/claude-code/managed-settings.json

RUN useradd -m -u 1000 clanker
USER clanker
