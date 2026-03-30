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
#   6. Cria wrappers `arkhe` e `rlm` em ~/.local/bin                            #
#   7. Abre `arkhe setup` em shells interativos                                 #
#   8. Cai em `.env` seguro como fallback não interativo                        #
#                                                                               #
# Variáveis opcionais:                                                          #
#   ARKHE_REPO_URL, ARKHE_INSTALL_DIR, ARKHE_BIN_DIR                            #
#   ARKHE_PROVIDER=openai|anthropic|google|custom                               #
#   ARKHE_BACKEND=openai|anthropic|google|portkey|litellm                       #
#   ARKHE_OPENAI_API_KEY, ARKHE_ANTHROPIC_API_KEY, ARKHE_GOOGLE_API_KEY         #
#   ARKHE_MODEL, ARKHE_MODEL_PLANNER, ARKHE_MODEL_WORKER                        #
#   ARKHE_MODEL_EVALUATOR, ARKHE_MODEL_FAST, ARKHE_MODEL_MINIREPL               #
#   ARKHE_SKIP_WIZARD=1 para pular o menu interativo                            #
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
DEFAULT_PROVIDER_RAW="${ARKHE_PROVIDER:-openai}"
DEFAULT_BACKEND_RAW="${ARKHE_BACKEND:-}"

BOOTSTRAP_PROVIDER=""
BOOTSTRAP_BACKEND=""
BOOTSTRAP_MODEL=""
BOOTSTRAP_MODEL_PLANNER=""
BOOTSTRAP_MODEL_WORKER=""
BOOTSTRAP_MODEL_EVALUATOR=""
BOOTSTRAP_MODEL_FAST=""
BOOTSTRAP_MODEL_MINIREPL=""
BOOTSTRAP_OPENAI_KEY=""
BOOTSTRAP_ANTHROPIC_KEY=""
BOOTSTRAP_GOOGLE_KEY=""
BOOTSTRAP_ENV_CREATED=0

ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗ Erro:${RESET} $*" >&2; exit 1; }
info() { echo -e "${CYAN}→${RESET}  $*"; }

normalize_provider() {
  local provider_raw="${1:-openai}"
  case "$provider_raw" in
    openai|anthropic|google|custom)
      echo "$provider_raw"
      ;;
    *)
      warn "ARKHE_PROVIDER=$provider_raw não é reconhecido. Usando openai no bootstrap."
      echo "openai"
      ;;
  esac
}

resolve_backend() {
  local backend_raw="${1:-}"
  local provider="$2"
  if [ -n "$backend_raw" ]; then
    echo "$backend_raw"
    return
  fi
  if [ "$provider" = "custom" ]; then
    echo "openai"
    return
  fi
  echo "$provider"
}

