FROM debian:trixie-slim

ARG CLANKER_UID=1000
ARG CLANKER_GID=1000
ARG USERNAME=clanker

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git openssh-client \
      build-essential clang clang-format clang-tidy lld cmake ninja-build ccache gosu \
      python3 python3-pip python3-venv pipx ripgrep jq gdb npm\
 && rm -rf /var/lib/apt/lists/*

RUN if ! getent group "$CLANKER_GID" >/dev/null; then groupadd -g "$CLANKER_GID" "$USERNAME"; fi \
	&& useradd -o -m -u "$CLANKER_UID" -g "$CLANKER_GID" "$USERNAME" \
	&& mkdir -p /home/"$USERNAME"/.claude /home/"$USERNAME"/.cache /project \
	&& chown -R "$CLANKER_UID:$CLANKER_GID" /home/"$USERNAME"

# For agent-loop
# Create venv and install httpx
RUN python3 -m venv /opt/clanker-venv
RUN /opt/clanker-venv/bin/pip install httpx
ENV PATH="/opt/clanker-venv/bin:$PATH"

#RUN groupadd -g ${CLANKER_GID} ${USERNAME} && useradd -m -u ${CLANKER_UID} -g ${CLANKER_GID} ${USERNAME}

# TODO: worry about rust later
# rust for linear_mercatoria
#RUN curl --proto '=https' -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.xx

# private artifact infrastructure
ENV PIP_INDEX_URL=https://pypi.internal/simple \
    CARGO_REGISTRIES_KELLNR_INDEX=... \
    USE_BUILTIN_RIPGREP=0

COPY bashrc /etc/clanker/bashrc

# Add entrypoint
# default, will be overridden
#RUN useradd -m -s /bin/bash -u 1000 builder  
COPY entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

WORKDIR /workspace
USER ${USERNAME}

# TODO: why do I need this entrypoint?
#ENTRYPOINT ["/usr/local/bin/entrypoint"]
