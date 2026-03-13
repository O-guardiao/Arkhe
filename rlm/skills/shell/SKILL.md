+++
name = "shell"
description = "Controle total do terminal local, VPS e homelab: executa comandos shell, scripts bash, SSH em máquinas remotas, gerencia sessões tmux, controla processos em background, faz deploy, reinicia serviços, monitora logs. Use when: user asks to rodar comando no terminal, acessar VPS, executar script, gerenciar servidor, reiniciar serviço, fazer deploy, monitorar processo, copiar arquivos via rsync/scp, instalar pacotes. PREFERRED over web requests when: tarefa é de administração de sistema."
tags = ["terminal", "shell", "bash", "ssh", "vps", "deploy", "servidor", "serviço", "processo", "tmux", "rsync", "homelab", "infra", "systemctl", "comando", "script", "linux", "servidor remoto", "instalar"]
priority = "contextual"

[sif]
signature = "shell(cmd: str, capture: bool = True, timeout: int = 30) -> subprocess.CompletedProcess"
prompt_hint = "Use para executar comando local ou remoto, script, deploy, diagnóstico, logs ou administração de sistema."
short_sig = "shell(cmd)\u2192CP"
compose = ["notion", "github", "filesystem", "email"]
examples_min = ["executar comando no terminal e capturar logs"]
codex = "lambda cmd,t=30: __import__('subprocess').run((__import__('shlex').split(cmd) if isinstance(cmd,str) else cmd),capture_output=True,text=True,timeout=t)"
impl = """
def shell(cmd, capture=True, timeout=30):
    import subprocess, shlex
    # Se tiver operadores pipe/&&, usa bash -c
    needs_bash = isinstance(cmd, str) and any(x in cmd for x in ['|', '&&', '||', ';', '>',  '<'])
    if needs_bash:
        args = ['bash', '-c', cmd]
    else:
        args = shlex.split(cmd) if isinstance(cmd, str) else cmd
    return subprocess.run(args, capture_output=capture, text=True, timeout=timeout)
"""

[runtime]
estimated_cost = 1.4
risk_level = "high"
side_effects = ["process_spawn", "filesystem_write", "remote_command"]
postconditions = ["command_executed_and_result_available"]
fallback_policy = "ask_user_or_use_filesystem"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "shell terminal bash ssh deploy process logs server script command"
example_queries = ["rode este comando", "veja os logs do serviço no terminal"]

[requires]
bins = []
+++

# Shell Skill — Controle Total do Terminal e Infraestrutura

O RLM executa num REPL Python onde `subprocess`, `os`, `paramiko`, `pexpect` e outras bibliotecas de sistema estão disponíveis. Isso dá controle total sobre terminal local, VPS, homelab e qualquer máquina acessível por SSH.

## Princípio fundamental

OpenClaw usa uma ferramenta `bash` declarativa. O RLM é superior: a lógica de decisão fica dentro do mesmo bloco de código — sem round-trips para o LLM a cada resultado.

```python
# OpenClaw precisa de 3 tool calls separados para fazer isso:
import subprocess

resultado = subprocess.run(
    ["systemctl", "status", "nginx"],
    capture_output=True, text=True
)

if resultado.returncode != 0:
    # Tomar decisão no mesmo bloco — sem ir e voltar ao LLM
    subprocess.run(["systemctl", "start", "nginx"], check=True)
    print("nginx iniciado")
else:
    linhas = resultado.stdout.splitlines()
    print(f"nginx está ativo: {linhas[2] if len(linhas) > 2 else 'ok'}")
```

## 1. Execução de comandos locais

```python
import subprocess, shlex

def shell(cmd: str, cwd: str | None = None, timeout: int = 60) -> dict:
    """
    Executa comando shell e retorna stdout, stderr, returncode.
    Seguro: não usa shell=True, faz split correto.
    """
    resultado = subprocess.run(
        shlex.split(cmd),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )
    return {
        "stdout":      resultado.stdout.strip(),
        "stderr":      resultado.stderr.strip(),
        "returncode":  resultado.returncode,
        "ok":          resultado.returncode == 0,
    }

# Exemplos:
print(shell("ls -la /var/log")["stdout"])
print(shell("df -h")["stdout"])
print(shell("free -h")["stdout"])
print(shell("uname -a")["stdout"])
```

## 2. Script bash multi-linha

```python
import subprocess, textwrap

def bash(script: str, cwd: str | None = None) -> dict:
    """Executa script bash multi-linha."""
    resultado = subprocess.run(
        ["bash", "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return {
        "stdout":     resultado.stdout.strip(),
        "stderr":     resultado.stderr.strip(),
        "returncode": resultado.returncode,
    }

saida = bash("""
    echo "Iniciando deploy..."
    git pull origin main
    pip install -r requirements.txt --quiet
    systemctl restart meu-app
    systemctl status meu-app --no-pager
""", cwd="/opt/meu-app")
print(saida["stdout"])
```

## 3. SSH em VPS / Homelab (via subprocess + chave)

