#!/usr/bin/env bash
# =============================================================================#
# RLM — Instalador one-liner                                                   #
#                                                                               #
# Uso:                                                                          #
#   curl -sSf https://raw.githubusercontent.com/SEU_USUARIO/rlm/main/install.sh | bash
#                                                                               #
# Ou localmente (após clonar o repositório):                                    #
#   bash install.sh                                                             #
#                                                                               #
# O que este script faz:                                                        #
#   1. Detecta o sistema operacional                                            #
#   2. Verifica Python ≥ 3.11                                                   #
#   3. Instala `uv` (gestor de pacotes Python ultra-rápido) se necessário       #
#   4. Cria o ambiente virtual e instala as dependências do RLM                 #
#   5. Instala o comando `rlm` globalmente no PATH do uv                        #
#   6. Executa `rlm setup` (wizard interativo de configuração)                  #
# =============================================================================#

set -euo pipefail

# --------------------------------------------------------------------------- #
# Cores e formatação                                                            #
# --------------------------------------------------------------------------- #
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}   $*"; }
err()  { echo -e "${RED}✗ Erro:${RESET} $*" >&2; exit 1; }
info() { echo -e "${CYAN}→${RESET}  $*"; }
banner() {
  echo
  echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${CYAN}║          RLM — Recursive Language Model      ║${RESET}"
  echo -e "${BOLD}${CYAN}║              Instalador v0.1                 ║${RESET}"
  echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${RESET}"
  echo
}

# --------------------------------------------------------------------------- #
# Detecção de OS                                                                #
# --------------------------------------------------------------------------- #
detect_os() {
  local os
  case "$(uname -s)" in
    Linux*)
      if grep -qi microsoft /proc/version 2>/dev/null; then
        os="WSL (Windows Subsystem for Linux)"
      else
        os="Linux"
      fi
      ;;
    Darwin*) os="macOS" ;;
    CYGWIN*|MINGW*|MSYS*) os="Windows (Git Bash / MSYS2)" ;;
    *) os="Desconhecido" ;;
  esac
  echo "$os"
}

# --------------------------------------------------------------------------- #
# Verifica Python                                                               #
# --------------------------------------------------------------------------- #
check_python() {
  local py_bin=""
  for candidate in python3.12 python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
      local ver
      ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
      local major minor
      major=$(echo "$ver" | cut -d. -f1)
      minor=$(echo "$ver" | cut -d. -f2)
      if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 11 ]; then
        py_bin="$candidate"
        break
      fi
    fi
  done
  echo "$py_bin"
}

# --------------------------------------------------------------------------- #
# Instala uv                                                                    #
# --------------------------------------------------------------------------- #
install_uv() {
  info "Instalando uv (gestor de pacotes Python)..."
  if command -v curl &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget &>/dev/null; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    err "curl ou wget necessários para instalar uv."
  fi

  # Adiciona uv ao PATH da sessão atual
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

  if ! command -v uv &>/dev/null; then
    err "uv instalado mas não encontrado no PATH. Adicione \$HOME/.local/bin ao PATH e execute novamente."
  fi
  ok "uv instalado: $(uv --version)"
}

# --------------------------------------------------------------------------- #
# Localiza a raiz do repositório                                                #
# --------------------------------------------------------------------------- #
find_project_root() {
  # Se já estamos dentro do repo clonado, usa o diretório atual
  if [ -f "pyproject.toml" ] && grep -q '"rlm"' pyproject.toml 2>/dev/null; then
    echo "$(pwd)"
    return
  fi
  # Caso contrário, clona
  echo ""
}

# --------------------------------------------------------------------------- #
# MAIN                                                                          #
# --------------------------------------------------------------------------- #
main() {
  banner

  local os_name
  os_name=$(detect_os)
  info "Sistema operacional: ${BOLD}${os_name}${RESET}"

  # ── Python ──────────────────────────────────────────────────────────────── #
  local py_bin
  py_bin=$(check_python)
  if [ -z "$py_bin" ]; then
    err "Python 3.11 ou superior não encontrado.\nInstale com: sudo apt install python3.12 (Linux) ou brew install python@3.12 (macOS)"
  fi
  ok "Python: $("$py_bin" --version)"

  # ── uv ──────────────────────────────────────────────────────────────────── #
  if ! command -v uv &>/dev/null; then
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  fi
  if ! command -v uv &>/dev/null; then
    install_uv
  else
    ok "uv: $(uv --version)"
  fi

  # ── Localiza/clona repositório ───────────────────────────────────────────── #
  local project_root
  project_root=$(find_project_root)

  if [ -z "$project_root" ]; then
    info "Repositório não encontrado localmente — clonando..."
    if ! command -v git &>/dev/null; then
      err "git não encontrado. Instale git primeiro ou execute dentro do diretório do projeto."
    fi
    local clone_dir="${HOME}/.rlm/repo"
    git clone https://github.com/SEU_USUARIO/rlm.git "$clone_dir" 2>/dev/null || \
      err "Falha ao clonar o repositório. Ajuste a URL do repositório em install.sh."
    project_root="$clone_dir"
  fi

  ok "Raiz do projeto: ${project_root}"
  cd "$project_root"

  # ── Instala dependências ─────────────────────────────────────────────────── #
  info "Instalando dependências Python..."
  uv sync
  ok "Dependências instaladas"

  # Instala o comando `rlm` no ambiente
  info "Registrando comando 'rlm'..."
  uv pip install -e . --quiet
  ok "Pacote RLM instalado (modo editable)"

  # Verifica se `rlm` está acessível
  if uv run rlm version &>/dev/null; then
    ok "Comando 'rlm' disponível: $(uv run rlm version)"
  else
    warn "Comando 'rlm' não encontrado diretamente. Use: uv run rlm <comando>"
  fi

  # ── Wizard de configuração ───────────────────────────────────────────────── #
  echo
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${GREEN}   Instalação concluída! Iniciando configuração...${RESET}"
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
  echo

  uv run rlm setup

  echo
  ok "Tudo pronto! Referência rápida:"
  echo
  echo -e "  ${CYAN}rlm start${RESET}          # Inicia o servidor RLM"
  echo -e "  ${CYAN}rlm stop${RESET}           # Para o servidor"
  echo -e "  ${CYAN}rlm status${RESET}         # Verifica processos ativos"
  echo -e "  ${CYAN}rlm token rotate${RESET}   # Rotaciona tokens de segurança"
  echo -e "  ${CYAN}rlm setup${RESET}          # Reconfigura (execute a qualquer momento)"
  echo
}

main "$@"
