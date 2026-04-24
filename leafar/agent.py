"""Core AI agent — wraps Claude Agent SDK with all Leafar tools."""

import asyncio
import difflib
import itertools
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    RateLimitEvent,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn

from .config import Config
from .tools.android import AndroidTools
from .tools.figma import FigmaTools
from .tools.filesystem import FilesystemTools
from .tools.gradle import GradleTools

console = Console()


def _parse_tokens(usage: object) -> int:
    """Extrai total de tokens de um objeto usage (dict snake_case ou dataclass).

    Inclui tokens de cache (cache_creation_input_tokens + cache_read_input_tokens)
    para refletir o custo real da sessão.
    """
    if isinstance(usage, dict):
        inp   = usage.get("input_tokens", 0) or 0
        out   = usage.get("output_tokens", 0) or 0
        cc    = usage.get("cache_creation_input_tokens", 0) or 0
        cr    = usage.get("cache_read_input_tokens", 0) or 0
        return inp + out + cc + cr
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cc  = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr  = getattr(usage, "cache_read_input_tokens", 0) or 0
    return inp + out + cc + cr


THINKING_VERBS = [
    "Pensando", "Analisando", "Refletindo", "Investigando",
    "Planejando", "Processando", "Deliberando", "Cogitando",
    "Raciocinando", "Deduzindo", "Explorando", "Avaliando",
]

_CLI_SOURCE_PATH = str(Path(__file__).parent.parent.resolve())

SYSTEM_PROMPT = f"""\
You are Leafar, an intelligent Android development assistant CLI.
You are running on the developer's machine and have full access to their Android project
and any connected emulator/device via ADB.

## Your own source code (the CLI itself)
- The Leafar CLI source code lives at: `{_CLI_SOURCE_PATH}`
- Key files:
  - `{_CLI_SOURCE_PATH}/leafar/agent.py` — AI agent, MCP tools, system prompt, session logic
  - `{_CLI_SOURCE_PATH}/leafar/cli.py` — CLI commands, chat loop, slash commands
  - `{_CLI_SOURCE_PATH}/leafar/tools/` — Android, Figma, filesystem, Gradle tools
  - `{_CLI_SOURCE_PATH}/leafar/config.py` — configuration, .env loading
- **When the user asks you to fix, improve or change something in "rf", "leafar" or "the CLI"**,
  read and modify files in `{_CLI_SOURCE_PATH}/leafar/`, NOT the Android project.
- You can self-modify: read the current source, understand it, then use `mcp__leafar__write_file`
  to apply the fix. After modifying agent.py or cli.py, tell the user to restart `rf` for changes
  to take effect.

## Your capabilities
1. **Project understanding** — Read Kotlin/Java sources, XML layouts, Gradle files.
2. **Emulator interaction** — Take screenshots, tap elements, type text, swipe, navigate screens.
3. **Figma integration** — Fetch Figma designs and generate matching Android UI code.
   - Results are cached locally for 1 hour — avoid redundant calls to the same node.
   - If you hit a 429 rate limit, stop and ask the user to provide the JSON file.
     Then use `mcp__leafar__load_figma_json` instead of `mcp__leafar__fetch_figma_design`.
   - Never call `mcp__leafar__fetch_figma_design` or `mcp__leafar__get_figma_image` more than 5 times per task.
4. **Build system** — Build with Gradle, install APKs, run tests.
5. **Debugging** — Visually inspect running screens, find bugs in code, apply fixes.
6. **Self-improvement** — Fix bugs and add features to the Leafar CLI itself when asked.

## Distinguishing CLI tasks from project tasks
- "fix the rf", "fix leafar", "fix the agent", "fix the CLI", "melhora o rf" → modify `{_CLI_SOURCE_PATH}/leafar/`
- "fix the app", "fix this screen", "fix this bug [in the app]" → modify the Android project
- When ambiguous, ask: "Você quer que eu corrija isso no projeto Android ou no próprio rf?"
- **Never mix**: if asked to fix something in the CLI, do NOT start reading/modifying the Android project.

## How to approach tasks
- If "Project context (already explored)" is present, trust it — do NOT re-read described files.
- Start by understanding context: read relevant source files, check project structure.
- For Figma tasks: fetch the design, analyse dimensions/colours/typography, then generate code
  that matches the project's existing style (Compose vs View-based XML).
- For bug fixes: reproduce the bug visually, trace it to the source, fix and verify.
- Follow Android best practices: MVVM, Coroutines/Flow, Material Design 3.
- Prefer Jetpack Compose for new UI unless the project uses View-based XML.
- When writing files always read existing related files first to match project conventions.

## Confirmations — CRITICAL
- **When you ask the user ANY question, you MUST stop ALL tool calls immediately and return your response.**
  Do NOT call any tool after asking a question. Wait for the user's reply in the next message.
- This applies to every question: yes/no, choices, clarifications, confirmations.
- Example of WRONG behaviour: ask "GitHub ou Azure?" then immediately call `get_me()` or any tool.
- Example of CORRECT behaviour: ask "GitHub ou Azure?" → end your turn → wait → user replies → then act.
- If you need information to answer a question (like org name), ask for it first before calling any tool.

## Session & token efficiency
- After exploring the project for the first time, call `mcp__leafar__save_project_context`
  with a compact summary (modules, key files, architecture, pending tasks).
- Be concise with tool calls: read only files relevant to the current task.
- **When the user starts a completely different task**, call `mcp__leafar__clear_session`
  BEFORE answering to start fresh. Continue in the same response after clearing.

## File modifications — CRITICAL RULES
- **NEVER use `mcp__leafar__run_shell_command`, `Bash`, or any shell trick to modify files**
  (no `sed -i`, `awk`, `echo >`, `tee`, `cp`, `mv` to overwrite, `patch`, `dd`, etc.).
- **ALWAYS use `mcp__leafar__write_file`** to create or modify any file, no exceptions.
- This is required so the user can review and approve every change via diff before it is applied.
- Violations of this rule will cause your change to be rejected automatically.

## MCP vs REST fallback
- Always prefer MCP tools over REST API or curl.
- If a required action is not available as an MCP tool, **tell the user first**:
  "O MCP não tem essa ferramenta. Posso usar a API REST diretamente para fazer isso. Quer prosseguir?"
- Wait for confirmation before using REST/curl as fallback.

## GitHub & Azure DevOps & browser
- Before creating a PR, run `git remote -v` to see available remotes, then **ask the user**:
  "Onde você quer abrir o PR? (GitHub / Azure DevOps)" — wait for their answer before proceeding.
- Based on the answer:
  - GitHub → use `mcp__github__*` tools
  - Azure DevOps → use `mcp__azure-devops__*` tools
- After creating a PR or issue, always open the URL in the browser automatically:
  `mcp__leafar__run_shell_command` with command `open <url>`

## Identity — CRITICAL
- **NEVER mention AI, Claude, Claude Code, Anthropic, or any AI tool** in any output, commit message, PR description, comment, or file you create or modify.
- Do not add footers, signatures, or attributions of any kind referencing AI or automation.
- Act as a natural extension of the developer — your output should be indistinguishable from work done by a human developer.

## Output style
- **Always respond in Brazilian Portuguese**, regardless of the language used in the prompt.
- Be concise in explanations; let tool results speak for themselves.
- When you modify files, state clearly what changed and why.
- When you identify a bug, explain the root cause before fixing it.
"""

