"""MCP server that exposes Android, filesystem, and Gradle tools via FastMCP."""

import os
from mcp.server.fastmcp import FastMCP, Image

from .config import Config
from .tools.android import AndroidTools
from .tools.filesystem import FilesystemTools
from .tools.gradle import GradleTools

_project_path = os.getenv("RF_PROJECT_PATH", ".")

config = Config(_project_path)
_android = AndroidTools(config)
_filesystem = FilesystemTools()
_gradle = GradleTools()

mcp = FastMCP("leafar")


# ---------------------------------------------------------------------------
# Android tools
# ---------------------------------------------------------------------------

@mcp.tool()
def take_screenshot():  # type: ignore[return]
    """Take a screenshot of the current emulator screen."""
    result = _android.take_screenshot()
    if "error" in result:
        return result["error"]
    return Image(data=result["image_base64"], format="png")


@mcp.tool()
def run_adb_command(command: str) -> str:
    """Run an ADB shell command on the connected device/emulator."""
    result = _android.run_adb(command)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def get_ui_hierarchy() -> str:
    """Get the UI hierarchy of the current screen (XML dump)."""
    result = _android.get_ui_hierarchy()
    return result.get("output", result.get("error", ""))


@mcp.tool()
def get_current_activity() -> str:
    """Get the currently focused Activity/Fragment."""
    result = _android.get_current_activity()
    return result.get("output", result.get("error", ""))


@mcp.tool()
def tap(x: int, y: int) -> str:
    """Tap at coordinates (x, y) on the screen."""
    result = _android.tap(x, y)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def tap_element(text: str = "", resource_id: str = "") -> str:
    """Tap a UI element by text or resource_id."""
    kwargs: dict = {}
    if resource_id:
        kwargs["resource_id"] = resource_id
    if text:
        kwargs["text"] = text
    result = _android.tap_element(**kwargs)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def input_text(text: str) -> str:
    """Type text into the currently focused input field."""
    result = _android.input_text(text)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def clear_text() -> str:
    """Clear the text in the currently focused input field."""
    result = _android.clear_text()
    return result.get("output", result.get("error", ""))


@mcp.tool()
def press_key(key: str) -> str:
    """Press a key on the device. Keys: BACK, HOME, ENTER, MENU, VOLUME_UP, VOLUME_DOWN, etc."""
    result = _android.press_key(key)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
    """Swipe from (x1, y1) to (x2, y2) over duration_ms milliseconds."""
    result = _android.swipe(x1, y1, x2, y2, duration_ms)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def launch_app(package_name: str = "") -> str:
    """Launch an app by package name. Defaults to the project's configured package."""
    pkg = package_name or config.android_package_name or ""
    if not pkg:
        return "Error: no package_name provided and none configured in .env"
    result = _android.launch_app(pkg)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def stop_app(package_name: str = "") -> str:
    """Force-stop an app by package name. Defaults to the project's configured package."""
    pkg = package_name or config.android_package_name or ""
    if not pkg:
        return "Error: no package_name provided and none configured in .env"
    result = _android.stop_app(pkg)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def install_apk(apk_path: str) -> str:
    """Install an APK file on the connected device/emulator."""
    result = _android.install_apk(apk_path)
    return result.get("output", result.get("error", ""))


# ---------------------------------------------------------------------------
# Filesystem tools
# ---------------------------------------------------------------------------

@mcp.tool()
def read_file(path: str) -> str:
    """Read the contents of a file."""
    result = _filesystem.read_file(path)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates or overwrites)."""
    result = _filesystem.write_file(path, content)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def list_files(directory: str, pattern: str = "**/*.kt") -> str:
    """List files in a directory matching a glob pattern."""
    result = _filesystem.list_files(directory, pattern)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def search_in_files(query: str, directory: str = ".", file_pattern: str = "") -> str:
    """Search for a string/pattern across files in a directory."""
    kwargs: dict = {"directory": directory, "query": query}
    if file_pattern:
        kwargs["file_pattern"] = file_pattern
    result = _filesystem.search_in_files(**kwargs)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def get_project_structure(path: str = ".") -> str:
    """Get the directory tree structure of a project."""
    result = _filesystem.get_project_structure(path)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def run_command(command: str, cwd: str = ".") -> str:
    """Run a shell command in the given working directory."""
    result = _filesystem.run_command(command, cwd)
    return result.get("output", result.get("error", ""))


# ---------------------------------------------------------------------------
# Gradle tools
# ---------------------------------------------------------------------------

@mcp.tool()
def gradle_build(project_path: str = ".", variant: str = "debug") -> str:
    """Build the Android project with Gradle."""
    result = _gradle.build(project_path, variant)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def gradle_install_and_run(project_path: str = ".") -> str:
    """Build, install, and launch the app on the connected device/emulator."""
    pkg = config.android_package_name or ""
    activity = config.android_main_activity or ".MainActivity"
    result = _gradle.install_and_run(project_path, "debug", pkg, activity)
    return result.get("output", result.get("error", ""))


@mcp.tool()
def gradle_run_tests(project_path: str = ".") -> str:
    """Run the project's Gradle test tasks."""
    result = _gradle.run_tests(project_path, "test")
    return result.get("output", result.get("error", ""))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the MCP stdio server."""
    mcp.run()
