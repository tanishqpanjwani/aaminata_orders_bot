# 🌿 Aaminata Order Tracker Bot

Telegram bot for Aaminata skincare — natural language order logging, customer management, and daily summaries.

---

## Features
- 🗣️ **Natural language orders** — just type `kumkumadi oil 3 bottles 3600 ansh` (English, Hindi, Hinglish all work)
- 👤 Add / search customers
- 📦 Log & track orders with status updates
- 📊 Export all orders as CSV
- 🌙 Daily 8pm IST summary sent automatically

---

## Railway Variables (set all of these)

| Variable | Value | Required |
|---|---|---|
| `BOT_TOKEN` | From @BotFather | ✅ |
| `ANTHROPIC_API_KEY` | From console.anthropic.com | ✅ |
| `ADMIN_IDS` | Your Telegram user ID(s), comma-separated | ✅ |
| `DB_PATH` | `/data/aaminata.db` (after adding Volume) | ✅ |
| `SUMMARY_HOUR` | `20` (8pm IST) | optional |
| `SUMMARY_MINUTE` | `0` | optional |

---

## Setup Steps

### 1. Create Telegram Bot
- Message @BotFather → `/newbot` → copy the token

### 2. Get your Telegram User ID
- Message @userinfobot → copy your numeric ID
- If your mum also needs access, have her do the same

### 3. Get Anthropic API Key
- Go to console.anthropic.com → API Keys → Create key
- You get $5 free credit (~1500 order parses for free)

### 4. Deploy to Railway
1. Push this folder to a GitHub repo
2. railway.app → New Project → Deploy from GitHub
3. Add all Variables from the table above
4. It will auto-deploy using the Procfile

### 5. Add Persistent Storage (important!)
1. Railway project → Add Plugin → Volume
2. Mount path: `/data`
3. Change `DB_PATH` variable to `/data/aaminata.db`
4. Redeploy

---

## Local Development
```bash
pip install -r requirements.txt
export BOT_TOKEN="your_token"
export ANTHROPIC_API_KEY="your_key"
export ADMIN_IDS="your_user_id"
export DB_PATH="aaminata.db"
python bot.py
```

---

## File Structure
```
aaminata-bot/
├── bot.py           # All bot logic, handlers, NLP, daily summary
├── database.py      # SQLite wrapper
├── config.py        # Env var config
├── requirements.txt
├── Procfile
└── README.md
```
