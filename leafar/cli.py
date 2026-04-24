"""CLI entry points for Leafar."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import click
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.shortcuts import PromptSession
from prompt_toolkit.styles import Style
from rich.console import Console

from .config import Config, create_env_file

console = Console()

def _print_logo() -> None:
    """Logo grande — usada no rf chat."""
    console.print("      /\\        /\\", style="cyan")
    console.print("     /  \\      /  \\", style="cyan")
    console.print(
        "[cyan] ╔═══════════════════════╗[/cyan]\n"
        "[cyan] ║  [/cyan][bold white]◉[/bold white][cyan]               [/cyan][bold white]◉[/bold white][cyan]  ║[/cyan]\n"
        "[cyan] ╠═══════════════════════╣[/cyan]\n"
        "[cyan] ║  [/cyan][bold white]▸ rf[/bold white][cyan]  ·  [/cyan][bold white]leafar[/bold white][cyan]      ║[/cyan]\n"
        "[cyan] ║  [/cyan][dim]android  ai  agent[/dim][cyan]   ║[/cyan]\n"
        "[cyan] ╚═══════════════════════╝[/cyan]\n"
        "[dim]  powered by Rafael Alves[/dim]\n"
    )


def _print_header() -> None:
    """Header compacto inspirado no logo — aparece em todo comando."""
    # As duas linhas com \ usam style= para evitar que \[ seja interpretado
    # como escape de colchete pelo Rich.
    console.print("      /\\      /\\", style="cyan")
    console.print("     /  \\    /  \\", style="cyan")
    console.print(
        "[cyan] ╔═══════════════════╗[/cyan]\n"
        "[cyan] ║ [/cyan][bold white]◉[/bold white][cyan]           [/cyan][bold white]◉[/bold white][cyan] ║[/cyan]\n"
        "[cyan] ╠═══════════════════╣[/cyan]\n"
        "[cyan] ║ [/cyan][bold white]▸ rf  ·  leafar[/bold white][cyan]   ║[/cyan]\n"
        "[cyan] ║ [/cyan][dim]android  ai  agent[/dim][cyan] ║[/cyan]\n"
        "[cyan] ╚═══════════════════╝[/cyan]\n"
        "[dim]   powered by Rafael Alves[/dim]\n"
    )


def _print_active_mcps(figma_started: bool, out: "Console | None" = None) -> None:
    """Mostra os servidores MCP que serão carregados nesta sessão."""
    import json as _json
    out = out or console

    icons = {
        "figma": "🎨",
        "github": "🐙",
        "azure-devops": "☁️",
    }
    labels = {
        "figma": "Figma",
        "github": "GitHub",
        "azure-devops": "Azure DevOps",
    }

    active = []

    if figma_started:
        active.append(("figma", "local :3333"))

    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        try:
            data = _json.loads(claude_json.read_text())
            for name, cfg in data.get("mcpServers", {}).items():
                if name == "figma" and figma_started:
                    continue
                active.append((name, cfg.get("type", "stdio")))
        except Exception:
            pass

    mcps_line = "[dim cyan]▸ MCPs:[/dim cyan]"
    parts = ["[dim]leafar[/dim] [dim](tools)[/dim]"]
    for name, transport in active:
        icon = icons.get(name, "🔌")
        label = labels.get(name, name)
        parts.append(f"[dim]{icon} {label}[/dim]")

    out.print(f"{mcps_line} {' [dim cyan]·[/dim cyan] '.join(parts)}\n")


def _handle_github_login(config: "Config") -> None:
    """Pede o GitHub PAT e salva no .env e em ~/.claude.json."""
    import getpass
    import json as _json

    current = config.github_token
    if current:
        console.print(f"[yellow]GitHub token já configurado[/yellow] [dim](termina em ...{current[-6:]})[/dim]")
        console.print("[dim]Para substituir, cole o novo token abaixo (Enter para manter o atual):[/dim]")

    try:
        token = getpass.getpass("GitHub Personal Access Token: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelado.[/dim]\n")
        return

    if not token:
        if current:
            console.print("[dim]Token mantido.[/dim]\n")
        return

    # Salva no .env
    env_locations = [
        Path(".") / ".env",
        Path.home() / ".leafar" / ".env",
    ]
    env_path = next((p for p in env_locations if p.exists()), Path(".") / ".env")
    env_text = env_path.read_text() if env_path.exists() else ""
    if "GITHUB_PERSONAL_ACCESS_TOKEN" in env_text:
        import re as _re
        env_text = _re.sub(
            r"^GITHUB_PERSONAL_ACCESS_TOKEN=.*$",
            f"GITHUB_PERSONAL_ACCESS_TOKEN={token}",
            env_text,
            flags=_re.MULTILINE,
        )
    else:
        env_text += f"\nGITHUB_PERSONAL_ACCESS_TOKEN={token}\n"
    env_path.write_text(env_text)

    # Atualiza ~/.claude.json com o servidor MCP GitHub
    claude_json_path = Path.home() / ".claude.json"
    try:
        claude_data = _json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
    except Exception:
        claude_data = {}

    claude_data.setdefault("mcpServers", {})["github"] = {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
    claude_json_path.write_text(_json.dumps(claude_data, indent=2))

    # Atualiza o env em runtime
    import os as _os
    _os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    config.github_token = token

    console.print("[green]✓ GitHub token salvo![/green]")
    console.print("[dim]  Reinicie o rf chat para ativar o MCP do GitHub.[/dim]\n")


def _handle_azure_login(config: "Config") -> None:
    """Pede o Azure DevOps PAT e org, salva no .env e em ~/.claude.json."""
    import getpass
    import json as _json
    import re as _re

    current = os.environ.get("AZURE_DEVOPS_PAT", "")
    if current:
        console.print(f"[yellow]Azure token já configurado[/yellow] [dim](termina em ...{current[-6:]})[/dim]")
        console.print("[dim]Para substituir, cole o novo token abaixo (Enter para manter o atual):[/dim]")

    try:
        token = getpass.getpass("Azure DevOps PAT: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelado.[/dim]\n")
        return

    if not token:
        if current:
            console.print("[dim]Token mantido.[/dim]\n")
        return

    try:
        org = input("URL da organização (ex: https://dev.azure.com/faelmg18): ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelado.[/dim]\n")
        return

    if not org:
        console.print("[red]URL da organização é obrigatória.[/red]\n")
        return

    # Salva no .env
    env_locations = [Path(".") / ".env", Path.home() / ".leafar" / ".env"]
    env_path = next((p for p in env_locations if p.exists()), Path(".") / ".env")
    env_text = env_path.read_text() if env_path.exists() else ""

    for key, val in [("AZURE_DEVOPS_PAT", token), ("AZURE_DEVOPS_ORG", org)]:
        if key in env_text:
            env_text = _re.sub(f"^{key}=.*$", f"{key}={val}", env_text, flags=_re.MULTILINE)
        else:
            env_text += f"\n{key}={val}\n"
    env_path.write_text(env_text)

    # Atualiza ~/.claude.json com o servidor MCP Azure DevOps
    claude_json_path = Path.home() / ".claude.json"
    try:
        claude_data = _json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
    except Exception:
        claude_data = {}

    claude_data.setdefault("mcpServers", {})["azure-devops"] = {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@tiberriver256/mcp-server-azure-devops"],
        "env": {
            "AZURE_DEVOPS_ORG_URL": org,
            "AZURE_DEVOPS_AUTH_METHOD": "pat",
            "AZURE_DEVOPS_PAT": token,
        },
    }
    claude_json_path.write_text(_json.dumps(claude_data, indent=2))

    os.environ["AZURE_DEVOPS_PAT"] = token
    os.environ["AZURE_DEVOPS_ORG"] = org

    console.print("[green]✓ Azure DevOps configurado![/green]")
    console.print("[dim]  Reinicie o rf chat para ativar o MCP do Azure DevOps.[/dim]\n")


def _start_figma_mcp(token: str) -> "subprocess.Popen | None":
    """Inicia figma-developer-mcp em background na porta 3333. Retorna o processo ou None."""
    if not token:
        return None

    # Já rodando?
    try:
        with socket.create_connection(("127.0.0.1", 3333), timeout=0.5):
            return None
    except OSError:
        pass

    env = {**os.environ, "FIGMA_API_KEY": token, "DO_NOT_TRACK": "1"}
    try:
        proc = subprocess.Popen(
            ["npx", "-y", "figma-developer-mcp"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None

    # Aguarda até 6s para o servidor subir
    for _ in range(20):
        time.sleep(0.3)
        try:
            with socket.create_connection(("127.0.0.1", 3333), timeout=0.5):
                return proc
        except OSError:
            pass

    return proc


def _make_agent(project: str, header: bool = True):
    from .agent import LeafarAgent
    if header:
        _print_header()
    config = Config(project_path=project)
    errors = config.validate()
    if errors:
        for err in errors:
            console.print(f"[red]Config error:[/red] {err}")
        sys.exit(1)
    return LeafarAgent(config, project_path=project)


# ---------------------------------------------------------------------------
# Smart group — unknown subcommands become natural-language prompts
# ---------------------------------------------------------------------------

_SHELL_COMMANDS = {
    # zsh/bash builtins
    "source", "export", "cd", "alias", "unalias", "echo", "printf", "read",
    "set", "unset", "exec", "eval", "exit", "return", "shift", "test",
    "builtin", "command", "type", "which", "hash", "bind", "trap",
    # common system commands that should never go to the agent
    "ls", "cat", "grep", "find", "mv", "cp", "rm", "mkdir", "touch",
    "git", "python", "python3", "pip", "pip3", "brew", "curl", "wget",
    "ssh", "scp", "rsync", "tar", "zip", "unzip", "open", "sudo",
    "adb", "gradle", "gradlew", "java", "javac", "kotlin", "kotlinc",
}


class _SmartGroup(click.Group):
    """A Click Group that routes unrecognised commands to the agent as
    natural-language prompts instead of raising an error.

    Example:
        leafar rode o app e me fala o que tem na primeira tela
        → same as: leafar ask "rode o app e me fala o que tem na primeira tela"
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            # Se o primeiro token é um comando de shell conhecido, não encaminha
            # para o agente — deixa o Click levantar o UsageError normalmente.
            first = args[0].split()[0] if args else ""
            if first in _SHELL_COMMANDS:
                raise

            # Primeiro token não reconhecido → trata como linguagem natural.
            project = ctx.params.get("project", ".")
            forwarded = ["ask", "--project", project] + list(args)
            return super().resolve_command(ctx, forwarded)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group(cls=_SmartGroup, invoke_without_command=True)
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
@click.option("--version", is_flag=True, is_eager=True, help="Show version and exit.")
@click.pass_context
def main(ctx: click.Context, project: str, version: bool) -> None:
    """Leafar — Intelligent Android Development CLI powered by Rafael Alves.

    \b
    You can use natural language directly, without subcommands:

    \b
      leafar rode o app e me fala o que você vê na primeira tela
      leafar cria uma tela de login baseada nesse Figma: https://...
      leafar tem um bug na tela de perfil, a data tá errada — corrige
    """
    if version:
        from . import __version__
        console.print(f"leafar {__version__}")
        return
    ctx.ensure_object(dict)
    ctx.obj["project"] = project
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


