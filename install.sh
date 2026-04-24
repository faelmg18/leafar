#!/usr/bin/env bash
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

echo ""
echo -e "${CYAN}      /\\        /\\"
echo -e "     /  \\      /  \\"
echo -e " ╔═══════════════════════╗"
echo -e " ║  ◉               ◉  ║"
echo -e " ╠═══════════════════════╣"
echo -e " ║  ▸ rf  ·  leafar      ║"
echo -e " ║  android  ai  agent   ║"
echo -e " ╚═══════════════════════╝${NC}"
echo -e "${DIM}   powered by Rafael Alves${NC}"
echo ""

# ── Python 3.10+ ─────────────────────────────────────────────────────────────
echo -e "${CYAN}▸ Verificando Python...${NC}"
if ! command -v python3 &>/dev/null; then
  echo -e "${RED}✗ Python 3 não encontrado. Instale em https://python.org${NC}"
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_OK=$(python3 -c "import sys; print(1 if sys.version_info >= (3,10) else 0)")
if [ "$PY_OK" != "1" ]; then
  echo -e "${RED}✗ Python $PY_VER encontrado, mas é necessário 3.10+${NC}"
  exit 1
fi
echo -e "${GREEN}✓ Python $PY_VER${NC}"

# ── pipx ─────────────────────────────────────────────────────────────────────
echo -e "${CYAN}▸ Verificando pipx...${NC}"
if ! command -v pipx &>/dev/null; then
  echo -e "${DIM}  Instalando pipx...${NC}"
  python3 -m pip install --user pipx --quiet
  python3 -m pipx ensurepath --quiet
  export PATH="$HOME/.local/bin:$PATH"
fi
echo -e "${GREEN}✓ pipx ok${NC}"

# ── Claude Code ───────────────────────────────────────────────────────────────
echo -e "${CYAN}▸ Verificando Claude Code...${NC}"
if ! command -v claude &>/dev/null && [ ! -f "$HOME/.npm-global/bin/claude" ]; then
  echo -e "${DIM}  Instalando Claude Code...${NC}"
  npm install -g @anthropic-ai/claude-code --prefix ~/.npm-global --quiet
  export PATH="$HOME/.npm-global/bin:$PATH"
fi
echo -e "${GREEN}✓ Claude Code ok${NC}"

# ── leafar ────────────────────────────────────────────────────────────────────
echo -e "${CYAN}▸ Instalando leafar (rf)...${NC}"
pipx install "git+https://github.com/faelmg18/leafar.git" --force --quiet
echo -e "${GREEN}✓ rf instalado${NC}"

# ── PATH hint ─────────────────────────────────────────────────────────────────
if ! command -v rf &>/dev/null; then
  echo ""
  echo -e "${DIM}  Adicione ao seu ~/.zshrc ou ~/.bashrc:${NC}"
  echo -e "${DIM}  export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
  echo -e "${DIM}  Depois rode: source ~/.zshrc${NC}"
fi

echo ""
echo -e "${GREEN}✓ Instalação concluída!${NC}"
echo ""
echo -e "  Próximos passos:"
echo -e "  ${CYAN}cd /seu-projeto-android${NC}"
echo -e "  ${CYAN}rf init${NC}   ← configura o projeto"
echo -e "  ${CYAN}rf chat${NC}   ← abre o Claude Code"
echo ""
