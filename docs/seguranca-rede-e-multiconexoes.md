# Segurança de Rede e Múltiplas Conexões — RLM Engine

> **Versão:** Phase 9.3 (CiberSeg)  
> **Última atualização:** 2026-03-06  
> **Escopo:** VPS dedicada, Homelab, dispositivos IoT (ESP32), veículos, rede WireGuard

---

## Índice

1. [Superfície de ataque atual](#1-superfície-de-ataque-atual)
2. [Arquitetura de rede recomendada](#2-arquitetura-de-rede-recomendada)
3. [WireGuard — espinha dorsal de segurança](#3-wireguard--espinha-dorsal-de-segurança)
4. [Portas, binds e serviços do RLM](#4-portas-binds-e-serviços-do-rlm)
5. [Autenticação por serviço](#5-autenticação-por-serviço)
6. [ESP32 e dispositivos IoT](#6-esp32-e-dispositivos-iot)
7. [Integração com veículo (OBD/CAN)](#7-integração-com-veículo-obdcan)
8. [nginx como proxy reverso TLS](#8-nginx-como-proxy-reverso-tls)
9. [Variáveis de ambiente obrigatórias](#9-variáveis-de-ambiente-obrigatórias)
10. [Rate limiting e proteção DDoS](#10-rate-limiting-e-proteção-ddos)
11. [Firewall (iptables/ufw)](#11-firewall-iptablesufw)
12. [Checklist de deploy seguro](#12-checklist-de-deploy-seguro)
13. [Referência de portas](#13-referência-de-portas)

---

## 1. Superfície de ataque atual

O RLM expõe três serviços de rede. Cada um tem características e riscos distintos.

| Serviço | Arquivo | Bind padrão (Phase 9.3) | Autenticação |
|---|---|---|---|
| HTTP API (FastAPI) | `rlm/server/api.py` | `127.0.0.1:5000` | `RLM_API_TOKEN` (Bearer) |
| WebSocket streaming | `rlm/server/ws_server.py` | `127.0.0.1:8765` | `RLM_WS_TOKEN` (query/Bearer) |
| Webhook externo | `rlm/server/webhook_dispatch.py` | via API (porta 5000) | `RLM_HOOK_TOKEN` (header/Bearer) |
| Telegram Gateway | `rlm/server/telegram_gateway.py` | saída apenas | `TELEGRAM_BOT_TOKEN` |

> **Antes da Phase 9.3**, os serviços HTTP e WebSocket faziam bind em `0.0.0.0`, expondo o RLM para qualquer IP que chegasse à máquina. O WebSocket não tinha autenticação alguma — equivalente à vulnerabilidade **ClawJacked** do OpenClaw que em fevereiro de 2026 expôs 135 mil instâncias.

---

## 2. Arquitetura de rede recomendada

```
╔══════════════════════════════════════════════════════════════════╗
║  INTERNET PÚBLICA                                                ║
║  Qualquer IP pode tentar conectar                                ║
╚══════════════════╦═══════════════════════════════════════════════╝
                   │
         UDP/51820 (WireGuard) ← ÚNICO buraco no firewall
         HTTPS/443 (nginx)     ← opcional, para acesso humano
                   │
╔══════════════════▼═══════════════════════════════════════════════╗
║  VPS / HOMELAB                                                   ║
║                                                                  ║
║  ┌─────────────────────────────────────────────────────────┐    ║
║  │  WireGuard  (interface wg0, 10.0.0.1/24)                │    ║
║  │  ─────────────────────────────────────────────────────  │    ║
║  │  RLM HTTP API    → escuta em 127.0.0.1:5000             │    ║
║  │  RLM WebSocket   → escuta em 127.0.0.1:8765             │    ║
║  │  nginx HTTPS     → escuta em 0.0.0.0:443 (proxy TLS)   │    ║
║  └─────────────────────────────────────────────────────────┘    ║
║                             │                                    ║
║         ┌───────────┬───────┴───────┬────────────┐              ║
║         ▼           ▼               ▼            ▼              ║
║   10.0.0.2      10.0.0.10       10.0.0.11    10.0.0.20          ║
║  Celular/PC    ESP32 #1        ESP32 #2      Carro (RPi)         ║
║  (peer WG)     (peer WG)       (peer WG)     (peer WG)           ║
╚══════════════════════════════════════════════════════════════════╝
```

**Princípio:** o RLM nunca fica exposto diretamente à internet. Todos os dispositivos chegam até ele através do túnel WireGuard. Scanner externo não consegue nem confirmar que existe um serviço na máquina.

---

## 3. WireGuard — espinha dorsal de segurança

### Por que WireGuard e não OpenVPN ou IPSec

| Critério | WireGuard | OpenVPN |
|---|---|---|
| Linhas de código | ~4.000 | ~600.000 |
| Superfície de ataque | Mínima | Grande |
| Criptografia | Curve25519 + ChaCha20 (fixas) | Configurável (erro humano possível) |
| Autenticação | Par de chaves por peer | Certificado + usuário/senha |
| Peer comprometido | Isolado (chave própria) | Pode afetar outros |
| Latência | < 1ms (kernel-space) | ~3-5ms (userspace) |
| UDP identification | Indistinguível de tráfego UDP comum | Identificável como VPN |

### Instalação na VPS (Ubuntu 22.04+)

```bash
# Instalar e habilitar
apt install wireguard
systemctl enable wg-quick@wg0

# Gerar par de chaves do servidor
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
chmod 600 /etc/wireguard/server_private.key
```

### Configuração do servidor `/etc/wireguard/wg0.conf`

```ini
[Interface]
Address    = 10.0.0.1/24
PrivateKey = <conteúdo de server_private.key>
ListenPort = 51820

# Habilitar roteamento (necessário se dispositivos precisam sair para internet via VPN)
PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# ─── Peers ───────────────────────────────────────────────────

[Peer]
# Celular / PC principal
PublicKey  = <celular_public_key>
AllowedIPs = 10.0.0.2/32

[Peer]
# ESP32 #1 — sensor de temperatura
PublicKey  = <esp32_1_public_key>
AllowedIPs = 10.0.0.10/32
# PersistentKeepalive mantém o túnel ativo em NAT (necessário para ESP32)
PersistentKeepalive = 25

[Peer]
# ESP32 #2 — controle de acesso
PublicKey  = <esp32_2_public_key>
AllowedIPs = 10.0.0.11/32
PersistentKeepalive = 25

[Peer]
# Raspberry Pi no carro
PublicKey  = <rpi_carro_public_key>
AllowedIPs = 10.0.0.20/32
PersistentKeepalive = 30
```

### Gerar par de chaves para cada peer

Execute **no dispositivo cliente** (ou use o app WireGuard no celular):

```bash
# Gerar
wg genkey | tee peer_private.key | wg pubkey > peer_public.key

# peer_public.key → copie para o servidor (campo PublicKey acima)
# peer_private.key → fica APENAS no dispositivo, nunca sai
```

### Ativar e testar

```bash
# Na VPS
wg-quick up wg0
wg show              # lista peers e tráfego

# Teste do celular (após configurar o app WireGuard)
ping 10.0.0.1        # deve responder
curl http://10.0.0.1:5000/health  # RLM deve responder
```

---

## 4. Portas, binds e serviços do RLM

### Como iniciar o RLM na VPS (com WireGuard ativo)

```python
# Opção A: bind no loopback (acesso apenas via nginx local)
from rlm.server.api import start_server
start_server(host="127.0.0.1", port=5000)

# Opção B: bind na interface WireGuard (VPN peers acessam diretamente)
start_server(host="10.0.0.1", port=5000)

# NUNCA em produção:
# start_server(host="0.0.0.0", port=5000)  ← expõe para internet pública
```

```python
# WebSocket — mesmo princípio
from rlm.server.ws_server import start_ws_server, RLMEventBus
bus = RLMEventBus()
start_ws_server(bus, host="10.0.0.1", port=8765)
# ou loopback + nginx WebSocket proxy
start_ws_server(bus, host="127.0.0.1", port=8765)
```

### Com systemd (deploy permanente)

`/etc/systemd/system/rlm.service`:

```ini
[Unit]
Description=RLM Engine
After=network.target wg-quick@wg0.service
Requires=wg-quick@wg0.service

[Service]
Type=simple
User=rlm
WorkingDirectory=/opt/rlm
EnvironmentFile=/etc/rlm/environment
ExecStart=/opt/rlm/.venv/bin/python -m rlm.server.api
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/rlm/environment` (chmod 600, dono root):

```bash
RLM_API_TOKEN=<token_64_hex>
RLM_HOOK_TOKEN=<token_64_hex>
RLM_WS_TOKEN=<token_64_hex>
OPENAI_API_KEY=<chave>
TELEGRAM_BOT_TOKEN=<token_bot>
```

---

## 5. Autenticação por serviço

### 5.1 HTTP API — `RLM_API_TOKEN`

Implementado em `rlm/server/openai_compat.py`. Token verificado em tempo constante (sem timing oracle via `hmac.compare_digest`).

```bash
# Gerar token
python -c "import secrets; print(secrets.token_hex(32))"

# Usar nas requisições
curl -H "Authorization: Bearer SEU_TOKEN" \
     http://10.0.0.1:5000/v1/chat/completions \
     -d '{"model":"gpt-4o","messages":[{"role":"user","content":"olá"}]}'
```

Se `RLM_API_TOKEN` não estiver configurado, o endpoint OpenAI-compat fica sem autenticação — **nunca faça isso em produção**.

### 5.2 WebSocket — `RLM_WS_TOKEN`

Implementado em `rlm/server/ws_server.py`. O handshake HTTP é interceptado por `process_request` — a conexão WebSocket é recusada com HTTP 401 **antes** de completar se o token for inválido. Nenhum dado do barramento interno chega ao cliente não autorizado.

```bash
# Gerar token
export RLM_WS_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# Conectar (query param — mais simples para dashboards)
wscat -c "ws://10.0.0.1:8765?token=SEU_TOKEN"

# Conectar (header Bearer — mais seguro)
wscat -c "ws://10.0.0.1:8765" \
      --header "Authorization: Bearer SEU_TOKEN"
```

Se `RLM_WS_TOKEN` não estiver configurado, o servidor loga `⚠️ Auth DISABLED` na inicialização e aceita qualquer conexão. Usar apenas em loopback isolado.

### 5.3 Webhook externo — `RLM_HOOK_TOKEN`

Implementado em `rlm/server/webhook_dispatch.py`.

**Ordem de preferência de envio do token (mais seguro → menos seguro):**

1. `Authorization: Bearer TOKEN` — não aparece em nenhum log
2. `X-Hook-Token: TOKEN` — aparece apenas em logs de debug do nginx
3. Token no path `/api/hooks/TOKEN` — **deprecado**, aparece em access.log

```bash
# Gerar token
export RLM_HOOK_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# Forma recomendada (Bearer)
curl -X POST http://10.0.0.1:5000/api/hooks/ \
     -H "Authorization: Bearer $RLM_HOOK_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text": "temperatura do sensor 1 é 38.5°C", "channel": "esp32"}'

# Forma legada (ainda funciona, mas loga aviso)
curl -X POST http://10.0.0.1:5000/api/hooks/$RLM_HOOK_TOKEN \
     -H "Content-Type: application/json" \
     -d '{"text": "mensagem"}'
```

---

## 6. ESP32 e dispositivos IoT

### Por que ESP32 + WireGuard é a combinação correta

- Cada ESP32 tem seu próprio par de chaves WireGuard → um dispositivo comprometido não dá acesso aos outros
- Comunicação via HTTP simples (não WebSocket) → menor uso de memória RAM
- Token no header (não no path) → não vaza em logs

### Biblioteca WireGuard para ESP32

```
https://github.com/ciniml/WireGuard-ESP32
```

```cpp
// platformio.ini
[env:esp32]
platform = espressif32
board = esp32dev
framework = arduino
lib_deps =
    ciniml/WireGuard-ESP32 @ ^0.2.0
    bblanchon/ArduinoJson @ ^7.0.0
```

### Código completo ESP32 → RLM

```cpp
#include <WiFi.h>
#include <WireGuard-ESP32.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ─── WireGuard config ────────────────────────────────────────────
const char* WG_PRIVATE_KEY  = "BASE64_PRIVATE_KEY_DO_ESP32";
const char* WG_PUBLIC_KEY   = "BASE64_PUBLIC_KEY_DO_SERVIDOR";  // servidor WG
const char* WG_ENDPOINT     = "IP_PUBLICO_DA_VPS";
const int   WG_PORT         = 51820;
const char* WG_LOCAL_IP     = "10.0.0.10";   // IP atribuído ao ESP32
const char* WG_DNS          = "1.1.1.1";

// ─── RLM config ──────────────────────────────────────────────────
const char* RLM_HOST        = "http://10.0.0.1:5000";
const char* RLM_HOOK_TOKEN  = "SEU_HOOK_TOKEN_AQUI";
const char* DEVICE_ID       = "esp32_sensor_01";  // client_id de sessão no RLM

static WireGuard wg;

void setup() {
    Serial.begin(115200);
    WiFi.begin("SSID", "SENHA_WIFI");
    while (WiFi.status() != WL_CONNECTED) delay(500);

    // Inicializar WireGuard
    IPAddress localIP, dns;
    localIP.fromString(WG_LOCAL_IP);
    dns.fromString(WG_DNS);

    wg.begin(localIP, WG_PRIVATE_KEY, WG_ENDPOINT, WG_PUBLIC_KEY, WG_PORT);
    delay(2000);  // aguardar handshake
    Serial.println("WireGuard conectado: " + String(WG_LOCAL_IP));
}

/**
 * Envia evento para o RLM via webhook.
 * Usa Bearer token no header — não no path (evita vazamento em logs).
 */
bool sendToRLM(String message, String channel = "esp32") {
    if (WiFi.status() != WL_CONNECTED) return false;

    HTTPClient http;
    String url = String(RLM_HOST) + "/api/hooks/";  // token NO HEADER, não no path
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Authorization", "Bearer " + String(RLM_HOOK_TOKEN));

    // Montar payload
    JsonDocument doc;
    doc["text"]      = message;
    doc["channel"]   = channel;
    doc["client_id"] = DEVICE_ID;

    String body;
    serializeJson(doc, body);

    int code = http.POST(body);
    bool ok = (code == 200 || code == 202);

    if (!ok) {
        Serial.printf("[RLM] Erro HTTP %d\n", code);
    }

    http.end();
    return ok;
}

void loop() {
    // Exemplo: leitura de sensor
    float temperatura = analogRead(34) * 0.1;  // substitua pela leitura real

    if (temperatura > 40.0) {
        String msg = "ALERTA: temperatura " + String(temperatura, 1) + "°C";
        sendToRLM(msg, "sensor_temperatura");
    }

    delay(30000);  // a cada 30 segundos
}
```

### Segmentação de IPs para IoT

Atribua faixas de IP por função no `wg0.conf`:

| Faixa | Uso |
|---|---|
| `10.0.0.1` | VPS/RLM |
| `10.0.0.2–9` | Dispositivos pessoais (celular, PC, laptop) |
| `10.0.0.10–49` | ESP32 e microcontroladores |
| `10.0.0.50–99` | Raspberry Pi e SBCs |
| `10.0.0.100+` | Dispositivos veiculares |

Isso permite criar regras de firewall diferenciadas por faixa (ex: IoT não acessa WebSocket de observabilidade).

---

## 7. Integração com veículo (OBD/CAN)

### Arquitetura recomendada

```
┌─────────────────────────────────────────────────────┐
│  VEÍCULO                                            │
│                                                     │
│  OBD-II port ──► Raspberry Pi 4                     │
│  (CAN bus)       ├── python-obd (leitura OBD)       │
│                  ├── WireGuard peer (10.0.0.20)     │
│                  ├── 4G modem USB (ou hotspot cel)  │
│                  └── RLM webhook client             │
└─────────────────────────────────────────────────────┘
         │ WireGuard tunnel (UDP/51820)
         ▼
    VPS 10.0.0.1 → RLM processa dados OBD
```

### Código Python para RPi no carro

```python
# car_agent.py — roda no Raspberry Pi
import obd
import requests
import time

RLM_URL   = "http://10.0.0.1:5000/api/hooks/"
RLM_TOKEN = "SEU_HOOK_TOKEN"
CLIENT_ID = "veiculo_principal"

HEADERS = {
    "Authorization": f"Bearer {RLM_TOKEN}",
    "Content-Type": "application/json",
}

def send_to_rlm(message: str, metadata: dict = None):
    payload = {
        "text": message,
        "client_id": CLIENT_ID,
        "channel": "obd_vehicle",
        "metadata": metadata or {},
    }
    try:
        r = requests.post(RLM_URL, json=payload, headers=HEADERS, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[RLM] falha: {e}")
        return False

def monitor_vehicle():
    connection = obd.OBD()  # conecta na primeira porta OBD disponível

    while True:
        rpm   = connection.query(obd.commands.RPM).value
        speed = connection.query(obd.commands.SPEED).value
        temp  = connection.query(obd.commands.COOLANT_TEMP).value
        dtcs  = connection.query(obd.commands.GET_DTC).value  # códigos de erro

        if dtcs:
            send_to_rlm(
                f"Códigos de erro OBD detectados: {dtcs}",
                metadata={"rpm": str(rpm), "speed": str(speed)}
            )

        if temp and temp.magnitude > 100:  # superaquecimento
            send_to_rlm(
                f"ALERTA: temperatura do motor {temp.magnitude}°C",
            )

        time.sleep(60)

if __name__ == "__main__":
    monitor_vehicle()
```

---

## 8. nginx como proxy reverso TLS

O RLM não serve HTTPS diretamente. O nginx recebe a conexão pública e encaminha para o processo Python local — isso é necessário quando humanos precisam acessar via browser.

### Configuração `/etc/nginx/sites-available/rlm`

```nginx
# HTTP → HTTPS redirect
server {
    listen 80;
    server_name rlm.seudominio.com;
    return 301 https://$host$request_uri;
}

# HTTPS + proxy para RLM
server {
    listen 443 ssl;
    server_name rlm.seudominio.com;

    ssl_certificate     /etc/letsencrypt/live/rlm.seudominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/rlm.seudominio.com/privkey.pem;

    # TLS moderno apenas
    ssl_protocols        TLSv1.2 TLSv1.3;
    ssl_ciphers          ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;

    # Security headers
    add_header X-Frame-Options           DENY;
    add_header X-Content-Type-Options    nosniff;
    add_header Referrer-Policy           no-referrer;
    add_header Strict-Transport-Security "max-age=63072000" always;

    # Token NO LOG (evita registrar token do webhook em access.log)
    # Rota legada com token na URL — ocultar da variável $request usada no log
    log_format rlm_safe '$remote_addr - [$time_local] '
                        '"$request_method $uri" $status';
    access_log /var/log/nginx/rlm_access.log rlm_safe;

    # ─── HTTP API ─────────────────────────────────────────────
    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;  # RLM pode demorar em tarefas longas
    }

    # ─── WebSocket de observabilidade ─────────────────────────
    location /ws {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       $host;
        proxy_read_timeout 3600s;  # WebSocket fica aberto por horas
    }
}
```

```bash
# Certificado TLS grátis (Let's Encrypt)
apt install certbot python3-certbot-nginx
certbot --nginx -d rlm.seudominio.com

# Habilitar site
ln -s /etc/nginx/sites-available/rlm /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## 9. Variáveis de ambiente obrigatórias

Arquivo `/etc/rlm/environment` — permissões `chmod 600`, proprietário `root:root`.

```bash
# ─── Tokens de autenticação ────────────────────────────────────────────────
# Gerar com: python -c "import secrets; print(secrets.token_hex(32))"
# 32 bytes hex = 256 bits = seguro contra força bruta por bilhões de anos

RLM_API_TOKEN=<64_chars_hex>       # HTTP API / OpenAI-compat
RLM_HOOK_TOKEN=<64_chars_hex>      # Receptor de webhooks externos
RLM_WS_TOKEN=<64_chars_hex>        # WebSocket de observabilidade

# ─── LLM ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...
RLM_MODEL=gpt-4o-mini              # ou gpt-4o, claude-3-5-sonnet-20241022

# ─── Telegram (opcional) ───────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_CHAT_IDS=123456789  # IDs de chat autorizados (vírgula)

# ─── Rede ──────────────────────────────────────────────────────────────────
RLM_BIND_HOST=10.0.0.1             # interface WireGuard; use 127.0.0.1 + nginx
RLM_BIND_PORT=5000
RLM_WS_HOST=10.0.0.1
RLM_WS_PORT=8765
```

> **NUNCA** comite este arquivo no Git. Adicione ao `.gitignore`:
> ```
> /etc/rlm/environment
> *.env
> .env*
> ```

---

## 10. Rate limiting e proteção DDoS

### Rate limit de webhook (já implementado)

`rlm/server/webhook_dispatch.py` usa sliding window por IP.

```python
# Padrão: 60 req/min por IP
# Override via env:
RLM_HOOK_RATE_LIMIT=0   # desabilitar (apenas em dep. interno isolado)
RLM_HOOK_RATE_LIMIT=10  # mais restritivo
```

Resposta quando excedido:
```json
HTTP 429 Too Many Requests
Retry-After: 45
{"error": "rate_limited", "retry_after": 45}
```

### Rate limit adicional no nginx (camada externa)

```nginx
# No nginx.conf (http block)
limit_req_zone $binary_remote_addr zone=rlm_api:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=rlm_ws:1m  rate=5r/m;

# No server block
location /api/ {
    limit_req zone=rlm_api burst=10 nodelay;
    proxy_pass http://127.0.0.1:5000;
}
```

### fail2ban para bloquear tentativas de força bruta

`/etc/fail2ban/filter.d/rlm.conf`:
```ini
[Definition]
failregex = .*\[webhook\] Invalid token from ip=<HOST>
            .*401.*Unauthorized.*
ignoreregex =
```

`/etc/fail2ban/jail.d/rlm.conf`:
```ini
[rlm-webhook]
enabled  = true
filter   = rlm
logpath  = /var/log/rlm/rlm.log
maxretry = 5
findtime = 300
bantime  = 3600
```

---

## 11. Firewall (iptables/ufw)

### Usando ufw (mais simples)

```bash
# Resetar e definir política padrão
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# SSH (porta padrão ou customizada)
ufw allow 22/tcp      # ou: ufw allow SEU_PORT_SSH/tcp

# WireGuard — único buraco para dispositivos IoT
ufw allow 51820/udp

# HTTPS — apenas se usuários humanos precisam acessar via browser
ufw allow 443/tcp

# HTTP redirect → HTTPS
ufw allow 80/tcp

# NÃO expor as portas do RLM diretamente:
# ufw allow 5000  ← NÃO FAZER
# ufw allow 8765  ← NÃO FAZER

ufw enable
ufw status verbose
```

### Isolamento de IoT por faixa de IP (iptables avançado)

```bash
# ESP32 (10.0.0.10-49): só pode acessar webhook HTTP, não WebSocket nem admin
iptables -A FORWARD -s 10.0.0.10/28 -d 10.0.0.1 -p tcp --dport 5000 -j ACCEPT
iptables -A FORWARD -s 10.0.0.10/28 -d 10.0.0.1 -p tcp --dport 8765 -j DROP

# Carro (10.0.0.20): acesso completo
iptables -A FORWARD -s 10.0.0.20/32 -d 10.0.0.1 -j ACCEPT

# Dispositivos pessoais (10.0.0.2-9): acesso completo
iptables -A FORWARD -s 10.0.0.2/30 -d 10.0.0.1 -j ACCEPT

# Salvar regras
iptables-save > /etc/iptables/rules.v4
```

---

## 12. Checklist de deploy seguro

### Antes de ligar o RLM pela primeira vez

- [ ] WireGuard instalado e `wg0` ativo (`wg show` mostra interface)
- [ ] Todos os peers-chave gerados e adicionados ao `wg0.conf`
- [ ] `RLM_API_TOKEN` gerado (64 hex) e no arquivo de environment
- [ ] `RLM_HOOK_TOKEN` gerado (64 hex) e no arquivo de environment
- [ ] `RLM_WS_TOKEN` gerado (64 hex) e no arquivo de environment
- [ ] `/etc/rlm/environment` com `chmod 600`
- [ ] RLM configurado para bind em `127.0.0.1` ou `10.0.0.1` (não `0.0.0.0`)
- [ ] nginx instalado com certificado TLS válido
- [ ] ufw habilitado: somente 22/tcp, 51820/udp, 443/tcp abertas
- [ ] fail2ban configurado para o log do RLM

### Periodicamente

- [ ] `wg show` — verificar se todos os peers estão handshaking
- [ ] `journalctl -u rlm` — verificar logs de tentativas inválidas de token
- [ ] Rotacionar tokens a cada 90 dias ou após qualquer suspeita de vazamento
- [ ] Atualizar WireGuard: `apt upgrade wireguard`
- [ ] Verificar se `openssl s_client -connect rlm.seudominio.com:443` retorna TLS 1.3

### Após adicionar novo dispositivo IoT

- [ ] Gerar par de chaves **no dispositivo**, não no servidor
- [ ] Adicionar apenas a chave pública ao `wg0.conf`
- [ ] Atribuir IP fixo único na faixa designada
- [ ] Confirmar conectividade: `ping 10.0.0.1` do dispositivo
- [ ] Configurar `PersistentKeepalive = 25` em dispositivos atrás de NAT (ESP32, celular 4G)

---

## 13. Referência de portas

| Porta | Protocolo | Serviço | Exposto para | Obs |
|---|---|---|---|---|
| 22 | TCP | SSH | Internet (ufw allow) | Mudar para porta não padrão se possível |
| 51820 | UDP | WireGuard | Internet (ufw allow) | Único ponto de entrada para IoT |
| 80 | TCP | nginx HTTP | Internet | Apenas redirect para 443 |
| 443 | TCP | nginx HTTPS | Internet | TLS termination, proxy para 5000 |
| 5000 | TCP | RLM HTTP API | Loopback / wg0 | Nunca expor diretamente |
| 8765 | TCP | RLM WebSocket | Loopback / wg0 | Nunca expor diretamente |
| 5432 | TCP | PostgreSQL (futuro) | Loopback | Se/quando migrar de SQLite |

---

## Fluxo completo de uma requisição segura

```
ESP32 (10.0.0.10)
  │  1. Lê sensor de temperatura: 42.3°C
  │  2. Monta JSON payload
  │  3. POST http://10.0.0.1:5000/api/hooks/
  │     Header: Authorization: Bearer [RLM_HOOK_TOKEN]
  │
  ▼ [WireGuard tunnel — Curve25519 + ChaCha20]
  │
VPS wg0 (10.0.0.1)
  │  4. Pacote decriptado pelo kernel WireGuard
  │  5. TCP 5000 → processo RLM (bind 10.0.0.1:5000)
  │
  ▼
rlm/server/webhook_dispatch.py
  │  6. Rate limit check: IP 10.0.0.10, 1/60 req/min → OK
  │  7. Token extraction: Authorization: Bearer → token extraído
  │  8. hmac.compare_digest(expected, received) → OK
  │  9. audit_input(text) → scan injeção → clean (temperatura legítima)
  │  10. supervisor.execute(session, "ALERTA: temperatura 42.3°C")
  │
  ▼
rlm/core/rlm.py (recursão)
  │  11. LLM processa
  │  12. Decide: enviar alerta via Telegram
  │  13. SIF tool: telegram_send("Atenção: temperatura elevada no sensor 1")
  │
  ▼
Telegram Bot → seu celular recebe notificação
```

Cada step tem uma barreira: criptografia de rede (WireGuard) → autenticação (token) → sanitização de conteúdo (audit_input) → sandbox de execução (REPLAuditor). Um atacante precisa comprometer **todas** as camadas simultaneamente.
