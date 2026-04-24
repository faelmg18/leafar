import os
from pathlib import Path

from dotenv import dotenv_values


class Config:
    """Loads and holds all Leafar configuration values."""

    def __init__(self, project_path: str = "."):
        # Search for .env in: project dir -> ~/.leafar -> current dir
        locations = [
            Path(project_path) / ".env",
            Path.home() / ".leafar" / ".env",
            Path(".env"),
        ]
        _env_values: dict = {}
        for env_file in locations:
            if env_file.exists():
                _env_values = dotenv_values(env_file)
                # Set env vars (except ANTHROPIC_API_KEY) for subprocesses that need them.
                # ANTHROPIC_API_KEY is intentionally NOT set in os.environ so the claude
                # CLI subprocess uses its own OAuth session instead of the API key.
                for k, v in _env_values.items():
                    if k != "ANTHROPIC_API_KEY" and not os.environ.get(k):
                        os.environ[k] = v or ""
                break

        # Read the API key directly from the file (not from os.environ) to avoid
        # polluting the environment that gets inherited by the claude CLI subprocess.
        self.claude_api_key: str = _env_values.get("ANTHROPIC_API_KEY", "")
        self.figma_access_token: str = os.getenv("FIGMA_ACCESS_TOKEN", "")
        self.figma_client_id: str = os.getenv("FIGMA_CLIENT_ID", "")
        self.figma_client_secret: str = os.getenv("FIGMA_CLIENT_SECRET", "")
        self.github_token: str = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        self.android_package_name: str = os.getenv("ANDROID_PACKAGE_NAME", "")
        self.android_main_activity: str = os.getenv("ANDROID_MAIN_ACTIVITY", ".MainActivity")
        self.adb_device_id: str = os.getenv("ADB_DEVICE_ID", "")
        self.default_project_path: str = os.getenv("DEFAULT_PROJECT_PATH", ".")

    def validate(self) -> list[str]:
        """Return a list of configuration error messages."""
        # ANTHROPIC_API_KEY is no longer required — the agent uses the claude CLI's
        # own OAuth session. The key in .env is kept for reference but not validated.
        return []


def create_env_file(
    env_path: "Path",
    package_name: str = "",
    main_activity: str = ".MainActivity",
    adb_device: str = "",
    gh_token: str = "",
    az_token: str = "",
    az_org: str = "",
) -> None:
    """Escreve o .env com os valores fornecidos pelo wizard."""
    from pathlib import Path as _Path

    lines = [
        "# Leafar Configuration — gerado por 'rf init'",
        "# NUNCA faça commit deste arquivo.\n",
        f"ANDROID_PACKAGE_NAME={package_name}",
        f"ANDROID_MAIN_ACTIVITY={main_activity}",
        f"ADB_DEVICE_ID={adb_device}",
        f"GITHUB_PERSONAL_ACCESS_TOKEN={gh_token}",
        f"AZURE_DEVOPS_PAT={az_token}",
        f"AZURE_DEVOPS_ORG={az_org}",
        "DEFAULT_PROJECT_PATH=.",
    ]
    _Path(env_path).write_text("\n".join(lines) + "\n")