# ---------------------------------------------------------------------------
# ask — main natural-language command
# ---------------------------------------------------------------------------

@main.command()
@click.argument("prompt", nargs=-1, required=True)
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
@click.option("--no-stream", is_flag=True, help="Disable streaming output.")
def ask(prompt: tuple[str, ...], project: str, no_stream: bool) -> None:
    """Ask Leafar anything about your Android project.

    \b
    Examples:
      leafar ask "create a login screen from https://figma.com/file/..."
      leafar ask "navigate to the profile screen and check the date format"
      leafar ask "why is the RecyclerView flickering in HomeFragment?"
    """
    _make_agent(project).run(" ".join(prompt), stream_output=not no_stream)


# ---------------------------------------------------------------------------
# chat — interactive REPL
# ---------------------------------------------------------------------------

_SLASH_COMMANDS = [
    # ── Skills / modos ───────────────────────────────────────────────
    ("/emulador",    "Ativa modo emulador — screenshots e interação com ADB"),
    ("/build",       "Ativa modo build — compila, instala e testa o app"),
    ("/figma",       "Ativa modo Figma — gera UI Android a partir de designs"),
    ("/debug",       "Ativa modo debug — rastreia e corrige bugs visualmente"),
    ("/review",      "Ativa modo review — analisa o código com boas práticas Android"),
    ("/testes",      "Ativa modo testes — escreve e roda testes unitários e de UI"),
    ("/arquitetura", "Ativa modo arquitetura — mapeia módulos, camadas e dependências"),
    # ── Configuração da CLI ──────────────────────────────────────────
    ("/reset",        "Limpa o histórico da sessão e começa do zero"),
    ("/tokens",       "Mostra o total de tokens usados na sessão"),
    ("/contexto",     "Mostra o resumo do projeto salvo pelo agente"),
    ("/projeto",      "Muda o caminho do projeto Android ativo"),
    ("/figma-json",    "Usa um JSON local do Figma em vez da API (evita rate limit)"),
    ("/github-login",  "Salva o GitHub Personal Access Token para integração MCP"),
    ("/azure-login",   "Salva o Azure DevOps PAT para integração MCP"),
    ("/claude-login",  "Faz login no Claude Code (abre browser para autenticação)"),
    ("/clear",        "Limpa a tela do terminal"),
    ("/sair",         "Encerra o chat"),
]

