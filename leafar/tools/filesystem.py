"""Filesystem and shell tool implementations."""

import subprocess
from pathlib import Path
from typing import Optional


_IGNORE_DIRS = {".gradle", ".git", "build", ".idea", "__pycache__", "node_modules", ".cxx"}


class FilesystemTools:

    def read_file(self, path: str) -> dict:
        try:
            p = Path(path)
            if not p.exists():
                return {"error": f"File not found: {path}"}
            if not p.is_file():
                return {"error": f"Not a file: {path}"}
            size = p.stat().st_size
            if size > 500_000:
                return {
                    "error": (
                        f"File is too large ({size:,} bytes). "
                        "Use search_in_files to find specific content."
                    )
                }
            return {"output": p.read_text(encoding="utf-8", errors="replace"), "path": str(p)}
        except Exception as e:
            return {"error": str(e)}

    def write_file(self, path: str, content: str) -> dict:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return {"output": f"Wrote {lines} lines to {path}", "path": str(p), "lines": lines}
        except Exception as e:
            return {"error": str(e)}

    def list_files(self, directory: str, pattern: str = "*") -> dict:
        try:
            base = Path(directory)
            if not base.exists():
                return {"error": f"Directory not found: {directory}"}

            glob_pattern = pattern if "**" in pattern else f"**/{pattern}"
            files = [
                f for f in base.glob(glob_pattern)
                if f.is_file()
                and not any(part in _IGNORE_DIRS for part in f.parts)
            ]
            rel_paths = sorted(str(f.relative_to(base)) for f in files)[:300]
            return {"output": "\n".join(rel_paths), "count": len(rel_paths)}
        except Exception as e:
            return {"error": str(e)}

    def search_in_files(
        self,
        directory: str,
        query: str,
        file_pattern: Optional[str] = None,
    ) -> dict:
        try:
            cmd: list[str]
            # Prefer ripgrep for speed
            try:
                subprocess.run(["rg", "--version"], capture_output=True, check=True)
                cmd = ["rg", "-n", "--max-count", "5", query]
                if file_pattern:
                    cmd += ["-g", file_pattern]
                for d in _IGNORE_DIRS:
                    cmd += ["--glob", f"!{d}/**"]
                cmd.append(directory)
            except (subprocess.CalledProcessError, FileNotFoundError):
                cmd = ["grep", "-rn", "--include", file_pattern or "*.*"]
                for d in _IGNORE_DIRS:
                    cmd += [f"--exclude-dir={d}"]
                cmd += [query, directory]

            r = subprocess.run(cmd, capture_output=True, timeout=30)
            output = r.stdout.decode("utf-8", errors="replace")
            lines = output.splitlines()[:150]
            return {"output": "\n".join(lines), "count": len(lines)}
        except subprocess.TimeoutExpired:
            return {"error": "Search timed out after 30s"}
        except Exception as e:
            return {"error": str(e)}

    def get_project_structure(self, path: str, max_depth: int = 4) -> dict:
        try:
            root = Path(path)

            def _tree(p: Path, depth: int, prefix: str) -> list[str]:
                if depth > max_depth:
                    return []
                lines: list[str] = []
                try:
                    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
                except PermissionError:
                    return []
                visible = [
                    i for i in items
                    if i.name not in _IGNORE_DIRS and not i.name.startswith(".")
                ]
                for idx, item in enumerate(visible):
                    is_last = idx == len(visible) - 1
                    connector = "└── " if is_last else "├── "
                    lines.append(f"{prefix}{connector}{item.name}")
                    if item.is_dir():
                        ext = "    " if is_last else "│   "
                        lines.extend(_tree(item, depth + 1, prefix + ext))
                return lines

            lines = [root.name] + _tree(root, 0, "")
            return {"output": "\n".join(lines)}
        except Exception as e:
            return {"error": str(e)}

    def run_command(self, command: str, cwd: str = ".") -> dict:
        try:
            r = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=120,
                cwd=cwd,
            )
            stdout = r.stdout.decode("utf-8", errors="replace")
            stderr = r.stderr.decode("utf-8", errors="replace")
            if r.returncode != 0:
                return {
                    "output": stdout,
                    "error": stderr or f"Command exited with code {r.returncode}",
                    "returncode": r.returncode,
                }
            return {"output": stdout or "Command completed successfully."}
        except subprocess.TimeoutExpired:
            return {"error": "Command timed out after 120s"}
        except Exception as e:
            return {"error": str(e)}