set_bootstrap_model_defaults() {
  local provider="$1"
  local default_model=""
  local default_planner=""
  local default_worker=""
  local default_evaluator=""
  local default_fast=""
  local default_minirepl=""

  case "$provider" in
    anthropic)
      default_model="claude-3-5-haiku-latest"
      default_planner="claude-sonnet-4-20250514"
      default_worker="claude-3-5-haiku-latest"
      default_evaluator="claude-3-5-haiku-latest"
      default_fast="claude-3-5-haiku-latest"
      default_minirepl="claude-3-5-haiku-latest"
      ;;
    google)
      default_model="gemini-2.5-flash"
      default_planner="gemini-2.5-pro"
      default_worker="gemini-2.5-flash"
      default_evaluator="gemini-2.5-flash"
      default_fast="gemini-2.5-flash"
      default_minirepl="gemini-2.5-flash"
      ;;
    custom)
      default_model="gpt-5.4-mini"
      default_planner="$default_model"
      default_worker="$default_model"
      default_evaluator="$default_model"
      default_fast="$default_model"
      default_minirepl="$default_model"
      ;;
    *)
      default_model="gpt-5.4-mini"
      default_planner="gpt-5.4"
      default_worker="gpt-5.4-mini"
      default_evaluator="gpt-5.4-mini"
      default_fast="gpt-5.4-nano"
      default_minirepl="gpt-5-nano"
      ;;
  esac

  BOOTSTRAP_MODEL="${ARKHE_MODEL:-$default_model}"
  BOOTSTRAP_MODEL_PLANNER="${ARKHE_MODEL_PLANNER:-$default_planner}"
  BOOTSTRAP_MODEL_WORKER="${ARKHE_MODEL_WORKER:-$default_worker}"
  BOOTSTRAP_MODEL_EVALUATOR="${ARKHE_MODEL_EVALUATOR:-$default_evaluator}"
  BOOTSTRAP_MODEL_FAST="${ARKHE_MODEL_FAST:-$default_fast}"
  BOOTSTRAP_MODEL_MINIREPL="${ARKHE_MODEL_MINIREPL:-$default_minirepl}"

  if [ "$provider" = "custom" ]; then
    BOOTSTRAP_MODEL_PLANNER="${ARKHE_MODEL_PLANNER:-$BOOTSTRAP_MODEL}"
    BOOTSTRAP_MODEL_WORKER="${ARKHE_MODEL_WORKER:-$BOOTSTRAP_MODEL}"
    BOOTSTRAP_MODEL_EVALUATOR="${ARKHE_MODEL_EVALUATOR:-$BOOTSTRAP_MODEL}"
    BOOTSTRAP_MODEL_FAST="${ARKHE_MODEL_FAST:-$BOOTSTRAP_MODEL}"
    BOOTSTRAP_MODEL_MINIREPL="${ARKHE_MODEL_MINIREPL:-$BOOTSTRAP_MODEL}"
  fi
}

set_bootstrap_provider_keys() {
  BOOTSTRAP_OPENAI_KEY="${OPENAI_API_KEY:-${ARKHE_OPENAI_API_KEY:-}}"
  BOOTSTRAP_ANTHROPIC_KEY="${ANTHROPIC_API_KEY:-${ARKHE_ANTHROPIC_API_KEY:-}}"
  BOOTSTRAP_GOOGLE_KEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-${ARKHE_GOOGLE_API_KEY:-${ARKHE_GEMINI_API_KEY:-}}}}"
}

prompt_primary_provider_key() {
  local provider="$1"
  case "$provider" in
    anthropic)
      if [ -z "$BOOTSTRAP_ANTHROPIC_KEY" ]; then
        BOOTSTRAP_ANTHROPIC_KEY=$(prompt_via_tty "ANTHROPIC_API_KEY (opcional agora; Enter para preencher depois): " true)
      fi
      ;;
    google)
      if [ -z "$BOOTSTRAP_GOOGLE_KEY" ]; then
        BOOTSTRAP_GOOGLE_KEY=$(prompt_via_tty "GOOGLE_API_KEY (opcional agora; Enter para preencher depois): " true)
      fi
      ;;
    *)
      if [ -z "$BOOTSTRAP_OPENAI_KEY" ]; then
        BOOTSTRAP_OPENAI_KEY=$(prompt_via_tty "OPENAI_API_KEY (opcional agora; Enter para preencher depois): " true)
      fi
      ;;
  esac
}

env_has_provider_key() {
  local env_path="$1"
  awk -F= '/^(OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY)=/ { if (length($2) > 0) found=1 } END { exit found ? 0 : 1 }' "$env_path"
}

warn_if_legacy_env() {
  local env_path="$1"
  local missing_keys=()
  local key
  for key in RLM_MODEL_PLANNER RLM_MODEL_WORKER RLM_MODEL_EVALUATOR RLM_MODEL_FAST RLM_MODEL_MINIREPL; do
    if ! grep -q "^${key}=" "$env_path" 2>/dev/null; then
      missing_keys+=("$key")
    fi
  done

  if [ "${#missing_keys[@]}" -gt 0 ]; then
    warn "O .env existente parece legado e não tem split completo de modelos (${missing_keys[*]}). Rode 'uv run arkhe setup' e escolha split recomendado ou manual."
  fi
}