# ---------------------------------------------------------------------------
# Skill definitions — inject context/behaviour into the agent
# ---------------------------------------------------------------------------

SKILLS: dict[str, dict] = {
    "emulador": {
        "description": "Foca em interação com emulador/dispositivo via ADB",
        "prompt": (
            "## Modo: Emulador\n"
            "Você está no modo de interação direta com o emulador/dispositivo.\n"
            "- Sempre tire um screenshot antes de agir para saber o estado atual da tela.\n"
            "- Após cada ação (tap, swipe, input), tire outro screenshot para confirmar o resultado.\n"
            "- Use mcp__leafar__get_ui_hierarchy quando precisar encontrar elementos por resource-id.\n"
            "- Prefira mcp__leafar__tap_element (por texto ou resource-id) ao tap por coordenadas.\n"
            "- Se o app não estiver aberto, use mcp__leafar__launch_app antes de qualquer navegação.\n"
        ),
    },
    "build": {
        "description": "Foca em build, testes e CI do projeto Android",
        "prompt": (
            "## Modo: Build\n"
            "Você está no modo de build e validação.\n"
            "- Sempre verifique o build.gradle (app e módulos) antes de sugerir mudanças de dependência.\n"
            "- Use 'assembleDebug' para builds rápidos de validação.\n"
            "- Mostre erros de compilação completos — não truncar stack traces relevantes.\n"
            "- Após um fix de build, rode o build novamente para confirmar que passou.\n"
            "- Para testes, prefira testDebugUnitTest antes de connectedAndroidTest (mais rápido).\n"
        ),
    },
    "figma": {
        "description": "Foca em geração de UI Android a partir de designs Figma",
        "prompt": (
            "## Modo: Figma\n"
            "Você está no modo de geração de UI a partir do Figma.\n"
            "- Sempre busque o design com mcp__leafar__fetch_figma_design antes de gerar código.\n"
            "- Use mcp__leafar__get_figma_image para renderizar o frame e inspecionar visualmente.\n"
            "- Verifique se o projeto usa Jetpack Compose ou View-based XML antes de gerar.\n"
            "- Extraia: cores exatas (hex), tipografia (font, size, weight), espaçamentos (dp), ícones.\n"
            "- Gere código idiomático que siga as convenções já existentes no projeto.\n"
            "- Sempre leia um arquivo de UI existente do projeto como referência de estilo.\n"
        ),
    },
    "debug": {
        "description": "Modo de debugging visual e rastreamento de bugs",
        "prompt": (
            "## Modo: Debug\n"
            "Você está no modo de debugging.\n"
            "- Reproduza o bug visualmente: navegue até a tela, confirme com screenshot.\n"
            "- Trace o problema: leia o código da tela, ViewModel, Repository e Model envolvidos.\n"
            "- Identifique a causa raiz antes de propor qualquer fix.\n"
            "- Aplique o fix mínimo necessário — não refatore código não relacionado.\n"
            "- Após o fix, rebuild e verifique visualmente que o bug foi resolvido.\n"
            "- Explique em português o que causou o bug e o que foi alterado.\n"
        ),
    },
    "review": {
        "description": "Code review focado em boas práticas Android/Kotlin",
        "prompt": (
            "## Modo: Review\n"
            "Você está no modo de code review.\n"
            "- Analise o código quanto a: memory leaks, coroutine scope, null safety, performance.\n"
            "- Verifique aderência ao MVVM: lógica de negócio não deve estar em Fragment/Activity.\n"
            "- Aponte violações de Clean Architecture se o projeto as usa.\n"
            "- Sugira melhorias com exemplos concretos em Kotlin.\n"
            "- Priorize problemas por severidade: crítico, importante, sugestão.\n"
            "- Não reescreva código funcional sem motivo claro.\n"
        ),
    },
    "testes": {
        "description": "Foca em escrita e execução de testes Android",
        "prompt": (
            "## Modo: Testes\n"
            "Você está no modo de testes.\n"
            "- Para unit tests: use JUnit4/5, MockK para mocks, Coroutines Test para suspend fns.\n"
            "- Para UI tests: use Espresso (View) ou Compose Test (Compose).\n"
            "- Siga o padrão: Given/When/Then nos nomes dos testes.\n"
            "- Leia a implementação antes de escrever o teste para garantir cobertura real.\n"
            "- Prefira testar comportamento (o que faz) ao invés de implementação (como faz).\n"
            "- Rode os testes após escrever para confirmar que passam.\n"
        ),
    },
    "arquitetura": {
        "description": "Analisa e documenta a arquitetura do projeto",
        "prompt": (
            "## Modo: Arquitetura\n"
            "Você está no modo de análise de arquitetura.\n"
            "- Mapeie módulos, camadas e dependências entre eles.\n"
            "- Identifique o padrão usado: MVVM, MVI, MVP, Clean Architecture.\n"
            "- Liste as principais tecnologias: DI (Hilt/Koin), navegação (NavComponent/custom), "
            "  rede (Retrofit/Ktor), banco (Room/SQLDelight), estado (StateFlow/LiveData).\n"
            "- Aponte inconsistências ou violações de arquitetura encontradas.\n"
            "- Produza um resumo claro e objetivo em português.\n"
        ),
    },
}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