_SKILL_NAMES = {"emulador", "build", "figma", "debug", "review", "testes", "arquitetura"}


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        word = text.lstrip("/").lower()
        for cmd, desc in _SLASH_COMMANDS:
            if cmd.lstrip("/").startswith(word):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )



@main.command()
@click.option("--project", "-p", default=".", show_default=True)
def chat(project: str) -> None:
    """Abre o Claude Code nativo no diretório do projeto."""
    import shutil as _shutil

    claude_bin = _shutil.which("claude") or str(Path.home() / ".npm-global" / "bin" / "claude")
    if not Path(claude_bin).exists():
        console.print("[red]Claude Code não encontrado.[/red]")
        console.print("Instale com: npm install -g @anthropic-ai/claude-code --prefix ~/.npm-global")
        return

    proj = Path(project).resolve()
    env = {**os.environ, "RF_PROJECT_PATH": str(proj)}
    # claude pode ser script Node.js — usa node explicitamente se necessário
    node_bin = _shutil.which("node")
    if node_bin:
        os.execve(node_bin, [node_bin, claude_bin], env)
    else:
        # fallback: deixa o shell resolver o shebang
        os.execv("/bin/sh", ["/bin/sh", "-c", f'exec "{claude_bin}"'])


@main.command()
def serve() -> None:
    """Inicia o MCP server (usado internamente pelo Claude Code)."""
    from .mcp_server import run as _mcp_run
    _mcp_run()


