"""Figma API tool implementations."""

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import Config

_CACHE_DIR = Path.home() / ".leafar" / "figma_cache"
_CACHE_TTL = 3600  # 1 hora


class FigmaTools:
    BASE_URL = "https://api.figma.com/v1"

    def __init__(self, config: Config):
        self.config = config
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Figma-Token": self.config.figma_access_token}

    def _cache_key(self, path: str, params: dict | None) -> Path:
        key = json.dumps({"path": path, "params": params or {}}, sort_keys=True)
        h = hashlib.sha1(key.encode()).hexdigest()[:16]
        return _CACHE_DIR / f"{h}.json"

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.config.figma_access_token:
            return {"error": "FIGMA_ACCESS_TOKEN is not set. Add it to your .env file."}

        # Verifica cache
        cache_file = self._cache_key(path, params)
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < _CACHE_TTL:
                try:
                    return json.loads(cache_file.read_text())
                except Exception:
                    pass

        try:
            r = requests.get(
                f"{self.BASE_URL}{path}",
                headers=self._headers,
                params=params,
                timeout=30,
            )
            if r.status_code == 429:
                return {"error": "Figma API 429: rate limit atingido. Aguarde alguns minutos ou use /figma-json para fornecer o JSON manualmente."}
            r.raise_for_status()
            data = r.json()
            # Salva no cache
            try:
                cache_file.write_text(json.dumps(data))
            except Exception:
                pass
            return data
        except requests.HTTPError as e:
            return {"error": f"Figma API {e.response.status_code}: {e.response.text[:300]}"}
        except requests.ConnectionError:
            return {"error": "Network error reaching Figma API."}
        except Exception as e:
            return {"error": str(e)}

    def load_json(self, json_path: str) -> dict:
        """Carrega um JSON do Figma salvo localmente (evita chamadas à API)."""
        try:
            data = json.loads(Path(json_path).read_text())
            simplified = self._simplify(data)
            return {
                "output": f"Figma JSON carregado de {json_path}",
                "data": simplified,
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------

    def parse_figma_url(self, url: str) -> tuple[str, Optional[str]]:
        """Extract (file_key, node_id) from a Figma URL.
        Supports /file/KEY and /design/KEY formats.
        """
        key_match = re.search(r"figma\.com/(?:file|design)/([^/?#]+)", url)
        file_key = key_match.group(1) if key_match else url

        node_match = re.search(r"node-id=([^&#]+)", url)
        if node_match:
            # URL-decode colons/dashes used in node IDs
            node_id = node_match.group(1).replace("%3A", ":").replace("-", ":")
        else:
            node_id = None

        return file_key, node_id

    def _mcp_client(self):
        """Retorna cliente MCP se autenticado, None caso contrário."""
        try:
            from ..figma_auth import get_mcp_client
            return get_mcp_client()
        except Exception:
            return None

    def fetch_design(
        self, url_or_key: str, node_id: Optional[str] = None
    ) -> dict:
        """Fetch Figma file or node data — usa MCP se autenticado, REST como fallback."""
        file_key, parsed_node_id = self.parse_figma_url(url_or_key)
        node_id = node_id or parsed_node_id

        # Tenta MCP primeiro
        mcp = self._mcp_client()
        if mcp:
            args: dict = {"fileKey": file_key}
            if node_id:
                args["nodeId"] = node_id
            result = mcp.call_tool("get_design_context", args, timeout=30)
            if "error" not in result:
                return {
                    "file_key": file_key,
                    "node_id": node_id,
                    "output": f"Fetched via MCP (file={file_key}, node={node_id})",
                    "data": result.get("output", ""),
                }

        # Fallback REST
        if node_id:
            raw = self._get(f"/files/{file_key}/nodes", params={"ids": node_id})
        else:
            raw = self._get(f"/files/{file_key}", params={"depth": 3})

        if "error" in raw:
            return raw

        simplified = self._simplify(raw)
        return {
            "file_key": file_key,
            "node_id": node_id,
            "output": f"Fetched Figma design (file={file_key}, node={node_id})",
            "data": simplified,
        }

    def get_image(
        self, file_key: str, node_id: str, scale: float = 2
    ) -> dict:
        """Render a Figma node as a PNG image and return it as base64."""
        # Tenta MCP primeiro
        mcp = self._mcp_client()
        if mcp:
            result = mcp.call_tool("get_screenshot", {
                "fileKey": file_key,
                "nodeId": node_id,
                "scale": scale,
            }, timeout=30)
            if "error" not in result:
                raw = result.get("raw", {})
                for block in raw.get("content", []):
                    if block.get("type") == "image":
                        data = block.get("data", "")
                        return {
                            "image_base64": data,
                            "media_type": "image/png",
                            "text": f"Rendered via MCP node {node_id}",
                        }

        # Fallback REST
        images_data = self._get(
            f"/images/{file_key}",
            params={"ids": node_id, "scale": scale, "format": "png"},
        )
        if "error" in images_data:
            return images_data

        image_url = (images_data.get("images") or {}).get(node_id)
        if not image_url:
            return {"error": f"No image URL returned for node {node_id}"}

        try:
            r = requests.get(image_url, timeout=30)
            r.raise_for_status()
            return {
                "image_base64": base64.b64encode(r.content).decode("ascii"),
                "media_type": "image/png",
                "text": f"Rendered Figma node {node_id}",
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _KEEP_KEYS = {
        "name", "type", "id", "children", "absoluteBoundingBox",
        "fills", "strokes", "style", "characters", "fontSize",
        "fontFamily", "fontWeight", "textAlignHorizontal", "textAlignVertical",
        "layoutMode", "primaryAxisAlignItems", "counterAxisAlignItems",
        "paddingLeft", "paddingRight", "paddingTop", "paddingBottom",
        "itemSpacing", "cornerRadius", "opacity", "visible",
        "backgroundColor", "nodes", "document",
    }

    def _simplify(self, data: Any, depth: int = 0) -> Any:
        if depth > 6:
            return {"...": "max depth"}
        if isinstance(data, dict):
            return {
                k: self._simplify(v, depth + 1)
                for k, v in data.items()
                if k in self._KEEP_KEYS
            }
        if isinstance(data, list):
            return [self._simplify(item, depth + 1) for item in data[:30]]
        return data