```python
import subprocess, os

def ssh(
    host: str,
    cmd: str,
    user: str = "",
    porta: int = 22,
    chave: str = "~/.ssh/id_rsa",
    timeout: int = 30,
) -> dict:
    """
    Executa comando em máquina remota via SSH sem senhas.
    host: IP ou hostname (ex: "192.168.1.100" ou "meu-vps.com")
    """
    destino = f"{user}@{host}" if user else host
    cmd_ssh = [
        "ssh",
        "-i", os.path.expanduser(chave),
        "-p", str(porta),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        destino,
        cmd,
    ]
    resultado = subprocess.run(
        cmd_ssh, capture_output=True, text=True, timeout=timeout
    )
    return {
        "stdout":     resultado.stdout.strip(),
        "stderr":     resultado.stderr.strip(),
        "returncode": resultado.returncode,
        "ok":         resultado.returncode == 0,
    }

# Exemplos:
print(ssh("192.168.1.100", "df -h", user="root"))
print(ssh("meu-vps.com",   "systemctl status nginx", user="ubuntu"))
print(ssh("homelab",       "docker ps", user="admin", porta=2222))
```

## 4. SSH com paramiko (sessões persistentes, mais robusto)

```python
import paramiko, os

def ssh_parametrizado(
    host: str,
    comandos: list[str],
    user: str = "root",
    chave: str = "~/.ssh/id_rsa",
    porta: int = 22,
) -> list[dict]:
    """
    Executa lista de comandos na mesma conexão SSH (sem reconectar).
    Ideal para sequências: pull → build → restart.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=porta,
        username=user,
        key_filename=os.path.expanduser(chave),
        timeout=10,
    )
    resultados = []
    for cmd in comandos:
        stdin, stdout, stderr = client.exec_command(cmd)
        resultados.append({
            "cmd":    cmd,
            "stdout": stdout.read().decode().strip(),
            "stderr": stderr.read().decode().strip(),
            "status": stdout.channel.recv_exit_status(),
        })
    client.close()
    return resultados

# Deploy completo num VPS:
passos = ssh_parametrizado("meu-vps.com", [
    "cd /opt/api && git pull origin main",
    "pip install -r requirements.txt -q",
    "systemctl restart meu-app",
    "systemctl is-active meu-app",
], user="ubuntu")

for p in passos:
    status = "✅" if p["status"] == 0 else "❌"
    print(f"{status} {p['cmd']}")
    if p["stdout"]: print(f"   {p['stdout']}")
    if p["stderr"] and p["status"] != 0: print(f"   ERR: {p['stderr']}")
```

## 5. Copiar arquivos remotos (SCP / RSYNC)

```python
import subprocess

def scp_upload(local: str, host: str, remoto: str, user: str = "root") -> dict:
    """Upload de arquivo/pasta para VPS via SCP."""
    return shell(f"scp -r {local} {user}@{host}:{remoto}")

def rsync(local: str, host: str, remoto: str, user: str = "root",
          excluir: list[str] = []) -> dict:
    """
    Sincroniza diretório com rsync incremental (mais eficiente que scp).
    Ideal para deploy contínuo.
    """
    exclusoes = "".join(f" --exclude='{e}'" for e in excluir)
    cmd = f"rsync -avz --progress{exclusoes} {local} {user}@{host}:{remoto}"
    return shell(cmd, timeout=300)

# Deploy de pasta local para VPS:
resultado = rsync(
    "./meu-app/",
    "meu-vps.com",
    "/opt/meu-app/",
    user="ubuntu",
    excluir=["__pycache__", ".git", "*.pyc", ".env"],
)
print(resultado["stdout"][-500:])  # últimas linhas do progresso
```

## 6. Controle de tmux (sessões persistentes no servidor)

```python
import subprocess

def tmux_nova_sessao(nome: str, cmd: str | None = None) -> dict:
    """Cria sessão tmux, opcionalmente rodando um comando."""
    c = f"tmux new-session -d -s {nome}"
    if cmd:
        c += f" '{cmd}'"
    return shell(c)

def tmux_envia(sessao: str, texto: str, enter: bool = True) -> dict:
    """Envia teclas/texto para sessão tmux."""
    sufixo = " Enter" if enter else ""
    return shell(f"tmux send-keys -t {sessao} '{texto}'{sufixo}")

def tmux_captura(sessao: str, linhas: int = 50) -> str:
    """Lê saída de sessão tmux."""
    result = shell(f"tmux capture-pane -t {sessao} -p")
    return "\n".join(result["stdout"].splitlines()[-linhas:])

def tmux_lista() -> list[str]:
    """Lista sessões tmux ativas."""
    r = shell("tmux list-sessions -F '#{session_name}'")
    return r["stdout"].splitlines() if r["ok"] else []

# Workflow: iniciar servidor Python em sessão tmux persistente
tmux_nova_sessao("servidor")
tmux_envia("servidor", "cd /opt/api && python -m uvicorn main:app --reload")
import time; time.sleep(3)
saida = tmux_captura("servidor")
print(saida)
```