# ---------------------------------------------------------------------------
# Shortcut commands
# ---------------------------------------------------------------------------

@main.command()
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
def run(project: str) -> None:
    """Build, install, and launch the app in the emulator."""
    _make_agent(project).run(
        "Build the project, install it on the connected emulator/device, "
        "launch the app, and take a screenshot to confirm it started."
    )


@main.command()
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
def screenshot(project: str) -> None:
    """Take a screenshot of the current emulator screen."""
    _make_agent(project).run(
        "Take a screenshot of the current emulator screen and describe what you see."
    )


@main.command()
@click.argument("screen")
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
def navigate(screen: str, project: str) -> None:
    """Navigate to a specific screen in the running app.

    \b
    Example:
      leafar navigate ProfileScreen
    """
    _make_agent(project).run(
        f"Navigate to the '{screen}' screen in the running app. "
        "Take a screenshot before and after navigation."
    )


@main.command()
@click.argument("description")
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
def debug(description: str, project: str) -> None:
    """Debug an issue in the app.

    \b
    Example:
      leafar debug "the date on the profile screen shows the wrong format"
    """
    _make_agent(project).run(
        f"Debug this issue: {description}\n\n"
        "Steps: navigate to the relevant screen, visually confirm the bug, "
        "trace it to the source code, fix it, then verify the fix visually."
    )


@main.command()
@click.argument("figma_url")
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
@click.option("--output", "-o", default=None, help="Output file path for generated code.")
def codegen(figma_url: str, project: str, output: str | None) -> None:
    """Generate Android UI code from a Figma design URL.

    \b
    Example:
      leafar codegen "https://www.figma.com/file/ABC123/..."
    """
    prompt = (
        f"Fetch this Figma design and generate the corresponding Android UI code: {figma_url}\n"
        "Analyse the design carefully (layout, spacing, colours, typography) and produce "
        "idiomatic Kotlin code that matches the project's existing architecture and UI toolkit."
    )
    if output:
        prompt += f"\nSave the generated code to: {output}"
    _make_agent(project).run(prompt)


# ---------------------------------------------------------------------------
# init — setup wizard
# ---------------------------------------------------------------------------

