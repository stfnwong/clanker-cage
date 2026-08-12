# /etc/clanker/bashrc - sourced when the container starts
# Restore default bashrc behavior
if [ -f /etc/bash.bashrc ]; then
    . /etc/bash.bashrc
fi
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi

# ─── Provider connectivity check ─────────────────────────────
if [ -S /var/run/provider.sock ]; then
    echo "  Agent: connected (socket proxy)"
    export CLANKER_PROVIDER_MODE="socket"
elif [ -f /run/secrets/provider.key ]; then
    echo "  Agent: connected (direct API key)"
    export CLANKER_PROVIDER_MODE="direct"
else
    echo "  Agent: offline (no provider configured)"
    echo "  Start the provider proxy on the host: clanker-proxy &"
    export CLANKER_PROVIDER_MODE="offline"
fi

# ─── Skill file discovery ────────────────────────────────────
# Priority: project .clanker/skills/ > global /etc/clanker/skills/
export CLANKER_SKILLS_PATH="/etc/clanker/skills"
if [ -d /workspace/.clanker/skills ]; then
    export CLANKER_SKILLS_PATH="/workspace/.clanker/skills"
fi

# ─── Convenience functions ───────────────────────────────────
# Talk to the agent
agent() {
    agent-loop "$@"
}

# Quick agent question
aq() {
    agent-loop --oneshot "$@"
}

# Apply agent's last suggestion to files
agent-apply() {
    agent-loop --apply-last
}

# ─── Welcome ─────────────────────────────────────────────────
echo ""
echo "  Skills: ${CLANKER_SKILLS_PATH}"
echo "  Type 'agent \"your request\"' to start working"
echo "  Type 'aq \"quick question\"' for one-shot answers"
echo ""
