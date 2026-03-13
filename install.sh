#!/usr/bin/env bash
# =============================================================================#
# Arkhe — Quickstart Installer                                                 #
#                                                                               #
# Uso:                                                                          #
#   curl -fsSL https://raw.githubusercontent.com/O-guardiao/Arkhe/main/install.sh | bash
#                                                                               #
# Ou localmente (após clonar o repositório):                                    #
#   bash install.sh                                                             #
#                                                                               #
# O que este script faz:                                                        #
#   1. Detecta o sistema operacional                                            #
#   2. Verifica Python >= 3.11                                                  #
#   3. Instala `uv` se necessário                                               #
#   4. Clona ou atualiza o repositório em ~/.arkhe/repo                         #
#   5. Executa `uv sync`                                                        #
#   6. Gera um `.env` com defaults seguros e cinco tokens de runtime            #
#   7. Cria wrappers `arkhe` e `rlm` em ~/.local/bin                            #
#                                                                               #
# Variáveis opcionais:                                                          #
#   ARKHE_REPO_URL, ARKHE_INSTALL_DIR, ARKHE_BIN_DIR, ARKHE_MODEL               #
# =============================================================================#

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

REPO_URL="${ARKHE_REPO_URL:-https://github.com/O-guardiao/Arkhe.git}"
INSTALL_BASE="${ARKHE_INSTALL_DIR:-$HOME/.arkhe}"
CLONE_DIR="$INSTALL_BASE/repo"
WRAPPER_DIR="${ARKHE_BIN_DIR:-$HOME/.local/bin}"
DEFAULT_MODEL="${ARKHE_MODEL:-gpt-4o-mini}"

ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗ Erro:${RESET} $*" >&2; exit 1; }
info() { echo -e "${CYAN}→${RESET}  $*"; }

banner() {
  echo
  echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${CYAN}║               Arkhe Quickstart               ║${RESET}"
  echo -e "${BOLD}${CYAN}║         Bootstrap local do runtime           ║${RESET}"
  echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${RESET}"
  echo
}

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

check_python() {
  local py_bin=""
  for candidate in python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
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

install_uv() {
  info "Instalando uv..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    err "curl ou wget são necessários para instalar uv."
  fi

  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

  if ! command -v uv >/dev/null 2>&1; then
    err "uv foi instalado, mas não entrou no PATH atual. Adicione $HOME/.local/bin ao PATH e execute novamente."
  fi
  ok "uv pronto: $(uv --version)"
}

ensure_dependencies() {
  command -v git >/dev/null 2>&1 || err "git não encontrado. Instale git antes de continuar."
}

find_project_root() {
  if [ -f "pyproject.toml" ] && grep -q 'name = "arkhe"' pyproject.toml 2>/dev/null; then
    pwd
    return
  fi
  echo ""
}

clone_or_update_repo() {
  local project_root
  project_root=$(find_project_root)
  if [ -n "$project_root" ]; then
    echo "$project_root"
    return
  fi

  ensure_dependencies
  mkdir -p "$INSTALL_BASE"

  if [ -d "$CLONE_DIR/.git" ]; then
    info "Atualizando checkout existente em $CLONE_DIR..." >&2
    git -C "$CLONE_DIR" fetch --tags origin >&2
    git -C "$CLONE_DIR" pull --ff-only origin main >&2
  else
    info "Clonando Arkhe em $CLONE_DIR..." >&2
    git clone "$REPO_URL" "$CLONE_DIR" >&2
  fi

  echo "$CLONE_DIR"
}

generate_token() {
  "$1" -c "import secrets; print(secrets.token_hex(32))"
}

prompt_via_tty() {
  local prompt_text="$1"
  local secret_mode="${2:-false}"
  local reply=""
  if [ ! -r /dev/tty ]; then
    echo ""
    return
  fi
  if [ "$secret_mode" = "true" ]; then
    read -r -s -p "$prompt_text" reply </dev/tty
    echo >&2
  else
    read -r -p "$prompt_text" reply </dev/tty
  fi
  echo "$reply"
}

create_env_file() {
  local py_bin="$1"
  local env_path="$2"
  local project_root="$3"
  local openai_key="${OPENAI_API_KEY:-${ARKHE_OPENAI_API_KEY:-}}"

  if [ -f "$env_path" ]; then
    warn ".env já existe em $env_path. Mantendo o arquivo atual."
    return
  fi

  if [ -z "$openai_key" ]; then
    openai_key=$(prompt_via_tty "OPENAI_API_KEY (opcional agora; Enter para preencher depois): " true)
  fi

  mkdir -p "$(dirname "$env_path")"
  cat > "$env_path" <<EOF
# Arkhe — gerado por install.sh

# --- LLM ---
OPENAI_API_KEY=$openai_key
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
RLM_MODEL=$DEFAULT_MODEL

# --- Servidor ---
RLM_API_HOST=127.0.0.1
RLM_API_PORT=5000
RLM_WS_HOST=127.0.0.1
RLM_WS_PORT=8765

# --- Segurança ---
RLM_WS_TOKEN=$(generate_token "$py_bin")
RLM_INTERNAL_TOKEN=$(generate_token "$py_bin")
RLM_ADMIN_TOKEN=$(generate_token "$py_bin")
RLM_HOOK_TOKEN=$(generate_token "$py_bin")
RLM_API_TOKEN=$(generate_token "$py_bin")
EOF
  ok ".env criado em $env_path"

  if [ -z "$openai_key" ]; then
    warn "Nenhuma chave de provedor foi configurada. Edite $env_path e preencha OPENAI_API_KEY, ANTHROPIC_API_KEY ou GOOGLE_API_KEY."
  else
    ok "Chave inicial inserida no .env"
  fi

  if [ ! -f "$project_root/.env.example" ]; then
    warn "Template .env.example não encontrado; revise manualmente o arquivo gerado."
  fi
}

create_wrapper() {
  local wrapper_path="$1"
  local command_name="$2"
  local project_root="$3"
  mkdir -p "$WRAPPER_DIR"
  cat > "$wrapper_path" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec uv --directory "$project_root" run $command_name "\$@"
EOF
  chmod +x "$wrapper_path"
}

install_wrappers() {
  local project_root="$1"
  create_wrapper "$WRAPPER_DIR/arkhe" arkhe "$project_root"
  create_wrapper "$WRAPPER_DIR/rlm" rlm "$project_root"
  ok "Wrappers criados em $WRAPPER_DIR"

  case ":$PATH:" in
    *":$WRAPPER_DIR:"*) ok "$WRAPPER_DIR já está no PATH" ;;
    *) warn "Adicione $WRAPPER_DIR ao PATH para usar 'arkhe' sem caminho absoluto." ;;
  esac
}

