ROYAL BINGO — RELEASE CANDIDATE

Files:
- server.py: Python/FastAPI + Telegram bot backend
- index.html: Telegram Mini App frontend
- requirements.txt: pinned Python dependencies

Required environment variables:
- BOT_TOKEN: Telegram bot token (required for Telegram auth and bot)
- ADMIN_ID: numeric Telegram admin user ID
- APP_URL: public HTTPS URL of this deployment
- CORS_ORIGINS: comma-separated allowed origins; normally same as APP_URL
- DB_PATH: persistent SQLite path, e.g. /var/data/royal_bingo.db
- SUPPORT_USERNAME, TELEBIRR_NAME, TELEBIRR_PHONE: optional display settings
- MIN_PLAYERS: optional minimum players (default 2)

Run with exactly ONE worker/process because game rooms are currently in memory:
  uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1

Deploy both server.py and index.html in the same working directory. Mount a persistent disk and set DB_PATH to that disk. Configure the Telegram bot Web App URL to APP_URL.

IMPORTANT LIMITATIONS BEFORE REAL-MONEY USE:
- This build still keeps active game rooms in process memory. A process restart or multiple server instances can interrupt a live round; deploy one process and do not restart during rounds. Persistent round recovery is not implemented.
- SQLite REAL money columns remain from the original schema; a production money system should migrate to integer minor units with a ledger.
- Payment proof is reviewed manually by the admin; this app does not verify bank/TeleBirr transfers with a payment-provider API.
- No independent penetration test, load test, or live payment test has been completed. Use test balances only until those are done and local legal requirements are confirmed.
