"""Android / ADB tool implementations."""

import base64
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from ..config import Config

KEYCODES: dict[str, str] = {
    "BACK": "KEYCODE_BACK",
    "HOME": "KEYCODE_HOME",
    "ENTER": "KEYCODE_ENTER",
    "TAB": "KEYCODE_TAB",
    "DEL": "KEYCODE_DEL",
    "DELETE": "KEYCODE_DEL",
    "DPAD_UP": "KEYCODE_DPAD_UP",
    "DPAD_DOWN": "KEYCODE_DPAD_DOWN",
    "DPAD_LEFT": "KEYCODE_DPAD_LEFT",
    "DPAD_RIGHT": "KEYCODE_DPAD_RIGHT",
    "MENU": "KEYCODE_MENU",
    "VOLUME_UP": "KEYCODE_VOLUME_UP",
    "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
    "POWER": "KEYCODE_POWER",
    "APP_SWITCH": "KEYCODE_APP_SWITCH",
}


class AndroidTools:
    def __init__(self, config: Config):
        self.config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _adb_args(self) -> list[str]:
        """Base adb command with optional -s flag."""
        cmd = ["adb"]
        if self.config.adb_device_id:
            cmd += ["-s", self.config.adb_device_id]
        return cmd

    def _run(self, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._adb_args() + args,
            capture_output=True,
            timeout=timeout,
        )

    def _run_text(self, args: list[str], timeout: int = 30) -> dict:
        """Run ADB and return text output dict."""
        try:
            r = self._run(args, timeout=timeout)
            if r.returncode != 0:
                err = (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()
                return {"error": err or f"adb failed (code {r.returncode})"}
            return {"output": r.stdout.decode("utf-8", errors="replace")}
        except subprocess.TimeoutExpired:
            return {"error": f"ADB command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"error": "adb not found. Install Android SDK Platform-Tools and add to PATH."}

    # ------------------------------------------------------------------
    # Public tools
    # ------------------------------------------------------------------

    def run_adb(self, command: str) -> dict:
        """Execute a raw ADB command string (without the 'adb' prefix)."""
        import shlex
        return self._run_text(shlex.split(command))

    def take_screenshot(self) -> dict:
        """Capture a PNG screenshot from the emulator/device."""
        try:
            r = self._run(["exec-out", "screencap", "-p"], timeout=15)
            if r.returncode != 0:
                return {"error": r.stderr.decode("utf-8", errors="replace") or "screencap failed"}
            data = r.stdout
            if not data:
                return {"error": "Empty screenshot — is an emulator running?"}
            return {
                "image_base64": base64.b64encode(data).decode("ascii"),
                "media_type": "image/png",
                "text": "Screenshot captured successfully.",
            }
        except FileNotFoundError:
            return {"error": "adb not found. Install Android SDK Platform-Tools."}
        except subprocess.TimeoutExpired:
            return {"error": "Screenshot timed out."}

    def get_ui_hierarchy(self) -> dict:
        """Dump the current screen's UI hierarchy as XML."""
        r1 = self._run_text(["shell", "uiautomator", "dump", "/sdcard/leafar_ui.xml"])
        if "error" in r1:
            return r1

        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            tmp_path = tmp.name

        r2 = self._run(["pull", "/sdcard/leafar_ui.xml", tmp_path])
        if r2.returncode != 0:
            return {"error": f"Failed to pull UI dump: {r2.stderr.decode()}"}

        try:
            content = Path(tmp_path).read_text(encoding="utf-8")
            return {"output": content}
        except Exception as e:
            return {"error": str(e)}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def get_current_activity(self) -> dict:
        """Return the name of the currently focused Activity."""
        r = self._run_text(["shell", "dumpsys", "activity", "activities"], timeout=15)
        if "error" in r:
            return r
        text = r["output"]
        match = re.search(r"mCurrentFocus=Window\{[^}]+ ([^\s}]+)\}", text)
        if not match:
            match = re.search(
                r"mResumedActivity:.*?ActivityRecord\{[^}]+ ([^\s}]+)", text
            )
        return {"output": match.group(1) if match else "Unknown (no focused activity found)"}

    def tap(self, x: int, y: int) -> dict:
        r = self._run_text(["shell", "input", "tap", str(x), str(y)])
        time.sleep(0.5)
        return r if "error" in r else {"output": f"Tapped ({x}, {y})"}

    def tap_element(
        self,
        resource_id: Optional[str] = None,
        text: Optional[str] = None,
    ) -> dict:
        """Tap a UI element by resource-id or visible text."""
        if not resource_id and not text:
            return {"error": "Provide resource_id or text"}

        hierarchy = self.get_ui_hierarchy()
        if "error" in hierarchy:
            return hierarchy

        try:
            root = ET.fromstring(hierarchy["output"])
        except ET.ParseError as e:
            return {"error": f"Could not parse UI hierarchy: {e}"}

        for node in root.iter("node"):
            attrs = node.attrib
            match = False
            if resource_id and attrs.get("resource-id") == resource_id:
                match = True
            elif text:
                node_text = attrs.get("text", "")
                if node_text == text or text.lower() in node_text.lower():
                    match = True
                elif attrs.get("content-desc", "").lower() == text.lower():
                    match = True

            if match:
                bounds = attrs.get("bounds", "")
                coords = re.findall(r"\d+", bounds)
                if len(coords) == 4:
                    cx = (int(coords[0]) + int(coords[2])) // 2
                    cy = (int(coords[1]) + int(coords[3])) // 2
                    return self.tap(cx, cy)

        return {"error": f"Element not found — resource_id={resource_id!r}, text={text!r}"}

    def input_text(self, text: str) -> dict:
        # ADB input text requires spaces as %s and special shell chars escaped
        escaped = text.replace("\\", "\\\\").replace(" ", "%s").replace("'", "\\'")
        r = self._run_text(["shell", "input", "text", escaped])
        return r if "error" in r else {"output": f"Typed: {text}"}

    def clear_text(self) -> dict:
        """Select all text and delete it in the focused field."""
        self._run(["shell", "input", "keyevent", "KEYCODE_CTRL_A"])
        time.sleep(0.1)
        r = self._run_text(["shell", "input", "keyevent", "KEYCODE_DEL"])
        return r if "error" in r else {"output": "Text cleared"}

    def press_key(self, key: str) -> dict:
        keycode = KEYCODES.get(key.upper(), key.upper())
        if not keycode.startswith("KEYCODE_"):
            keycode = f"KEYCODE_{keycode}"
        r = self._run_text(["shell", "input", "keyevent", keycode])
        time.sleep(0.3)
        return r if "error" in r else {"output": f"Pressed {key}"}

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> dict:
        r = self._run_text(
            ["shell", "input", "swipe",
             str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
        )
        time.sleep(0.5)
        return r if "error" in r else {"output": f"Swiped ({x1},{y1}) -> ({x2},{y2})"}

    def launch_app(self, package_name: str) -> dict:
        if not package_name:
            return {"error": "package_name is required. Set ANDROID_PACKAGE_NAME in .env"}
        r = self._run_text(
            ["shell", "monkey", "-p", package_name,
             "-c", "android.intent.category.LAUNCHER", "1"]
        )
        time.sleep(1.5)
        return r if "error" in r else {"output": f"Launched {package_name}"}

    def start_activity(self, component: str) -> dict:
        r = self._run_text(["shell", "am", "start", "-n", component])
        time.sleep(1.0)
        return r if "error" in r else {"output": f"Started activity: {component}"}

    def stop_app(self, package_name: str) -> dict:
        r = self._run_text(["shell", "am", "force-stop", package_name])
        return r if "error" in r else {"output": f"Stopped {package_name}"}

    def install_apk(self, apk_path: str) -> dict:
        try:
            r = self._run(["install", "-r", apk_path], timeout=120)
            output = r.stdout.decode("utf-8", errors="replace")
            if r.returncode != 0 or "Failure" in output:
                return {"error": r.stderr.decode("utf-8", errors="replace") or output}
            return {"output": f"Installed: {apk_path}"}
        except subprocess.TimeoutExpired:
            return {"error": "APK install timed out after 120s"}