def _write_claude_md(path: Path, package_name: str, project_path: str) -> None:
    content = f"""# Leafar — Android AI Agent

Você é um assistente especializado em desenvolvimento Android para o projeto `{package_name or 'este projeto'}`.

## Projeto
- **Path**: `{project_path}`
- **Package**: `{package_name}`
- **Tools disponíveis**: MCP server `leafar` (Android ADB, Figma, Gradle, filesystem)

## Capacidades
1. **Interação com emulador** — screenshots, tap, swipe, input, navegação via ADB
2. **Leitura e escrita de código** — Kotlin, XML layouts, Gradle, recursos
3. **Build system** — compilar, instalar, rodar e testar o app
4. **Debug visual** — inspecionar telas, hierarquia de UI, encontrar e corrigir bugs

## Regras importantes
- Para modificar arquivos use SEMPRE `write_file` (nunca shell tricks como `sed -i`)
- Ao perguntar algo ao usuário, PARE todos os tool calls e aguarde a resposta
- Prefira Jetpack Compose para nova UI, a menos que o projeto use XML
- Siga MVVM + Clean Architecture nas implementações

## Tools MCP disponíveis (`mcp__leafar__*`)
- `take_screenshot` — screenshot do emulador atual
- `tap`, `tap_element`, `swipe`, `press_key`, `input_text` — interação com a tela
- `run_adb_command` — comando ADB direto
- `get_ui_hierarchy`, `get_current_activity` — inspeção da UI
- `launch_app`, `stop_app`, `install_apk` — gerenciamento do app
- `read_file`, `write_file`, `list_files`, `search_in_files` — filesystem
- `get_project_structure` — estrutura de diretórios
- `gradle_build`, `gradle_install_and_run`, `gradle_run_tests` — build
- `run_command` — comando shell no projeto
"""
    path.write_text(content)


@main.command("figma-login")
def figma_login() -> None:
    """Autentica com o Figma via OAuth (MCP). Abre o browser para login."""
    from .config import Config
    from .figma_auth import login, is_logged_in

    if is_logged_in():
        console.print("[yellow]Você já está autenticado no Figma.[/yellow]")
        console.print("Para fazer login com outra conta: [bold]rf figma-logout[/bold]")
        return

    cfg = Config()
    if not cfg.figma_client_id or not cfg.figma_client_secret:
        console.print(
            "[red]FIGMA_CLIENT_ID e FIGMA_CLIENT_SECRET não encontrados no .env[/red]\n"
            "Adicione ao .env do projeto:\n"
            "  FIGMA_CLIENT_ID=...\n"
            "  FIGMA_CLIENT_SECRET=..."
        )
        return

    login(cfg.figma_client_id, cfg.figma_client_secret)


@main.command("figma-logout")
def figma_logout() -> None:
    """Remove a autenticação Figma salva localmente."""
    from .figma_auth import logout, is_logged_in
    if not is_logged_in():
        console.print("[dim]Nenhuma sessão Figma ativa.[/dim]")
        return
    logout()
    console.print("[green]Sessão Figma removida.[/green]")


@main.command("figma-status")
def figma_status() -> None:
    """Mostra o status da integração Figma MCP."""
    from .agent import _figma_local_available, _find_claude_cli
    local_ok = _figma_local_available()
    cli = _find_claude_cli()
    if local_ok:
        console.print("[green]✓ Figma desktop MCP ativo[/green] [dim](localhost:3845)[/dim]")
        console.print("[dim]  O agente usará o Figma MCP automaticamente.[/dim]")
    elif cli and "npm-global" in cli:
        console.print("[green]✓ Plugin figma@claude-plugins-official instalado[/green]")
        console.print(f"[dim]  claude: {cli}[/dim]")
        console.print("[dim]  Para autenticar no Figma, rode: rf ou pergunte 'autentique meu figma'[/dim]")
    else:
        console.print("[yellow]Plugin figma não encontrado.[/yellow]")
        console.print(
            "\nInstale com:\n"
            "  [bold]npm install -g @anthropic-ai/claude-code --prefix ~/.npm-global[/bold]\n"
            "  [bold]~/.npm-global/bin/claude plugin marketplace add anthropics/claude-plugins-official[/bold]\n"
            "  [bold]~/.npm-global/bin/claude plugin install figma@claude-plugins-official[/bold]"
        )


