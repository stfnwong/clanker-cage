# docker.py
import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, List, Dict
from .config import Config
from .session import Session


class DockerManager:
    """Handles Docker image build and container run."""

    def __init__(self, config: Config):
        self.config = config

    def _image_exists(self, image_tag: str) -> bool:
        """Check if a Docker image exists locally."""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image_tag],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except FileNotFoundError:
            raise RuntimeError("Docker command not found. Please install Docker.")

    def build_image_if_needed(self) -> None:
        """Build the clanker image if it's not present."""
        image_tag = self.config.image_tag
        if self._image_exists(image_tag):
            print(f"Image {image_tag} already exists.")
            return

        dockerfile_dir = self.config.dockerfile_dir
        print(f"Building Docker image {image_tag} from {dockerfile_dir}...")
        try:
            subprocess.run(
                ["docker", "build", "-t", image_tag, str(dockerfile_dir)],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Docker build failed: {e}")

    def _build_mount_args(self, session: Session) -> List[str]:
        """Construct the list of -v mount arguments for docker run."""
        cfg = self.config
        project_root = cfg.project_root
        mounts = [
            # Workspace: read-write, with optional delegated on macOS
            f"{project_root}:/workspace:{cfg.workspace_mount_opts}",
            # Package caches
            f"{cfg.cache_dir / 'pip'}:/home/clanker/.cache/pip:rw",
            f"{cfg.cache_dir / 'npm'}:/home/clanker/.cache/npm:rw",
            # Global skills (read-only)
            f"{cfg.skills_dir}:/etc/clanker/skills:ro",
        ]

        # Project-specific skills override
        project_skills = project_root / ".clanker" / "skills"
        if project_skills.is_dir():
            mounts.append(f"{project_skills}:/workspace/.clanker/skills:ro")

        # Project config file if present
        project_config = project_root / ".clanker" / "config"
        if project_config.is_file():
            mounts.append(f"{project_config}:/workspace/.clanker/config:ro")

        # Shadow .venv if present (keep host's venv out of container)
        if (project_root / ".venv").is_dir():
            volume_name = f"clanker-venv-{project_root.name}"
            mounts.append(f"{volume_name}:/workspace/.venv")

        # Session directory (mounted for transcript logging)
        session.mkdir()
        mounts.append(f"{session.session_dir}:/session:rw")

        # Provider socket (Linux only, socket mode)
        if cfg.provider_mode == "socket":
            socket_host = Path(cfg.provider_socket_host).expanduser()
            socket_host.parent.mkdir(parents=True, exist_ok=True)
            mounts.append(f"{socket_host}:{cfg.provider_socket_container}:rw")

        # API key secret (tmpfs + temp file)
        secret_tmp = None
        key_file = Path(cfg.secrets_dir) / "provider.key"
        if key_file.exists():
            secret_tmp = self._create_tmp_key(key_file)
            mounts.append(f"{secret_tmp}:/run/secrets/provider.key:ro")

        return mounts, secret_tmp

    def _create_tmp_key(self, key_file: Path) -> str:
        """Copy API key to a temporary file to mount read-only."""
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(key_file.read_bytes())
        tmp.close()
        return tmp.name

    def _build_network_args(self) -> List[str]:
        """Return network-related docker run args."""
        cfg = self.config
        if cfg.provider_mode == "socket":
            # No network needed; socket mount provides access
            return ["--network", "none"]
        else:
            # Default bridge network for TCP (macOS)
            return []

    def _build_environment(self, session: Session, initial_prompt: Optional[str]) -> List[str]:
        """Construct environment variables for the container."""
        cfg = self.config
        env = [
            f"CLANKER_PROVIDER={cfg.provider}",
            f"CLANKER_PROVIDER_MODE={cfg.provider_mode}",
            f"CLANKER_PROVIDER_SOCKET={cfg.provider_socket_container}",
            f"CLANKER_PROVIDER_ENDPOINT={cfg.provider_endpoint}",
            f"CLANKER_MODEL={cfg.model}",
            "CLANKER_PROVIDER_KEY_FILE=/run/secrets/provider.key",
            f"CLANKER_PROJECT_NAME={cfg.project_root.name}",
            f"CLANKER_SESSION_ID={session.session_id}",
            f"CLANKER_SESSION_DIR=/session",
            f"CLANKER_UID={os.getuid()}",
            f"CLANKER_GID={os.getgid()}",
            f"TERM={os.environ.get('TERM', 'xterm-256color')}",
            f"LINES={os.environ.get('LINES', '')}",
            f"COLUMNS={os.environ.get('COLUMNS', '')}",
        ]
        if initial_prompt:
            env.append(f"CLANKER_INITIAL_PROMPT={initial_prompt}")
        return env

    def run(self, session: Session, initial_prompt: Optional[str] = None) -> int:
        """
        Run the clanker container interactively.

        Args:
            session: Session object with session_dir, session_id.
            initial_prompt: If provided, the container will run agent-loop
                            with this prompt instead of a bash shell.

        Returns:
            Exit code of the docker run command.
        """
        self.build_image_if_needed()

        cfg = self.config
        container_name = f"clanker-{session.session_id}"

        # Write current container pointer for neovim plugin
        pointer_file = cfg.cache_dir / "current-container"
        pointer_file.write_text(container_name)

        mounts, secret_tmp = self._build_mount_args(session)
        network_args = self._build_network_args()
        env_args = []
        for e in self._build_environment(session, initial_prompt):
            env_args.extend(["-e", e])

        # Build command
        cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--hostname", "clanker",
        ]
        cmd.extend(network_args)
        cmd.extend(env_args)
        for m in mounts:
            cmd.extend(["-v", m])
        cmd.extend(["-it", cfg.image_tag])

        # Choose command inside container
        if initial_prompt:
            # Run agent-loop directly (requires entrypoint to handle this)
            cmd.append("/usr/local/bin/agent-loop")
        else:
            cmd.extend(["/bin/bash", "--rcfile", "/etc/clanker/bashrc"])

        # Cleanup temp key after run
        try:
            # Run docker, inherit terminal
            result = subprocess.run(cmd, check=False)
            return result.returncode
        finally:
            if secret_tmp:
                Path(secret_tmp).unlink(missing_ok=True)
            pointer_file.unlink(missing_ok=True)