detect_installed_daemon() {
  local project_root="$1"
  local systemd_unit="$HOME/.config/systemd/user/rlm.service"
  local launchd_plist="$HOME/Library/LaunchAgents/com.rlm.server.plist"

  if [ -f "$systemd_unit" ] && grep -Fq "WorkingDirectory=$project_root" "$systemd_unit" 2>/dev/null; then
    echo "systemd"
    return
  fi

  if [ -f "$launchd_plist" ] && grep -Fq "<string>$project_root</string>" "$launchd_plist" 2>/dev/null; then
    echo "launchd"
    return
  fi

  echo ""
}

daemon_status_hint() {
  case "$1" in
    systemd) echo "systemctl --user status rlm" ;;
    launchd) echo "launchctl list com.rlm.server" ;;
    *) echo "" ;;
  esac
}

daemon_restart_hint() {
  case "$1" in
    systemd) echo "systemctl --user restart rlm" ;;
    launchd) echo "launchctl kickstart -k gui/\$(id -u)/com.rlm.server" ;;
    *) echo "" ;;
  esac
}

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

has_tty() {
  [ -r /dev/tty ] && [ -w /dev/tty ]
}

create_env_file() {
  local py_bin="$1"
  local env_path="$2"
  local project_root="$3"

  if [ -f "$env_path" ]; then
    warn ".env já existe em $env_path. Mantendo o arquivo atual."
    warn_if_legacy_env "$env_path"
    return
  fi

  prompt_primary_provider_key "$BOOTSTRAP_PROVIDER"

  mkdir -p "$(dirname "$env_path")"
  cat > "$env_path" <<EOF
# Arkhe — gerado por install.sh

# --- LLM ---
OPENAI_API_KEY=$BOOTSTRAP_OPENAI_KEY
ANTHROPIC_API_KEY=$BOOTSTRAP_ANTHROPIC_KEY
GOOGLE_API_KEY=$BOOTSTRAP_GOOGLE_KEY
RLM_BACKEND=$BOOTSTRAP_BACKEND
RLM_MODEL=$BOOTSTRAP_MODEL
RLM_MODEL_PLANNER=$BOOTSTRAP_MODEL_PLANNER
RLM_MODEL_WORKER=$BOOTSTRAP_MODEL_WORKER
RLM_MODEL_EVALUATOR=$BOOTSTRAP_MODEL_EVALUATOR
RLM_MODEL_FAST=$BOOTSTRAP_MODEL_FAST
RLM_MODEL_MINIREPL=$BOOTSTRAP_MODEL_MINIREPL

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
  BOOTSTRAP_ENV_CREATED=1
  ok ".env criado em $env_path"

  if ! env_has_provider_key "$env_path"; then
    warn "Nenhuma chave de provedor foi configurada. Edite $env_path ou rode 'uv run arkhe setup' antes de iniciar o runtime."
  else
    ok "Bootstrap gerado com provider=$BOOTSTRAP_PROVIDER e split recomendado de modelos"
  fi

  if [ ! -f "$project_root/.env.example" ]; then
    warn "Template .env.example não encontrado; revise manualmente o arquivo gerado."
  fi
}