@main.command()
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
def init(project: str) -> None:
    """Wizard de configuração passo a passo."""
    import getpass
    import json as _json
    import re as _re
    import xml.etree.ElementTree as _ET

    proj_path = Path(project).resolve()
    env_path = proj_path / ".env"

    _print_logo()
    console.print("[bold cyan]Setup Wizard[/bold cyan] [dim]— configuração passo a passo[/dim]\n")

    # ── 1/2 Claude Code ──────────────────────────────────────────────────
    console.print("[bold]1/2  Claude Code[/bold]")
    import shutil as _shutil

    npm_global_bin = Path.home() / ".npm-global" / "bin"
    claude_candidates = [
        npm_global_bin / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    ]
    claude_bin = next((p for p in claude_candidates if p.exists()), None)

    # ── Verifica/instala Node.js ─────────────────────────────────────────
    if not _shutil.which("node") and not _shutil.which("npm"):
        console.print("  [yellow]⚠[/yellow]  Node.js não encontrado.")
        console.print("  [dim]Instalando via Homebrew...[/dim]")
        try:
            if _shutil.which("brew"):
                subprocess.run(["brew", "install", "node"], check=True)
                console.print("  [green]✓[/green] Node.js instalado")
            else:
                console.print(
                    "  [red]✗[/red]  Homebrew não encontrado.\n"
                    "  Instale o Node.js manualmente: [bold]https://nodejs.org[/bold]\n"
                    "  Depois rode [bold]rf init[/bold] novamente."
                )
                sys.exit(1)
        except subprocess.CalledProcessError:
            console.print("  [red]✗[/red]  Falha ao instalar Node.js. Instale manualmente: https://nodejs.org")
            sys.exit(1)
    else:
        node_ver = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
        console.print(f"  [green]✓[/green] Node.js [dim]{node_ver}[/dim]")

    # ── Verifica/instala Claude Code ─────────────────────────────────────
    npm_global = Path.home() / ".npm-global"
    install_cjs = npm_global / "lib" / "node_modules" / "@anthropic-ai" / "claude-code" / "install.cjs"
    node_bin = _shutil.which("node") or "node"

    if not claude_bin:
        console.print("  [dim]Instalando Claude Code...[/dim]")
        npm_bin = _shutil.which("npm") or "npm"
        try:
            subprocess.run(
                [npm_bin, "install", "-g", "@anthropic-ai/claude-code",
                 "--prefix", str(npm_global)],
                check=True,
            )
            claude_bin = npm_global_bin / "claude"
            # Adiciona ao PATH no .zshrc/.bashrc
            for rc_file in [Path.home() / ".zshrc", Path.home() / ".bashrc"]:
                if rc_file.exists():
                    content = rc_file.read_text()
                    path_line = 'export PATH="$HOME/.npm-global/bin:$PATH"'
                    if path_line not in content:
                        with rc_file.open("a") as f:
                            f.write(f"\n# rf — leafar\n{path_line}\n")
            os.environ["PATH"] = f"{npm_global_bin}:{os.environ.get('PATH', '')}"
            console.print("  [green]✓[/green] Claude Code instalado")
            console.print(f"  [dim]PATH atualizado em ~/.zshrc[/dim]")
        except subprocess.CalledProcessError:
            console.print("  [red]✗[/red]  Falha ao instalar Claude Code.")
            sys.exit(1)
    else:
        console.print("  [green]✓[/green] Claude Code instalado")

    # Garante binário nativo — sempre roda install.cjs se existir
    # (o postinstall pode não executar com --prefix ou em instalações antigas)
    if install_cjs.exists():
        subprocess.run([node_bin, str(install_cjs)], check=False,
                       cwd=str(install_cjs.parent),
                       capture_output=True)  # silencioso — só baixa se faltar

    # ── Verifica/faz login ───────────────────────────────────────────────
    session_file = Path.home() / ".claude" / "credentials.json"
    alt_session  = Path.home() / ".claude.json"
    logged_in = session_file.exists() or (
        alt_session.exists() and '"oauthAccount"' in alt_session.read_text()
    )
    if logged_in:
        console.print("  [green]✓[/green] Sessão OAuth ativa\n")
    else:
        console.print("  [yellow]⚠[/yellow]  Não autenticado.")
        try:
            do_login = click.confirm("  Fazer login agora?", default=True)
        except (KeyboardInterrupt, EOFError):
            do_login = False
        if do_login and claude_bin:
            subprocess.run(f'"{claude_bin}" /login', shell=True)
        console.print()

    # ── 2/2 Projeto Android ──────────────────────────────────────────────
    console.print("[bold]2/2  Projeto Android[/bold]")

    # Auto-detecta package name
    package_name = ""
    main_activity = ".MainActivity"

    for gradle_file in [
        proj_path / "app" / "build.gradle.kts",
        proj_path / "app" / "build.gradle",
    ]:
        if gradle_file.exists():
            m = _re.search(r'applicationId\s*[=:]\s*"([^"]+)"', gradle_file.read_text())
            if m:
                package_name = m.group(1)
                break

    if not package_name:
        manifest = proj_path / "app" / "src" / "main" / "AndroidManifest.xml"
        if manifest.exists():
            try:
                root = _ET.parse(manifest).getroot()
                package_name = root.get("package", "")
            except Exception:
                pass

    if package_name:
        console.print(f"  [green]✓[/green] Package detectado: [bold]{package_name}[/bold]")
    else:
        console.print("  [dim]Package não detectado automaticamente.[/dim]")
        try:
            package_name = input("  Package name (ex: com.example.app): ").strip()
        except (KeyboardInterrupt, EOFError):
            package_name = ""

    # Detecta emulador/dispositivo ADB
    adb_device = ""
    try:
        adb_out = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5
        )
        devices = [
            line.split()[0]
            for line in adb_out.stdout.splitlines()[1:]
            if line.strip() and "device" in line and "offline" not in line
        ]
        if devices:
            adb_device = devices[0]
            console.print(f"  [green]✓[/green] Dispositivo ADB: [bold]{adb_device}[/bold]")
        else:
            console.print("  [dim]Nenhum dispositivo ADB conectado (pode conectar depois)[/dim]")
    except Exception:
        console.print("  [dim]ADB não encontrado (pode configurar depois)[/dim]")

    console.print()

    gh_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    az_token = os.getenv("AZURE_DEVOPS_PAT", "")
    az_org   = os.getenv("AZURE_DEVOPS_ORG", "")

    # ── Escreve .env ─────────────────────────────────────────────────────
    if env_path.exists():
        try:
            overwrite = click.confirm(f".env já existe em {env_path}. Sobrescrever?", default=False)
        except (KeyboardInterrupt, EOFError):
            overwrite = False
        if not overwrite:
            console.print("[dim].env mantido sem alterações.[/dim]")
            env_path = None  # type: ignore[assignment]

    if env_path is not None:
        create_env_file(
            env_path,
            package_name=package_name,
            main_activity=main_activity,
            adb_device=adb_device,
            gh_token=gh_token,
            az_token=az_token,
            az_org=az_org,
        )
        console.print(f"  [green]✓[/green] .env salvo em [bold]{env_path}[/bold]")

        # .gitignore
        gitignore = proj_path / ".gitignore"
        if gitignore.exists():
            text = gitignore.read_text()
            additions = [e for e in (".env", ".rf_session.json") if e not in text]
            if additions:
                with gitignore.open("a") as f:
                    f.write("\n# Leafar\n" + "\n".join(additions) + "\n")
                console.print(f"  [dim]{', '.join(additions)} adicionados ao .gitignore[/dim]")

    # ── Registra MCP server ──────────────────────────────────────────────
    import shutil
    console.print("[dim]Registrando MCP server leafar...[/dim]")
    claude_json_path = Path.home() / ".claude.json"
    try:
        claude_data = _json.loads(claude_json_path.read_text()) if claude_json_path.exists() else {}
    except Exception:
        claude_data = {}

    rf_bin = shutil.which("rf") or "rf"
    claude_data.setdefault("mcpServers", {})["leafar"] = {
        "type": "stdio",
        "command": rf_bin,
        "args": ["serve"],
        "env": {"RF_PROJECT_PATH": str(proj_path)},
    }
    claude_json_path.write_text(_json.dumps(claude_data, indent=2))
    console.print("  [green]✓[/green] MCP leafar registrado em ~/.claude.json")

    # ── Gera CLAUDE.md ───────────────────────────────────────────────────
    claude_md_path = proj_path / "CLAUDE.md"
    if not claude_md_path.exists():
        _write_claude_md(claude_md_path, package_name, str(proj_path))
        console.print(f"  [green]✓[/green] CLAUDE.md gerado em {claude_md_path}")
    else:
        console.print(f"  [dim]CLAUDE.md já existe — mantido[/dim]")

    # ── Pronto ───────────────────────────────────────────────────────────
    console.print("\n[bold green]✓ Configuração concluída![/bold green]")
    console.print(f"  Projeto: [bold]{proj_path}[/bold]")
    if package_name:
        console.print(f"  Package: [bold]{package_name}[/bold]")
    console.print("\n  Rode: [bold cyan]rf chat[/bold cyan]\n")