sync_project() {
  local project_root="$1"
  info "Instalando dependências Python com uv..."
  uv --directory "$project_root" sync
  ok "Dependências sincronizadas"
}

main() {
  banner

  local os_name
  os_name=$(detect_os)
  info "Sistema operacional: ${BOLD}${os_name}${RESET}"

  local py_bin
  py_bin=$(check_python)
  if [ -z "$py_bin" ]; then
    err "Python 3.11 ou superior não encontrado. Instale Python 3.11+ e execute novamente."
  fi
  ok "Python: $("$py_bin" --version)"

  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    install_uv
  else
    ok "uv: $(uv --version)"
  fi

  local project_root
  project_root=$(clone_or_update_repo)
  ok "Projeto pronto em: $project_root"

  sync_project "$project_root"
  install_wrappers "$project_root"
  create_env_file "$py_bin" "$project_root/.env" "$project_root"

  local arkhe_cmd="$WRAPPER_DIR/arkhe"
  if "$arkhe_cmd" version >/dev/null 2>&1; then
    ok "CLI validada: $("$arkhe_cmd" version)"
  else
    warn "Não foi possível validar o wrapper automaticamente. Tente: $arkhe_cmd version"
  fi

  echo
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${GREEN}   Quickstart concluído${RESET}"
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
  echo
  echo -e "  Projeto:      ${CYAN}$project_root${RESET}"
  echo -e "  Configuração: ${CYAN}$project_root/.env${RESET}"
  echo -e "  CLI:          ${CYAN}$arkhe_cmd${RESET}"
  echo
  echo -e "  ${CYAN}1.${RESET} Edite ${CYAN}$project_root/.env${RESET} e preencha sua chave do provedor, se ainda estiver vazia"
  echo -e "  ${CYAN}2.${RESET} Inicie com ${CYAN}$arkhe_cmd start --foreground${RESET}"
  echo -e "  ${CYAN}3.${RESET} Rode ${CYAN}$arkhe_cmd doctor${RESET} para validar a instalação"
  echo -e "  ${CYAN}4.${RESET} Se quiser instalação guiada de daemon, execute ${CYAN}$arkhe_cmd setup${RESET}"
  echo
}

main "$@"
