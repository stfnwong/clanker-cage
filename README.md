# CLANKER CAGE

Container image to hold an agent. 

This repo is just a docker image that in which I want to run agents. The reason I want this is to be able to control agent access to other things on my laptop. For now this is specialised around `Claude` because thats what I have a subscription for but the idea would be to generalise this somewhat as time goes by.


## Overview 

The idea is something like this 

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              HOST MACHINE                                       │
│                                                                                 │
│  ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐     │
│  │     Neovim         │    │     Terminal       │    │   User Service     │     │
│  │  clanker.nvim      │    │                    │    │  (LaunchAgent /    │     │
│  │  plugin            │    │  $ clanker         │    │   systemd unit)    │     │
│  └─────────┬──────────┘    └─────────┬──────────┘    └─────────┬──────────┘     │
│            │                         │                         │                │
│            │ docker exec             │ docker run              │ manages        │
│            │ (reads pointer file)    │ (builds image,          │                │
│            │                         │  mounts project,        │                │
│            │                         │  sets env, launches)    │                │
│            │                         │                         │                │
│            │                         ▼                         ▼                │
│            │                ┌─────────────────────────────────────────────┐     │
│            │                │           Provider Proxy (Rust)             │     │
│            │                │                                             │     │
│            │                │   Linux: Unix socket                        │     │
│            │                │     ~/.cache/clanker/provider.sock          │     │
│            │                │                                             │     │
│            │                │   macOS: TCP 0.0.0.0:11434                  │     │
│            │                │                                             │     │
│            │                │   Holds DEEPSEEK_API_KEY                    │     │
│            │                │   /health endpoint                          │     │
│            │                └──────────────────────┬──────────────────────┘     │
│            │                                       │                            │
│            │                        API calls (streaming)                       │
│            │                                       │                            │
└────────────┼───────────────────────────────────────┼────────────────────────────┘
             │                                       │
             │                                       ▼
             │                          ┌─────────────────────────┐
             │                          │      DeepSeek API       │
             │                          │  api.deepseek.com/v1    │
             │                          └─────────────────────────┘
             │
             │  (docker exec -i <container> agent-loop ...)
             │
┌────────────▼─────────────────────────────────────────────────────────────────────┐
│                          CONTAINER (clanker:latest)                              │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐     │
│  │                              /workspace                                 │     │
│  │                       (project mounted read-write)                      │     │
│  └─────────────────────────────────────────────────────────────────────────┘     │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐     │
│  │                           agent-loop.py                                 │     │
│  │                                                                         │     │
│  │   ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐          │     │
│  │   │  Construct   │───▶│  Call LLM    │───▶│  Parse Response  │          │     │
│  │   │  Messages    │    │  (streaming) │    │                  │          │     │
│  │   └──────────────┘    └──────┬───────┘    └────────┬─────────┘          │     │
│  │                              │                     │                    │     │
│  │                              │                     │ Tool calls?        │     │
│  │                              │                     ▼                    │     │
│  │   ┌──────────────┐    ┌──────▼───────┐    ┌──────────────────┐          │     │
│  │   │  Execute Tool│◀───┤  Tool Loop   │    │  Final Answer    │          │     │
│  │   │  (read_file, │    │              │    │  (text response) │          │     │
│  │   │   run_shell, │    └──────────────┘    └──────────────────┘          │     │
│  │   │   search,    │                                                      │     │
│  │   │   list)      │                                                      │     │
│  │   └──────────────┘                                                      │     │
│  └─────────────────────────────────────────────────────────────────────────┘     │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐     │
│  │                        Provider Connection                              │     │
│  │                                                                         │     │
│  │   Linux: /var/run/provider.sock (mounted from host)                     │     │
│  │   macOS: http://host.docker.internal:11434 (TCP)                        │     │
│  │                                                                         │     │
│  │   Mode set by CLANKER_PROVIDER_MODE env var                             │     │
│  └─────────────────────────────────────────────────────────────────────────┘     │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐     │
│  │                     Skills & Configuration                              │     │
│  │                                                                         │     │
│  │   /etc/clanker/skills/SKILL.md          (global, from image)            │     │
│  │   /workspace/.clanker/skills/SKILL.md   (project-specific, if exists)   │     │
│  │   /workspace/.clanker/config            (project config)                │     │
│  └─────────────────────────────────────────────────────────────────────────┘     │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐     │
│  │                          User & Permissions                             │     │
│  │                                                                         │     │
│  │   entrypoint.sh: adapts UID/GID to match host                           │     │
│  │   gosu: drops privileges to clanker user                                │     │
│  │   /run/secrets/provider.key: mounted tmpfs (if direct API mode)         │     │
│  └─────────────────────────────────────────────────────────────────────────┘     │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```


## Usage 
The idea is that the container is a kind of throwaway sandbox in which we can constrain the agent. The original motivation is my paranoia about data loss, so at the time of writing there isn't any specific defense against prompt injection, data exfiltration, etc. The imagined workflow looks like 

- Clone this repo 
- From here build the image (`make build`)
- Take `clanker` and copy it somewhere so that its on `$PATH`, perhaps somewhere like `/usr/local/bin`, or `~/.local/bin`.
- The idea of `clanker` is that it gives a set of commands which put a new `clanker-cage` into the current project directory. So `cd to/where/project/is && clanker up && clanker attach` is the intended workflow.



# Proxy setup
Communication between the agent and container is proxied through a separate process. This is implemented as a long running process in a platform specific way. 


## On Linux 
Copy `proxy/clanker-proxy.service` to somewhere like `~/.config/systemd/user/clanker-proxy.service`. Then do

```bash
systemctl --user enable --now clanker-proxy.service
```

to manually start the service.


## On OSX

Copy `proxy/com.clanker.provider-proxy.plist`  to `~/Library/LaunchAgents/com.clanker.provider-proxy.plist`. Load the agent once by doing

```bash
launchctl load ~/Library/LaunchAgents/com.clanker.provider-proxy.plist
```

On newer OSX this might be 

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clanker.provider-proxy.plist
```