@main.command()
@click.option("--project", "-p", default=".", show_default=True, help="Android project path.")
def reset(project: str) -> None:
    """Apaga o histórico da sessão e começa do zero."""
    agent = _make_agent(project)
    agent.reset()
    console.print("[yellow]Sessão apagada.[/yellow] Próximo comando começa do zero.")


# ---------------------------------------------------------------------------
# hook — instala/remove o command_not_found_handler no zsh
# ---------------------------------------------------------------------------

_HOOK_START = "# >>> rf hook start <<<"
_HOOK_END   = "# >>> rf hook end <<<"
_HOOK_BODY  = """\
# >>> rf hook start <<<

# ── Logo no lado direito do prompt quando em projeto rf ──────────────────
function _rf_update_rprompt() {
    if [[ -f ".env" ]] && grep -q "ANTHROPIC_API_KEY" ".env" 2>/dev/null; then
        RPROMPT="%F{cyan}/\\ /\\%f %F{cyan}╔%f%F{white}◉%f%F{cyan}═%f%F{white}◉%f%F{cyan}╗%f %F{cyan}▸rf%f  "
    else
        RPROMPT=""
    fi
}
precmd_functions+=(_rf_update_rprompt)

# ── ZLE widget: intercepta Enter e redireciona linguagem natural pro rf ──
# Comandos de shell que NUNCA devem ir pro rf (builtins + comuns)
_rf_shell_cmds=(
    source . export cd alias unalias echo printf read set unset exec eval
    exit return shift test builtin command type which hash bind trap
    ls cat grep find mv cp rm mkdir touch git python python3 pip pip3
    brew curl wget ssh scp rsync tar zip unzip open sudo env
    adb gradle gradlew java javac kotlin kotlinc noglob
)

function _rf_smart_enter() {
    local input="$BUFFER"
    local first="${input%% *}"

    if [[ -f ".env" ]] && grep -q "ANTHROPIC_API_KEY" ".env" 2>/dev/null; then
        if [[ "$first" == "rf" ]]; then
            # Usuário digitou rf explicitamente — usa noglob pra evitar
            # expansão de ? * [] no resto da frase
            BUFFER="noglob ${input}"
        elif ! (( $+commands[$first] )) \\
            && ! (( $+aliases[$first] )) \\
            && ! (( $+functions[$first] )) \\
            && ! (( $+builtins[$first] )) \\
            && ! (( $_rf_shell_cmds[(Ie)$first] )); then
            # Linguagem natural — redireciona pro rf com args entre aspas
            BUFFER="noglob rf ${(q)input}"
        fi
    fi
    zle accept-line
}
zle -N _rf_smart_enter
bindkey "^M" _rf_smart_enter
bindkey "^J" _rf_smart_enter

# >>> rf hook end <<<"""


