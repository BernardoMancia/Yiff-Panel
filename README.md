# Auto-Yiff

> **Telegram Bot + Web Dashboard** that automatically fetches artwork from [e621.net](https://e621.net) and sends it to a Telegram channel at random intervals between **1 hour and 1 hour 30 minutes**.

---

## Features

- 🦊 Fetches posts from e621.net via official API with fully configurable search tags
- 📤 Sends media to Telegram (photo / video / GIF) with **no caption**
- ⏱ Random interval between 1h and 1h30min per send
- ⚡ Priority ingest queue — send media directly via a Telegram group to jump the queue
- 🔄 Auto-refills queue when empty with balanced media types
- 📊 Real-time web dashboard with countdown, history and queue preview
- 🔴 Server-Sent Events (SSE) for live updates without page refresh
- 🏷️ Tag management via admin panel (mandatory, required, OR, blacklist)
- 👍👎 Reaction-based auto-removal (configurable dislike threshold)
- 🗄 SQLite database with soft-delete and audit logs
- 🐧 systemd service for 24/7 Linux server operation

---

## Tag System

Tags are **fully configurable** through the admin dashboard. You can define:

| Tag Type | Description |
|---|---|
| **Mandatory** | Tags that MUST appear in search results |
| **Required (OR)** | At least one of these must match |
| **Blacklist** | Posts with these tags are excluded |

Tags can be added, removed and managed in real-time via the web panel without restarting the bot.

---

## Requirements

- Python 3.11+
- A Telegram bot token (via [@BotFather](https://t.me/BotFather))
- An e621.net account with API key enabled
- Linux server (optional, for production)

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo>
cd auto-yiff
cp .env.example .env
# Edit .env with your credentials
nano .env
```

### 2. Install dependencies

```bash
make install
# or manually:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run (development)

```bash
make run
# Dashboard: http://localhost:8000
```

### 4. Deploy to Linux server (production)

```bash
# Copy project to /opt/auto-yiff
sudo mkdir -p /opt/auto-yiff
sudo cp -r . /opt/auto-yiff/

# Create .env on server
sudo nano /opt/auto-yiff/.env

# Install as systemd service
make service

# Monitor logs
make logs
```

---

## Environment Variables

| Variable | Description | Example |
|---|---|---|
| `E621_USERNAME` | Your e621.net username | `myuser` |
| `E621_API_KEY` | API key from your e621 profile | `xxxxxx` |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | Target channel/group ID | `-1001234567890` |
| `TELEGRAM_INGEST_CHAT_ID` | Priority ingest group ID | `-5001234567890` |
| `HOST` | Server bind host | `0.0.0.0` |
| `PORT` | Server port | `8000` |
| `MIN_INTERVAL_SECONDS` | Minimum interval (default: 3600) | `3600` |
| `MAX_INTERVAL_SECONDS` | Maximum interval (default: 5400) | `5400` |

---

## Dashboard Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Web dashboard |
| `GET /api/stats` | Global statistics |
| `GET /api/next` | Next post + countdown |
| `GET /api/history` | Last sent posts |
| `GET /api/queue` | Queued posts |
| `GET /api/stream` | SSE real-time stream |
| `POST /api/trigger` | Force immediate send |

---

---

# Auto-Yiff (PT-BR)

> **Bot Telegram + Dashboard Web** que busca artes do [e621.net](https://e621.net) automaticamente e envia para um canal do Telegram em intervalos aleatórios entre **1 hora e 1 hora e 30 minutos**.

---

## Funcionalidades

- 🦊 Busca posts do e621.net via API oficial com tags de busca totalmente configuráveis
- 📤 Envia mídias para o Telegram (foto / vídeo / GIF) **sem legenda**
- ⏱ Intervalo aleatório entre 1h e 1h30 por envio
- ⚡ Fila prioritária de ingest — envie mídias direto por um grupo do Telegram para furar a fila
- 🔄 Reabastece a fila automaticamente quando vazia com tipos de mídia balanceados
- 📊 Dashboard web em tempo real com countdown, histórico e prévia da fila
- 🔴 Server-Sent Events (SSE) para atualizações ao vivo sem recarregar
- 🏷️ Gerenciamento de tags pelo painel admin (obrigatórias, requeridas, OR, blacklist)
- 👍👎 Remoção automática por reações (threshold de dislikes configurável)
- 🗄 Banco SQLite com soft-delete e logs de auditoria
- 🐧 Serviço systemd para operação 24/7 no servidor Linux

---

## Sistema de Tags

As tags são **totalmente configuráveis** pelo painel de administração. Você pode definir:

| Tipo de Tag | Descrição |
|---|---|
| **Obrigatórias** | Tags que DEVEM aparecer nos resultados |
| **Requeridas (OR)** | Pelo menos uma delas deve estar presente |
| **Blacklist** | Posts com essas tags são excluídos |

Tags podem ser adicionadas, removidas e gerenciadas em tempo real pelo painel web sem reiniciar o bot.

---

## Início Rápido

### 1. Clonar e configurar

```bash
git clone <repo>
cd auto-yiff
cp .env.example .env
# Edite o .env com suas credenciais
nano .env
```

### 2. Instalar dependências

```bash
make install
```

### 3. Rodar em desenvolvimento

```bash
make run
# Dashboard: http://localhost:8000
```

### 4. Deploy no servidor Linux

```bash
sudo mkdir -p /opt/auto-yiff
sudo cp -r . /opt/auto-yiff/
sudo nano /opt/auto-yiff/.env
make service
make logs
```

---

## Como obter as credenciais

### Token do Bot Telegram
1. Abra o Telegram e fale com [@BotFather](https://t.me/BotFather)
2. Envie `/newbot` e siga as instruções
3. Copie o token gerado para `TELEGRAM_BOT_TOKEN`

### Chat ID do Canal/Grupo
- **Canal**: adicione o bot como admin, depois use `@username` do canal ou obtenha o ID numérico via `@userinfobot`
- **Grupo**: adicione o bot ao grupo e use `@userinfobot` para obter o ID

### API Key do e621
1. Faça login em [e621.net](https://e621.net)
2. Vá em **Account > My Profile**
3. Clique em **Manage API Access** e gere uma nova chave
4. Copie para `E621_API_KEY`
