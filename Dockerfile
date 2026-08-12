FROM debian:trixie-slim

ARG UID=1000
ARG GID=1000
ARG USERNAME=clanker

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git openssh-client \
      build-essential clang clang-format clang-tidy lld cmake ninja-build ccache \
      python3 python3-venv pipx ripgrep jq gdb npm\
 && rm -rf /var/lib/apt/lists/*

RUN if ! getent group "$GID" >/dev/null; then groupadd -g "$GID" "$USERNAME"; fi \
	&& useradd -o -m -u "$UID" -g "$GID" "$USERNAME" \
	&& mkdir -p /home/"$USERNAME"/.claude /home/"$USERNAME"/.cache /project \
	&& chown -R "$UID:$GID" /home/"$USERNAME"

#RUN groupadd -g ${GID} ${USERNAME} && useradd -m -u ${UID} -g ${GID} ${USERNAME}

# rust for linear_mercatoria
RUN curl --proto '=https' -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.xx

# private artifact infrastructure
ENV PIP_INDEX_URL=https://pypi.internal/simple \
    CARGO_REGISTRIES_KELLNR_INDEX=... \
    USE_BUILTIN_RIPGREP=0

COPY bashrc /etc/clanker/bashrc

WORKDIR /workspace
USER ${USERNAME}


# pin the CLI; do NOT let it auto-update inside a supposedly reproducible image
#RUN npm install -g @anthropic-ai/claude-code@2.1.x
#ENV DISABLE_AUTOUPDATER=1





# policy the agent cannot edit its way out of
#RUN mkdir -p /etc/claude-code
#COPY managed-settings.json /etc/claude-code/managed-settings.json
#
#USER ${USERNAME}
