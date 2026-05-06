"""
Configuration — all values come from environment variables.
Set these in Railway's Variables panel.
"""

import os

# ── Required ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")

# ── Optional ──────────────────────────────────────────────────────────────────
# Comma-separated Telegram user IDs allowed to use the bot.
# Leave blank to allow anyone (not recommended).
_raw_ids = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}

# SQLite database file path
DB_PATH = os.environ.get("DB_PATH", "aaminata.db")

# Daily summary time in IST (24h format)
SUMMARY_HOUR   = int(os.environ.get("SUMMARY_HOUR",   "20"))  # 8pm
SUMMARY_MINUTE = int(os.environ.get("SUMMARY_MINUTE", "0"))