_FIGMA_LOCAL_URL = "http://127.0.0.1:3845/mcp"


def _figma_local_available() -> bool:
    """Retorna True se o servidor MCP local do Figma desktop estiver rodando."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 3845), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False


def _find_claude_cli() -> str | None:
    """Localiza o claude CLI com o plugin figma instalado (npm-global preferido).

    O SDK tem um bundled claude que não tem acesso aos plugins do usuário.
    O claude instalado via npm (~/.npm-global/bin/claude) tem o plugin
    figma@claude-plugins-official instalado e acessa mcp.figma.com diretamente.
    """
    import shutil
    candidates = [
        Path.home() / ".npm-global" / "bin" / "claude",
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / "node_modules" / ".bin" / "claude",
        Path.home() / ".yarn" / "bin" / "claude",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # Último recurso: claude no PATH (mas pode ser o bundled)
    found = shutil.which("claude")
    return found if found else None


class LeafarAgent:
    def __init__(self, config: Config, project_path: str = ".", chat_mode: bool = False):
        self.config = config
        self.project_path = str(Path(project_path).resolve())
        self.android = AndroidTools(config)
        self.figma = FigmaTools(config)
        self.fs = FilesystemTools()
        self.gradle = GradleTools()
        self.project_context: str = ""
        self._session_id: str | None = None
        self._session_tokens: int = 0
        self._active_skill: str | None = None
        self.chat_mode: bool = chat_mode
        self.chat_console: "Console | None" = None
        self.toolbar_state: "dict | None" = None
        self._load_session()

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    @property
    def _session_path(self) -> Path:
        return Path(self.project_path) / ".rf_session.json"

    def _load_session(self) -> None:
        try:
            if not self._session_path.exists():
                return
            data = json.loads(self._session_path.read_text())
            self._session_id = data.get("session_id")
            self.project_context = data.get("project_context", "")
            updated = data.get("updated_at", "")
            if self._session_id and not self.chat_mode:
                console.print(
                    f"[dim]↩ sessão retomada ({updated[:16]})"
                    f" — '/reset' para começar do zero[/dim]"
                )
        except Exception:
            self._session_id = None
            self.project_context = ""
            self._session_path.unlink(missing_ok=True)

    def _save_session(self) -> None:
        try:
            self._session_path.write_text(json.dumps({
                "project": self.project_path,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "project_context": self.project_context,
                "session_id": self._session_id,
            }))
        except Exception:
            pass

    def clear_session(self) -> None:
        self._session_id = None
        self.project_context = ""
        try:
            self._session_path.unlink(missing_ok=True)
        except Exception:
            pass

    def reset(self) -> None:
        self.clear_session()

    # ------------------------------------------------------------------
    # MCP tool helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ok(result: Any) -> dict:
        """Convert any tool result into MCP content dict."""
        if isinstance(result, dict):
            if "image_base64" in result:
                parts: list = []
                if result.get("text"):
                    parts.append({"type": "text", "text": result["text"]})
                parts.append({
                    "type": "image",
                    "data": result["image_base64"],
                    "mimeType": result.get("media_type", "image/png"),
                })
                return {"content": parts}
            if "error" in result:
                return {
                    "content": [{"type": "text", "text": f"Error: {result['error']}"}],
                    "is_error": True,
                }
        text = json.dumps(result, indent=2, default=str) if isinstance(result, dict) else str(result)
        # Truncate large results
        if len(text) > 3000:
            text = text[:3000] + f"\n…[{len(text)-3000} chars truncated]"
        return {"content": [{"type": "text", "text": text}]}

    @staticmethod
    def _err(msg: str) -> dict:
        return {"content": [{"type": "text", "text": f"Error: {msg}"}], "is_error": True}

    # Padrões que indicam mutação de arquivo via shell
    _FILE_MUTATION_RE = re.compile(
        r"\bsed\s+-i\b"
        r"|\bawk\b.*[>|]"
        r"|\becho\b.*\s>>"
        r"|\becho\b.*\s>[^>]"
        r"|\bprintf\b.*\s>"
        r"|\btee\b"
        r"|\bpatch\b"
        r"|\bcp\b.*\.kt\b"
        r"|\bmv\b.*\.kt\b"
        r"|\btruncate\b"
        r"|\bdd\b.*of="
    )

    def _is_file_mutation(self, cmd: str) -> bool:
        return bool(self._FILE_MUTATION_RE.search(cmd))

    def _resolve(self, path: str) -> str:
        p = Path(path)
        return str(p if p.is_absolute() else Path(self.project_path) / path)

    # ------------------------------------------------------------------
    # MCP tools — build the list on each run so handlers capture self
    # ------------------------------------------------------------------

    def _build_mcp_tools(self) -> list[SdkMcpTool]:
        agent = self

        def T(name, description, schema, handler) -> SdkMcpTool:
            return SdkMcpTool(
                name=name, description=description,
                input_schema=schema, handler=handler,
            )

        # ── Screenshot & UI ────────────────────────────────────────────
        async def h_take_screenshot(a: dict) -> dict:
            return agent._ok(agent.android.take_screenshot())

        async def h_get_ui_hierarchy(a: dict) -> dict:
            return agent._ok(agent.android.get_ui_hierarchy())

        async def h_get_current_activity(a: dict) -> dict:
            return agent._ok(agent.android.get_current_activity())

        # ── Interaction ────────────────────────────────────────────────
        async def h_tap(a: dict) -> dict:
            return agent._ok(agent.android.tap(a["x"], a["y"]))

        async def h_tap_element(a: dict) -> dict:
            return agent._ok(agent.android.tap_element(
                resource_id=a.get("resource_id"),
                text=a.get("text"),
            ))

        async def h_input_text(a: dict) -> dict:
            return agent._ok(agent.android.input_text(a["text"]))

        async def h_clear_text(a: dict) -> dict:
            return agent._ok(agent.android.clear_text())

        async def h_press_key(a: dict) -> dict:
            return agent._ok(agent.android.press_key(a["key"]))

        async def h_swipe(a: dict) -> dict:
            return agent._ok(agent.android.swipe(
                a["x1"], a["y1"], a["x2"], a["y2"],
                a.get("duration_ms", 300),
            ))

        # ── App control ────────────────────────────────────────────────
        async def h_launch_app(a: dict) -> dict:
            pkg = a.get("package_name") or agent.config.android_package_name
            return agent._ok(agent.android.launch_app(pkg))

        async def h_start_activity(a: dict) -> dict:
            return agent._ok(agent.android.start_activity(a["component"]))

        async def h_stop_app(a: dict) -> dict:
            pkg = a.get("package_name") or agent.config.android_package_name
            return agent._ok(agent.android.stop_app(pkg))

        async def h_install_apk(a: dict) -> dict:
            return agent._ok(agent.android.install_apk(a["apk_path"]))

        # ── Build ──────────────────────────────────────────────────────
        async def h_build_project(a: dict) -> dict:
            result = agent.gradle.build(
                agent.project_path,
                variant=a.get("variant", "debug"),
                task=a.get("task"),
            )
            if isinstance(result, dict) and "output" in result:
                t = result["output"]
                if len(t) > 1500:
                    result["output"] = t[:1500] + f"\n…[{len(t)-1500} chars truncated]"
            return agent._ok(result)

        async def h_install_and_run(a: dict) -> dict:
            return agent._ok(agent.gradle.install_and_run(
                agent.project_path,
                variant=a.get("variant", "debug"),
                package_name=agent.config.android_package_name,
            ))

        async def h_run_tests(a: dict) -> dict:
            return agent._ok(agent.gradle.run_tests(
                agent.project_path,
                task=a.get("task", "testDebugUnitTest"),
            ))

        # ── Filesystem ─────────────────────────────────────────────────
        async def h_read_file(a: dict) -> dict:
            return agent._ok(agent.fs.read_file(agent._resolve(a["path"])))

        async def h_write_file(a: dict) -> dict:
            resolved = agent._resolve(a["path"])
            ok, msg = await asyncio.to_thread(agent._confirm_write, resolved, a["content"])
            if not ok:
                return agent._err(f"Alteração rejeitada: {msg}")
            result = agent.fs.write_file(resolved, a["content"])
            agent._log_write_result(resolved, result)
            return agent._ok(result)

        async def h_list_files(a: dict) -> dict:
            return agent._ok(agent.fs.list_files(
                agent._resolve(a.get("directory", ".")),
                a.get("pattern", "*"),
            ))

        async def h_search_in_files(a: dict) -> dict:
            return agent._ok(agent.fs.search_in_files(
                agent._resolve(a.get("directory", ".")),
                a["query"],
                a.get("file_pattern"),
            ))

        async def h_get_project_structure(a: dict) -> dict:
            return agent._ok(agent.fs.get_project_structure(
                agent.project_path, max_depth=a.get("max_depth", 4)
            ))

        # ── Figma ──────────────────────────────────────────────────────
        async def h_fetch_figma_design(a: dict) -> dict:
            return agent._ok(agent.figma.fetch_design(
                a["url_or_key"], node_id=a.get("node_id")
            ))

        async def h_get_figma_image(a: dict) -> dict:
            return agent._ok(agent.figma.get_image(
                a["file_key"], a["node_id"], scale=a.get("scale", 2)
            ))

        async def h_load_figma_json(a: dict) -> dict:
            return agent._ok(agent.figma.load_json(agent._resolve(a["path"])))

        # ── Shell / ADB ────────────────────────────────────────────────
        async def h_run_adb_command(a: dict) -> dict:
            return agent._ok(agent.android.run_adb(a["command"]))

        async def h_run_shell_command(a: dict) -> dict:
            cmd = a.get("command", "")
            if agent._is_file_mutation(cmd):
                return agent._err(
                    "Modificação de arquivo via shell não é permitida. "
                    "Use mcp__leafar__write_file para que o usuário possa "
                    "revisar e aprovar a alteração."
                )
            result = agent.fs.run_command(cmd, cwd=agent.project_path)
            if isinstance(result, dict) and "output" in result:
                t = result["output"]
                if len(t) > 1500:
                    result["output"] = t[:1500] + f"\n…[{len(t)-1500} chars truncated]"
            return agent._ok(result)

        # ── Session / Context ──────────────────────────────────────────
        async def h_save_project_context(a: dict) -> dict:
            agent.project_context = a["summary"]
            agent._save_session()
            return agent._ok({"output": "Project context saved."})

        async def h_clear_session(a: dict) -> dict:
            ctx = agent.project_context
            agent.clear_session()
            agent.project_context = ctx
            return agent._ok({"output": "Session cleared. Starting fresh."})

        # ── Assemble ───────────────────────────────────────────────────
        O = {"type": "object"}
        S = {"type": "string"}
        I = {"type": "integer"}
        N = {"type": "number"}

        return [
            T("take_screenshot", "Capture a screenshot of the current Android emulator/device screen.",
              {**O, "properties": {}, "required": []}, h_take_screenshot),
            T("get_ui_hierarchy", "Dump the XML UI hierarchy of the current screen.",
              {**O, "properties": {}, "required": []}, h_get_ui_hierarchy),
            T("get_current_activity", "Return the fully-qualified name of the current Activity.",
              {**O, "properties": {}, "required": []}, h_get_current_activity),
            T("tap", "Tap at an (x, y) pixel coordinate on screen.",
              {**O, "properties": {"x": {**I}, "y": {**I}}, "required": ["x", "y"]}, h_tap),
            T("tap_element", "Tap a UI element by resource-id or visible text.",
              {**O, "properties": {"resource_id": {**S}, "text": {**S}}}, h_tap_element),
            T("input_text", "Type text into the currently focused input field.",
              {**O, "properties": {"text": {**S}}, "required": ["text"]}, h_input_text),
            T("clear_text", "Select all and delete text in the currently focused input.",
              {**O, "properties": {}, "required": []}, h_clear_text),
            T("press_key",
              "Press an Android key: BACK HOME ENTER TAB DEL DPAD_UP DPAD_DOWN DPAD_LEFT DPAD_RIGHT MENU.",
              {**O, "properties": {"key": {**S}}, "required": ["key"]}, h_press_key),
            T("swipe", "Perform a swipe gesture between two coordinates.",
              {**O, "properties": {
                  "x1": {**I}, "y1": {**I}, "x2": {**I}, "y2": {**I},
                  "duration_ms": {**I},
              }, "required": ["x1", "y1", "x2", "y2"]}, h_swipe),
            T("launch_app", "Launch the app from its launcher icon.",
              {**O, "properties": {"package_name": {**S}}}, h_launch_app),
            T("start_activity", "Start a specific Activity component directly.",
              {**O, "properties": {"component": {**S}}, "required": ["component"]}, h_start_activity),
            T("stop_app", "Force-stop the app.",
              {**O, "properties": {"package_name": {**S}}}, h_stop_app),
            T("install_apk", "Install an APK onto the connected emulator/device.",
              {**O, "properties": {"apk_path": {**S}}, "required": ["apk_path"]}, h_install_apk),
            T("build_project", "Build the Android project with Gradle.",
              {**O, "properties": {"variant": {**S}, "task": {**S}}}, h_build_project),
            T("install_and_run", "Build, install, and launch the app in one step.",
              {**O, "properties": {"variant": {**S}}}, h_install_and_run),
            T("run_tests", "Run Gradle unit tests.",
              {**O, "properties": {"task": {**S}}}, h_run_tests),
            T("read_file", "Read the content of a file in the project.",
              {**O, "properties": {"path": {**S}}, "required": ["path"]}, h_read_file),
            T("write_file",
              "Write (create or overwrite) a file. User must approve via diff before it is applied.",
              {**O, "properties": {"path": {**S}, "content": {**S}}, "required": ["path", "content"]},
              h_write_file),
            T("list_files", "List files in a directory matching a glob pattern.",
              {**O, "properties": {"directory": {**S}, "pattern": {**S}}, "required": ["directory"]},
              h_list_files),
            T("search_in_files", "Search for text or regex inside project files.",
              {**O, "properties": {"query": {**S}, "file_pattern": {**S}, "directory": {**S}},
               "required": ["query"]}, h_search_in_files),
            T("get_project_structure",
              "Return an ASCII tree of the project directory (build/ and .gradle/ excluded).",
              {**O, "properties": {"max_depth": {**I}}}, h_get_project_structure),
            T("fetch_figma_design",
              "Fetch a Figma design by URL or file key. Returns node structure, colours, typography.",
              {**O, "properties": {"url_or_key": {**S}, "node_id": {**S}},
               "required": ["url_or_key"]}, h_fetch_figma_design),
            T("get_figma_image",
              "Render a specific Figma node as a PNG image for visual inspection.",
              {**O, "properties": {"file_key": {**S}, "node_id": {**S}, "scale": {**N}},
               "required": ["file_key", "node_id"]}, h_get_figma_image),
            T("load_figma_json",
              "Load a Figma design from a locally saved JSON file instead of calling the API.",
              {**O, "properties": {"path": {**S}}, "required": ["path"]}, h_load_figma_json),
            T("run_adb_command", "Run a raw ADB command (without the 'adb' prefix).",
              {**O, "properties": {"command": {**S}}, "required": ["command"]}, h_run_adb_command),
            T("run_shell_command", "Run a shell command in the project directory.",
              {**O, "properties": {"command": {**S}}, "required": ["command"]}, h_run_shell_command),
            T("save_project_context",
              "Save a compact project summary so future sessions don't re-read everything.",
              {**O, "properties": {"summary": {**S}}, "required": ["summary"]},
              h_save_project_context),
            T("clear_session",
              "Clear conversation history. Preserves project_context. Use when user changes topic.",
              {**O, "properties": {}, "required": []}, h_clear_session),
        ]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, prompt: str, stream_output: bool = True) -> None:
        """Run the agent for a single user prompt."""
        self._rate_limit_resets_at: int | None = None
        self._not_logged_in: bool = False
        try:
            asyncio.run(self._run_with_retry(prompt, stream_output))
        except KeyboardInterrupt:
            self._out.print("\n[dim]Interrompido.[/dim]")
        except Exception as e:
            self._handle_run_error(e)

    async def _run_with_retry(self, prompt: str, stream_output: bool) -> None:
        """Single event loop: try with session, then retry fresh if it fails."""
        if not self._session_id:
            # No session — straight fresh start
            await self._run_async(prompt, stream_output)
            return

        try:
            await self._run_async(prompt, stream_output)
        except Exception as e:
            err = str(e)
            etype = type(e).__name__

            # Auth or rate limit failures → don't retry
            if self._rate_limit_resets_at is not None or self._not_logged_in:
                raise

            # Session error → clear and retry in the SAME event loop
            is_session_err = (
                "session" in err.lower()
                or "not found" in err.lower()
                or "exit code 1" in err.lower()
                or "exit code: 1" in err.lower()
                or "exit status" in err.lower()
                or "Command failed" in err
                or "Fatal error" in err
                or "CLIError" in etype
            )
            if is_session_err:
                self._out.print("[dim]↻ sessão expirada — iniciando nova conversa[/dim]")
                old_ctx = self.project_context
                self.clear_session()
                self.project_context = old_ctx
                self._rate_limit_resets_at = None
                await self._run_async(prompt, stream_output)
                return

            raise

    def _handle_run_error(self, e: Exception) -> None:
        """Display a user-friendly error message."""
        import datetime
        if self._rate_limit_resets_at is not None:
            resets = datetime.datetime.fromtimestamp(self._rate_limit_resets_at)
            self._out.print(
                f"\n[yellow]Limite de uso atingido.[/yellow] "
                f"Resets às {resets.strftime('%H:%M')}."
            )
            return

        err = str(e)
        etype = type(e).__name__
        if self._not_logged_in:
            self._out.print(
                "\n[red]Claude Code não está autenticado.[/red]\n"
                "Execute no terminal para fazer login:\n"
                "  [bold cyan]~/.npm-global/bin/claude /login[/bold cyan]\n"
                "ou abra um terminal a partir do Claude Code desktop."
            )
            return
        if "Authentication" in err or "API key" in err:
            self._out.print(
                "\n[red]Erro de autenticação.[/red] "
                "Faça login: [bold]rf init[/bold]"
            )
        elif "connection" in err.lower() or "network" in err.lower():
            self._out.print(
                "\n[red]Sem conexão com a API.[/red] Verifique sua internet."
            )
        elif "CLINotFoundError" in etype:
            self._out.print(
                "\n[red]Claude Code CLI não encontrado.[/red]\n"
                "Instale com: [bold]npm install -g @anthropic-ai/claude-code[/bold]"
            )
        elif "exit code 1" in err.lower() or "exit code: 1" in err.lower():
            self._out.print(
                "\n[yellow]O Claude Code saiu inesperadamente.[/yellow] "
                "Possível rate limit ou erro de rede. Tente novamente em alguns minutos."
            )
        else:
            self._out.print(f"\n[red]Erro:[/red] {err}")

    async def _run_async(self, prompt: str, stream_output: bool) -> None:
        # Build tools and MCP server
        tools = self._build_mcp_tools()
        mcp_server = create_sdk_mcp_server("leafar", tools=tools)

        # Build system prompt with project context
        system = SYSTEM_PROMPT
        if self.project_context:
            system += (
                f"\n\n## Project Context (already explored — do NOT re-read these files)\n"
                f"{self.project_context}"
            )

        # Build full prompt
        prefix = ""
        if not self._session_id:
            prefix = (
                f"Project path: {self.project_path}\n"
                f"Package name: {self.config.android_package_name or '(check build.gradle)'}\n\n"
            )
        if self._active_skill and self._active_skill in SKILLS:
            prefix += SKILLS[self._active_skill]["prompt"] + "\n"
        full_prompt = prefix + prompt

        # Figma MCP: local desktop (porta 3845) → plugin oficial (mcp.figma.com) → REST fallback
        mcp_servers: dict = {"leafar": mcp_server}
        if _figma_local_available():
            mcp_servers["figma"] = {"type": "http", "url": "http://127.0.0.1:3845/mcp"}
        # else: o plugin figma@claude-plugins-official já está instalado no claude CLI
        # e será carregado automaticamente sem precisar declarar aqui.

        cli_path = _find_claude_cli()

        # Build subprocess env: clear any API key so the CLI uses OAuth,
        # and forward the OAuth token if Claude Code injected one into our env.
        _subprocess_env: dict = {"ANTHROPIC_API_KEY": ""}
        _oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if _oauth_token:
            _subprocess_env["CLAUDE_CODE_OAUTH_TOKEN"] = _oauth_token
        if self.config.github_token:
            _subprocess_env["GITHUB_PERSONAL_ACCESS_TOKEN"] = self.config.github_token

        options = ClaudeAgentOptions(
            system_prompt=system,
            mcp_servers=mcp_servers,
            permission_mode="bypassPermissions",
            disallowed_tools=["Write", "Edit", "MultiEdit", "NotebookEdit"],
            max_turns=50,
            include_partial_messages=True,
            cwd=self.project_path,
            env=_subprocess_env,
            **({"resume": self._session_id} if self._session_id else {}),
            **({"cli_path": cli_path} if cli_path else {}),
        )

        # ── Spinner ────────────────────────────────────────────────────
        start_time = time.time()
        chars_out = [0]
        stop_spinner = threading.Event()
        in_text_stream = [False]
        verb_cycle = itertools.cycle(THINKING_VERBS)
        cur_verb = [next(verb_cycle)]

        spinner_chars = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])

        def _spinner_loop() -> None:
            i = 0
            last_chat_print = [0.0]
            while not stop_spinner.is_set():
                if in_text_stream[0]:
                    time.sleep(0.05)
                    continue
                elapsed = time.time() - start_time
                m, s = int(elapsed // 60), int(elapsed % 60)
                t = f"{m}m {s}s" if m else f"{s}s"
                est = chars_out[0] // 4
                sess = (self._session_tokens + est) / 1000
                frame = next(spinner_chars)

                if self.chat_mode:
                    # Atualiza estado do toolbar para o TUI renderizar na linha sep
                    if self.toolbar_state is not None:
                        tok_str = f"{sess:.1f}k" if self._session_tokens > 0 else "···"
                        self.toolbar_state["spinner"] = f"{frame} {cur_verb[0]}... ({t})  ·  {tok_str}"
                        app = getattr(self, "_chat_app", None)
                        if app:
                            try:
                                app.invalidate()
                            except Exception:
                                pass
                    time.sleep(0.1)
                else:
                    line = (
                        f"\r\033[2m{frame} {cur_verb[0]}... ({t})"
                        f" · sessão: {sess:.1f}k\033[0m"
                    )
                    sys.stderr.write(line)
                    sys.stderr.flush()
                    time.sleep(0.1)

                i += 1
                if i % 40 == 0:
                    cur_verb[0] = next(verb_cycle)

            if not self.chat_mode:
                sys.stderr.write("\r\033[K")
                sys.stderr.flush()

        spinner_thread = threading.Thread(target=_spinner_loop, daemon=True)
        spinner_thread.start()

        # ── Message loop ───────────────────────────────────────────────
        _text_buf: list[str] = []  # buffer for chat_mode text streaming

        try:
            async for msg in query(prompt=full_prompt, options=options):  # type: ignore[union-attr]
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    ev_type = ev.get("type", "")

                    if ev_type == "message_start":
                        # Captura tokens assim que o streaming começa (inclui cache)
                        usage = ev.get("message", {}).get("usage", {})
                        tok = _parse_tokens(usage) if usage else 0
                        if tok:
                            self._session_tokens = max(self._session_tokens, tok)

                    elif ev_type == "content_block_start":
                        block = ev.get("content_block", {})
                        if block.get("type") == "text":
                            in_text_stream[0] = True
                            _text_buf.clear()
                            if not self.chat_mode:
                                # Clear spinner line in non-chat mode only
                                sys.stderr.write("\r\033[K")
                                sys.stderr.flush()

                    elif ev_type == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                chars_out[0] += len(text)
                                if self.chat_mode:
                                    # Buffer — emit atomically at block_stop
                                    _text_buf.append(text)
                                else:
                                    sys.stdout.write(text)
                                    sys.stdout.flush()

                    elif ev_type == "content_block_stop":
                        if in_text_stream[0]:
                            in_text_stream[0] = False
                            if self.chat_mode:
                                # Print buffered text as a single atomic write
                                block_text = "".join(_text_buf).strip()
                                if block_text:
                                    (self.chat_console or console).print(block_text)
                                _text_buf.clear()
                            else:
                                sys.stdout.write("\n")
                                sys.stdout.flush()

                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            self._log_tool_call(block.name, block.input)
                    if msg.usage:
                        self._session_tokens += _parse_tokens(msg.usage)

                elif isinstance(msg, ResultMessage):
                    stop_spinner.set()
                    spinner_thread.join(timeout=0.5)

                    # Limpa spinner da linha separadora
                    if self.toolbar_state is not None:
                        self.toolbar_state["spinner"] = ""

                    result_text = msg.result or ""
                    if msg.is_error and ("not logged in" in result_text.lower() or "login" in result_text.lower()):
                        self._not_logged_in = True

                    self._session_id = msg.session_id
                    # Usa usage do ResultMessage (inclui cache tokens)
                    # Evita dupla contagem: zera o que foi somado no AssistantMessage
                    # e usa o valor final definitivo do ResultMessage
                    if msg.usage:
                        self._session_tokens = _parse_tokens(msg.usage)
                    self._save_session()

                    elapsed = time.time() - start_time
                    m, s = int(elapsed // 60), int(elapsed % 60)
                    t = f"{m}m {s}s" if m else f"{s}s"
                    sess = self._session_tokens / 1000
                    cost = msg.total_cost_usd or 0
                    self._out.print(
                        f"[dim]{t} · sessão: {sess:.1f}k tokens · ${cost:.4f}[/dim]"
                    )

                elif isinstance(msg, RateLimitEvent):
                    # Only block when we're actually rejected — 'allowed' and
                    # 'allowed_warning' are informational only.
                    info = msg.rate_limit_info
                    status = getattr(info, "status", "allowed")
                    if status == "rejected":
                        resets_at = getattr(info, "resets_at", None)
                        # Flag so run() can show a friendly message instead of retrying
                        self._rate_limit_resets_at = resets_at
                        # Don't await — the CLI already exited; just let the exception propagate

        finally:
            stop_spinner.set()
            spinner_thread.join(timeout=0.5)
            if self.toolbar_state is not None:
                self.toolbar_state["spinner"] = ""

    async def _rate_limit_wait(self, wait: int = 60) -> None:
        """Show countdown and wait for rate limit to clear."""
        out = self._out
        out.print()
        with Progress(
            TextColumn("[yellow]Rate limit. Retomando em"),
            BarColumn(bar_width=30, style="yellow", complete_style="green"),
            TextColumn("[bold]{task.fields[remaining]}s[/bold]"),
            console=out,
            transient=True,
        ) as progress:
            task = progress.add_task("", total=wait, remaining=wait)
            for i in range(wait, 0, -1):
                progress.update(task, completed=wait - i, remaining=i)
                await asyncio.sleep(1)
        out.print("[green]Retomando...[/green]")

    # ------------------------------------------------------------------
    # Tool call logging
    # ------------------------------------------------------------------

    # Ferramentas internas do Claude Code que não devem aparecer no log do usuário
    _INTERNAL_TOOLS = {"ToolSearch", "WebSearch", "WebFetch", "TodoWrite", "TodoRead"}

    def _log_tool_call(self, name: str, inp: dict) -> None:
        # Oculta ferramentas internas do Claude Code
        base = name.split("__")[-1] if "__" in name else name
        if base in self._INTERNAL_TOOLS or name in self._INTERNAL_TOOLS:
            return
        # Friendly display name
        display_name = name.replace("mcp__leafar__", "").replace("mcp__plugin_figma_figma__", "figma:")
        brief = ", ".join(
            f"{k}={repr(v)[:60]}"
            for k, v in (inp.items() if isinstance(inp, dict) else {}.items())
            if k != "content"
        ) if inp else ""
        out = self.chat_console or console
        out.print(f"\n[cyan]▶ {display_name}[/cyan][dim]({brief})[/dim]")

    def _log_write_result(self, path: str, result: Any) -> None:
        out = self.chat_console or console
        if isinstance(result, dict):
            if "error" in result:
                out.print(f"  [red]✗ {result['error']}[/red]")
            elif "output" in result:
                lines = result.get("lines", "?")
                out.print(f"  [green]✓ {path} · {lines} linha(s)[/green]")

    # ------------------------------------------------------------------
    # Write confirmation with diff
    # ------------------------------------------------------------------

    def _show_diff(self, path: str, old_text: str, new_text: str) -> None:
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        raw = list(difflib.unified_diff(old_lines, new_lines, n=3, lineterm=""))

        old_lineno = 0
        new_lineno = 0

        out = self._out
        for line in raw[:200]:
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("@@"):
                m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if m:
                    old_lineno = int(m.group(1)) - 1
                    new_lineno = int(m.group(2)) - 1
                out.print(f"[cyan]{line}[/cyan]")
            elif line.startswith("+"):
                new_lineno += 1
                out.print(f"[dim]{new_lineno:4d}[/dim]  [green]{line}[/green]")
            elif line.startswith("-"):
                old_lineno += 1
                out.print(f"[dim]{old_lineno:4d}[/dim]  [red]{line}[/red]")
            else:
                old_lineno += 1
                new_lineno += 1
                out.print(f"[dim]{new_lineno:4d}   {line}[/dim]")

        if len(raw) > 200:
            out.print(f"[dim]  … +{len(raw) - 200} linhas omitidas[/dim]")

    def _confirm_write(self, path: str, new_content: str) -> tuple[bool, str]:
        """Show diff and arrow selector before writing. Runs in thread pool."""
        p = Path(path)

        if p.exists():
            old_text = p.read_text(errors="replace")
            if old_text == new_content:
                return True, "sem alterações"
            self._out.print(f"\n[bold yellow]── {path} ──[/bold yellow]")
            self._show_diff(path, old_text, new_content)
        else:
            lines = new_content.splitlines()
            self._out.print(f"\n[bold yellow]── novo arquivo: {path} ──[/bold yellow]")
            for i, line in enumerate(lines[:80], 1):
                self._out.print(f"[dim]{i:4d}[/dim]  [green]+{line}[/green]")
            if len(lines) > 80:
                self._out.print(f"[dim]  … +{len(lines) - 80} linhas omitidas[/dim]")

        accepted = self._arrow_select([
            ("Aceitar", True),
            ("Rejeitar", False),
        ])
        self._out.print()
        return accepted, "aceito" if accepted else "rejeitado"

    def _arrow_select(self, options: list[tuple[str, Any]]) -> Any:
        """Vertical arrow-key selector. Blocking — must run in main thread or executor."""
        import tty
        import termios

        selected = 0
        n = len(options)

        def _render(first: bool = False) -> None:
            if not first:
                sys.stdout.write(f"\033[{n}A")
            for i, (label, _) in enumerate(options):
                if i == selected:
                    sys.stdout.write(f"\r\033[K  \033[7m ❯ {label} \033[0m\n")
                else:
                    sys.stdout.write(f"\r\033[K     {label}\n")
            sys.stdout.flush()

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        sys.stdout.write("\n")
        try:
            tty.setraw(fd)
            _render(first=True)
            while True:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    seq = sys.stdin.read(2)
                    if seq == "[A":
                        selected = (selected - 1) % n
                    elif seq == "[B":
                        selected = (selected + 1) % n
                elif ch in ("\r", "\n"):
                    break
                elif ch in ("\x03", "\x04"):
                    selected = n - 1
                    break
                _render()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write("\n")
            sys.stdout.flush()

        label = options[selected][0]
        self._out.print(f"  [dim]❯ {label}[/dim]")
        return options[selected][1]

    @property
    def _out(self) -> "Console":
        """Returns chat_console when in chat mode, else the module-level console."""
        return self.chat_console or console

    def set_skill(self, skill: str | None) -> None:
        self._active_skill = skill