run_setup_wizard() {
  local project_root="$1"
  if [ "${ARKHE_SKIP_WIZARD:-0}" = "1" ]; then
    warn "Wizard interativo pulado porque ARKHE_SKIP_WIZARD=1."
    return 1
  fi
  if ! has_tty; then
    warn "Sem TTY interativo; mantendo apenas o .env bootstrap."
    return 1
  fi

  info "Abrindo arkhe setup para revisar chaves, modelo e daemon..."
  if (cd "$project_root" && uv run arkhe setup </dev/tty >/dev/tty 2>/dev/tty); then
    ok "Wizard interativo concluído"
    return 0
  fi

  warn "arkhe setup falhou; mantendo o .env bootstrap em $project_root/.env"
  return 1
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

  BOOTSTRAP_PROVIDER=$(normalize_provider "$DEFAULT_PROVIDER_RAW")
  BOOTSTRAP_BACKEND=$(resolve_backend "$DEFAULT_BACKEND_RAW" "$BOOTSTRAP_PROVIDER")
  set_bootstrap_model_defaults "$BOOTSTRAP_PROVIDER"
  set_bootstrap_provider_keys

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
  local wizard_completed=0
  if run_setup_wizard "$project_root"; then
    wizard_completed=1
  fi

  local arkhe_cmd="$WRAPPER_DIR/arkhe"
  if "$arkhe_cmd" version >/dev/null 2>&1; then
    ok "CLI validada: $("$arkhe_cmd" version)"
  else
    warn "Não foi possível validar o wrapper automaticamente. Tente: $arkhe_cmd version"
  fi

  local daemon_manager
  daemon_manager=$(detect_installed_daemon "$project_root")
  local daemon_status_cmd=""
  local daemon_restart_cmd=""
  if [ -n "$daemon_manager" ]; then
    daemon_status_cmd=$(daemon_status_hint "$daemon_manager")
    daemon_restart_cmd=$(daemon_restart_hint "$daemon_manager")
  fi

  local has_provider_key=0
  if env_has_provider_key "$project_root/.env"; then
    has_provider_key=1
  fi

  echo
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${GREEN}   Quickstart concluído${RESET}"
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
  echo
  echo -e "  Projeto:      ${CYAN}$project_root${RESET}"
  echo -e "  Configuração: ${CYAN}$project_root/.env${RESET}"
  echo -e "  CLI:          ${CYAN}$arkhe_cmd${RESET}"
  if [ "$BOOTSTRAP_ENV_CREATED" -eq 1 ]; then
    echo -e "  Provider:     ${CYAN}$BOOTSTRAP_PROVIDER${RESET}"
  fi
  echo
  if [ "$wizard_completed" -eq 0 ]; then
    echo -e "  ${CYAN}1.${RESET} Se o wizard não abriu, execute ${CYAN}(cd $project_root && uv run arkhe setup)${RESET}"
    echo -e "  ${CYAN}2.${RESET} Não inicie o runtime só com o bootstrap; revise provider, modelos e tokens primeiro"
    echo -e "  ${CYAN}3.${RESET} Se você automatiza instalação, use ${CYAN}ARKHE_SKIP_WIZARD=1${RESET} apenas para preparar o checkout e depois finalize a configuração do .env"
  else
    echo -e "  ${CYAN}1.${RESET} Revise ou ajuste depois com ${CYAN}(cd $project_root && uv run arkhe setup)${RESET}"

    if [ "$has_provider_key" -eq 0 ]; then
      echo -e "  ${CYAN}2.${RESET} Preencha uma chave de provedor no ${CYAN}$project_root/.env${RESET} antes de iniciar o runtime"
      echo -e "  ${CYAN}3.${RESET} Depois disso, revise o setup com ${CYAN}(cd $project_root && uv run arkhe setup)${RESET} ou inicie manualmente conforme seu modo de operação"
    elif [ -n "$daemon_manager" ]; then
      echo -e "  ${CYAN}2.${RESET} O wizard deixou um daemon ${CYAN}$daemon_manager${RESET} instalado; verifique com ${CYAN}$daemon_status_cmd${RESET}"
      echo -e "  ${CYAN}3.${RESET} Se alterar o .env, reaplique com ${CYAN}$daemon_restart_cmd${RESET}"
    else
      echo -e "  ${CYAN}2.${RESET} Inicie manualmente com ${CYAN}$arkhe_cmd start --foreground${RESET}"
      echo -e "  ${CYAN}3.${RESET} Use ${CYAN}$arkhe_cmd status${RESET} para validar o runtime"
    fi
  fi

  echo -e "  ${CYAN}4.${RESET} Rode ${CYAN}$arkhe_cmd doctor${RESET} para validar a instalação"
  echo -e "  ${CYAN}5.${RESET} Use ${CYAN}ARKHE_SKIP_WIZARD=1${RESET} apenas para bootstrap não interativo"
  echo
}

main "$@"