@main.command()
def hook() -> None:
    """Instala o hook no zsh para dispensar o prefixo 'rf'.

    \b
    Após instalar, dentro de qualquer projeto configurado com 'rf init'
    você pode digitar diretamente no terminal:

    \b
      você consegue criar um mock de compra no billing?
      roda o app e me fala o que você vê na primeira tela
      tem um bug na tela de checkout — corrige

    Para remover o hook: rf unhook
    """
    zshrc = Path.home() / ".zshrc"
    content = zshrc.read_text() if zshrc.exists() else ""

    if _HOOK_START in content:
        console.print("[yellow]Hook já está instalado em ~/.zshrc[/yellow]")
        console.print("Para remover: [bold]rf unhook[/bold]")
        return

    with zshrc.open("a") as f:
        f.write(f"\n{_HOOK_BODY}\n")

    console.print("[green]Hook instalado em ~/.zshrc[/green]")
    console.print(
        "\nRoda o comando abaixo para ativar na sessão atual:\n"
        "[bold cyan]source ~/.zshrc[/bold cyan]"
    )
    console.print(
        "\nDepois é só digitar direto no terminal dentro do projeto — sem 'rf'."
    )


@main.command()
def unhook() -> None:
    """Remove o hook do zsh instalado por 'rf hook'."""
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists() or _HOOK_START not in zshrc.read_text():
        console.print("[dim]Hook não encontrado em ~/.zshrc[/dim]")
        return

    lines = zshrc.read_text().splitlines(keepends=True)
    inside = False
    cleaned = []
    for line in lines:
        if _HOOK_START in line:
            inside = True
        if not inside:
            cleaned.append(line)
        if _HOOK_END in line:
            inside = False

    zshrc.write_text("".join(cleaned))
    console.print("[green]Hook removido de ~/.zshrc[/green]")
    console.print("Rode [bold cyan]source ~/.zshrc[/bold cyan] para aplicar.")