## 7. Gerenciar processos em background

```python
import subprocess, os, signal, time

def rodar_background(cmd: str, cwd: str | None = None) -> int:
    """Inicia processo em background. Retorna PID."""
    proc = subprocess.Popen(
        ["bash", "-c", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        start_new_session=True,
    )
    return proc.pid

def matar_processo(pid: int, forcado: bool = False) -> bool:
    """Mata processo pelo PID."""
    try:
        sig = signal.SIGKILL if forcado else signal.SIGTERM
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False

def processos_usando_porta(porta: int) -> list[dict]:
    """Lista processos usando uma porta específica."""
    r = shell(f"lsof -i :{porta} -P -n")
    linhas = r["stdout"].splitlines()
    return [{"linha": l} for l in linhas[1:] if l]  # pula header

# Iniciar servidor de desenvolvimento
pid = rodar_background("python -m http.server 8080", cwd="/tmp")
print(f"Servidor rodando com PID {pid}")
time.sleep(2)
procs = processos_usando_porta(8080)
print(f"Porta 8080: {procs}")
```

## 8. Monitoramento de sistema

```python
def status_sistema() -> dict:
    """Coleta métricas do sistema local ou remoto."""
    return {
        "cpu":     shell("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'")["stdout"],
        "ram":     shell("free -h | awk 'NR==2{printf \"%s/%s (%.0f%%)\", $3,$2,$3/$2*100}'")["stdout"],
        "disco":   shell("df -h / | awk 'NR==2{print $5\" usado de \"$2}'")["stdout"],
        "uptime":  shell("uptime -p")["stdout"],
        "load":    shell("cat /proc/loadavg")["stdout"],
        "servicos": [
            {"nome": s, "status": shell(f"systemctl is-active {s}")["stdout"]}
            for s in ["nginx", "docker", "postgresql", "redis"]
        ],
    }

status = status_sistema()
print(f"CPU: {status['cpu']}")
print(f"RAM: {status['ram']}")
print(f"Disco: {status['disco']}")
for s in status["servicos"]:
    icone = "✅" if s["status"] == "active" else "❌"
    print(f"{icone} {s['nome']}: {s['status']}")
```

## 9. Docker remoto

```python
def docker_exec(host: str, user: str, container_cmd: str) -> dict:
    """Executa comando docker em host remoto."""
    return ssh(host, container_cmd, user=user)

# Listar containers no VPS
print(docker_exec("meu-vps.com", "ubuntu", "docker ps --format 'table {{.Names}}\\t{{.Status}}'"))

# Reiniciar container específico
print(docker_exec("meu-vps.com", "ubuntu", "docker restart minha-api"))

# Ver logs
print(docker_exec("meu-vps.com", "ubuntu", "docker logs minha-api --tail 50"))
```

## 10. Deploy completo automatizado

```python
def deploy_completo(
    vps_host: str,
    vps_user: str,
    repo_local: str,
    app_dir: str,
    servico: str,
) -> None:
    """Pipeline de deploy: sync → install → restart → verify."""
    print("📦 Sincronizando código...")
    rsync(repo_local, vps_host, app_dir, user=vps_user,
          excluir=["__pycache__", ".git", ".env", "*.pyc"])

    print("🔧 Instalando dependências e reiniciando...")
    resultados = ssh_parametrizado(vps_host, [
        f"cd {app_dir} && pip install -r requirements.txt -q",
        f"systemctl restart {servico}",
        f"sleep 2 && systemctl is-active {servico}",
        f"journalctl -u {servico} --no-pager -n 20",
    ], user=vps_user)

    for r in resultados:
        ok = "✅" if r["status"] == 0 else "❌"
        print(f"{ok} {r['cmd']}")
        if r["stdout"]: print(f"   {r['stdout'][:200]}")

deploy_completo(
    vps_host="meu-vps.com",
    vps_user="ubuntu",
    repo_local="./minha-api/",
    app_dir="/opt/minha-api/",
    servico="minha-api",
)
```

## Variáveis de ambiente e configuração SSH

```bash
# ~/.ssh/config — recomendado para simplificar conexões
Host vps
    HostName 192.168.1.100
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    Port 22

Host homelab
    HostName 10.0.0.5
    User admin
    IdentityFile ~/.ssh/homelab_key
    Port 2222
```

Com o `~/.ssh/config` configurado, todos os comandos acima funcionam com apenas `host="vps"` ou `host="homelab"`.

## Por que o RLM supera o OpenClaw aqui

OpenClaw tem um `bash` tool onde cada comando é uma chamada separada ao LLM. Se o comando falha, o LLM precisa ser chamado de novo para decidir o próximo passo.

No RLM, um único bloco de código faz:
1. Conecta no VPS
2. Roda 10 comandos sequenciais  
3. Analisa cada saída com Python puro
4. Toma decisões condicionais sem round-trip ao LLM
5. Retorna resultado estruturado

**Latência**: 1 inferência LLM vs N inferências no OpenClaw para N comandos.
