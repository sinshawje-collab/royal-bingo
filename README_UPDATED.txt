ROYAL BINGO — PATCHED BUILD

Included files:
- server.py: patched FastAPI + Telegram bot backend
- index.html: patched Telegram Mini App frontend
- requirements.txt: Python dependencies

FIXES IN THIS BUILD
1. Player ID shown in the Mini App profile and admin deposit/withdraw messages is the player's registered phone number.
2. Phone registration is required before the Mini App API can be used. If a player has no phone on file, they must open the bot, send /start, and share their own contact.
3. A phone number already assigned to another account cannot be registered again.
4. Card numbers 1 through 400 are accepted by both /api/buy_card and /api/claim_bingo.
5. Admin add/subtract balance accepts the displayed phone Player ID. Numeric Telegram IDs remain accepted for backward compatibility.

IMPORTANT ID DESIGN
Telegram numeric user ID remains the private/internal database key used for Telegram authentication, wallet records, and existing account continuity. The public Player ID displayed to players/admins is the registered phone number. This avoids breaking existing balances and transaction history by changing primary database keys.

DEPLOYMENT
Build command:
  pip install -r requirements.txt

Start command (one worker only):
  uvicorn server:app --host 0.0.0.0 --port $PORT --workers 1

Deploy server.py and index.html together in the same directory. Keep your existing persistent SQLite database and set DB_PATH to its existing path. Do not delete or replace the database.

Required Render environment variables:
- BOT_TOKEN
- ADMIN_ID
- APP_URL

Recommended:
- DB_PATH (point to the existing persistent disk database)
- CORS_ORIGINS (your app URL)
- SUPPORT_USERNAME
- TELEBIRR_NAME
- TELEBIRR_PHONE
- MIN_PLAYERS

AFTER DEPLOYMENT
- Existing users whose phone field is blank must open the Royal Bingo bot, send /start, and share their own phone contact once.
- Then reopen the Mini App. Their Player ID should display as their phone number.
- Test cards 101, 250, and 400 in a test round before using real balances.
- The app still limits each player to 4 cards per stake room/round.

SAFETY
Back up the persistent SQLite database before deployment. This patch does not intentionally change the database schema or reset wallets. The current game rooms are held in process memory; keep one worker and avoid restarting during a live round. Payment approval is still manual; this build does not independently verify TeleBirr/CBE Birr transfers.
