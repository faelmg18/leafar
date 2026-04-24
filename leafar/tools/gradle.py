"""Gradle build tool implementations."""

import subprocess
from pathlib import Path


class GradleTools:

    def _find_gradlew(self, project_path: str) -> Path | None:
        """Search for gradlew starting at project_path and walking up."""
        p = Path(project_path).resolve()
        for candidate in [p, *p.parents]:
            gw = candidate / "gradlew"
            if gw.exists():
                return gw
        return None

    def _run(self, project_path: str, task: str, timeout: int = 360) -> dict:
        gradlew = self._find_gradlew(project_path)
        if gradlew is None:
            return {
                "error": (
                    "gradlew not found. Make sure you're pointing to a valid "
                    "Android project root (or a subdirectory of one)."
                )
            }

        cmd = [str(gradlew), task, "--stacktrace"]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                cwd=str(Path(project_path).resolve()),
            )
            combined = (
                r.stdout.decode("utf-8", errors="replace")
                + r.stderr.decode("utf-8", errors="replace")
            )
            # Keep only the last 6 000 chars to stay within token budget
            if len(combined) > 6000:
                combined = "...(truncated)\n" + combined[-6000:]

            if r.returncode != 0:
                return {"error": combined, "returncode": r.returncode}
            return {"output": combined}
        except subprocess.TimeoutExpired:
            return {"error": f"Gradle timed out after {timeout}s"}
        except Exception as e:
            return {"error": str(e)}

    def build(
        self,
        project_path: str,
        variant: str = "debug",
        task: str | None = None,
    ) -> dict:
        if not task:
            task = f"assemble{variant.capitalize()}"
        return self._run(project_path, task)

    def install_and_run(
        self,
        project_path: str,
        variant: str = "debug",
        package_name: str = "",
    ) -> dict:
        task = f"install{variant.capitalize()}"
        result = self._run(project_path, task)
        if "error" not in result and package_name:
            try:
                subprocess.run(
                    ["adb", "shell", "monkey", "-p", package_name,
                     "-c", "android.intent.category.LAUNCHER", "1"],
                    capture_output=True,
                    timeout=15,
                )
                result["output"] = (result.get("output", "") or "") + f"\nApp launched: {package_name}"
            except Exception:
                pass
        return result

    def clean(self, project_path: str) -> dict:
        return self._run(project_path, "clean", timeout=120)

    def run_tests(self, project_path: str, task: str = "testDebugUnitTest") -> dict:
        return self._run(project_path, task, timeout=300)
