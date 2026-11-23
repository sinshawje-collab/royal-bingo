import base64, hashlib, hmac, io, json, math, os, random, re, secrets, threading, time, urllib.parse
import psycopg
sqlite3 = psycopg
from psycopg.rows import dict_row
from contextlib import contextmanager
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import telebot
from telebot.types import InlineKeyboardButton as IBtn, InlineKeyboardMarkup as IKB, KeyboardButton as KBtn, ReplyKeyboardMarkup as RKB, WebAppInfo

TOKEN = os.environ.get('BOT_TOKEN', '').strip().strip("'\"")
try: ADMIN_ID = int(os.environ.get('ADMIN_ID', '0').strip().strip("'\"") or '0')
except Exception: ADMIN_ID = 0

DEFAULT_URL = 'https://royal-bingo-9u3a.onrender.com'
APP_URL = (os.environ.get('APP_URL') or DEFAULT_URL).strip().strip("'\"").rstrip('/')
APP_URL = ('https://' + APP_URL[8:]) if APP_URL.lower().startswith('https://') else DEFAULT_URL

SUPPORT = os.environ.get('SUPPORT_USERNAME', '@royal_bingo_99_Support')
TB_NAME = os.environ.get('TELEBIRR_NAME', 'SINSHAW JEMANEH ESHETE')
TB_PHONE = os.environ.get('TELEBIRR_PHONE', '0941882333')
CBE_NAME = os.environ.get('CBE_BIRR_NAME', 'Sinshaw Jemaneh Eshete')
CBE_PHONE = os.environ.get('CBE_BIRR_PHONE', '0941882333')
DB_PATH = os.environ.get('DB_PATH', 'royal_bingo.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip().strip("'\\\"")
HOUSE_CUT, CD_SEC, CALL_SEC, WIN_SEC = 0.20, 40, 3.0, 10
MIN_PLAYERS_BY_STAKE = {10: 1, 20: 1, 50: 1}
MIN_PLAYERS = 1

def set_min_players(value, persist=True):
  global MIN_PLAYERS, MIN_PLAYERS_BY_STAKE
  value = max(1, min(400, int(value)))
  MIN_PLAYERS = value
  MIN_PLAYERS_BY_STAKE = {10: value, 20: value, 50: value}
  if persist:
    with get_db() as c:
      c.execute('UPDATE game_settings SET value=? WHERE key=?', (value, 'min_players'))
  return value

def load_game_settings():
  try:
    with get_db() as c:
      row = c.execute('SELECT value FROM game_settings WHERE key=?', ('min_players',)).fetchone()
    if row:
      set_min_players(int(row['value']), persist=False)
  except Exception as exc:
    print('Game settings load failed:', type(exc).__name__, str(exc))

def min_players_for(stake):
  return MIN_PLAYERS_BY_STAKE.get(int(stake), MIN_PLAYERS)
db_lock, room_lock = threading.RLock(), threading.RLock()

class HybridRow(dict):
  def __getitem__(self, key):
    if isinstance(key, int):
      return list(self.values())[key]
    return super().__getitem__(key)

class DBCursor:
  def __init__(self, cursor, lastrowid=None):
    self.cursor, self.lastrowid = cursor, lastrowid
    self.rowcount = cursor.rowcount
  def _row(self, row):
    return HybridRow(row) if row is not None else None
  def fetchone(self):
    return self._row(self.cursor.fetchone())
  def fetchall(self):
    return [self._row(row) for row in self.cursor.fetchall()]

class DBConnection:
  def __init__(self, connection):
    self.connection = connection
  def execute(self, sql, params=()):
    query = sql.replace('?', '%s').replace('BEGIN IMMEDIATE', 'BEGIN')
    # psycopg uses percent signs for placeholders; escape literal SQL LIKE wildcards.
    query = re.sub(r'%(?![sbt])', '%%', query)
    query = query.replace('MAX(0,play_wallet-%s)', 'GREATEST(0,play_wallet-%s)')
    query = query.replace('INSERT OR IGNORE INTO', 'INSERT INTO')
    if 'INSERT OR IGNORE' in sql.upper():
      query += ' ON CONFLICT DO NOTHING'
    if query.lstrip().upper().startswith('INSERT INTO REQUESTS') and 'RETURNING' not in query.upper():
      query += ' RETURNING id'
      cur = self.connection.execute(query, params)
      inserted = cur.fetchone()
      return DBCursor(cur, inserted['id'] if inserted else None)
    return DBCursor(self.connection.execute(query, params))

_db_pool = None
_db_pool_lock = threading.Lock()

def get_db_pool():
  global _db_pool
  if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL is required; configure the Neon PostgreSQL connection in Render.')
  if _db_pool is None:
    with _db_pool_lock:
      if _db_pool is None:
        from psycopg_pool import ConnectionPool
        _db_pool = ConnectionPool(
          conninfo=DATABASE_URL,
          min_size=1,
          max_size=10,
          timeout=10,
          kwargs={'row_factory': dict_row, 'connect_timeout': 15},
          open=True
        )
  return _db_pool

@contextmanager
def get_db():
  # Reuse warm Neon connections instead of opening a new TLS/database connection
  # for every card tap, state poll, and wallet operation.
  with get_db_pool().connection() as conn:
    yield DBConnection(conn)

def init_db():
  with get_db() as c:
    statements = [
      """CREATE TABLE IF NOT EXISTS users (uid BIGINT PRIMARY KEY, name TEXT DEFAULT 'Player', phone TEXT,
        play_wallet DOUBLE PRECISION DEFAULT 0, win_wallet DOUBLE PRECISION DEFAULT 0, matches_played INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0, total_won DOUBLE PRECISION DEFAULT 0, referred_by BIGINT, created_at BIGINT)""",
      """CREATE TABLE IF NOT EXISTS transactions (id BIGSERIAL PRIMARY KEY, uid BIGINT, icon TEXT,
        title TEXT, amount DOUBLE PRECISION, created_at BIGINT)""",
      """CREATE TABLE IF NOT EXISTS requests (id BIGSERIAL PRIMARY KEY, req_type TEXT, uid BIGINT,
        amount DOUBLE PRECISION, method TEXT, details TEXT, status TEXT DEFAULT 'pending', created_at BIGINT, reference TEXT)""",
      """CREATE TABLE IF NOT EXISTS house_stats (id INTEGER PRIMARY KEY CHECK (id=1),
        total_commission DOUBLE PRECISION DEFAULT 0, total_rounds INTEGER DEFAULT 0)""",
      "INSERT INTO house_stats VALUES (1, 0, 0) ON CONFLICT DO NOTHING",
      """CREATE TABLE IF NOT EXISTS game_rooms (stake INTEGER PRIMARY KEY, snapshot TEXT NOT NULL, updated_at BIGINT NOT NULL)""",
      """CREATE TABLE IF NOT EXISTS game_payouts (round_id TEXT PRIMARY KEY, uid BIGINT NOT NULL, prize DOUBLE PRECISION NOT NULL, card_no INTEGER NOT NULL, created_at BIGINT NOT NULL)""",
      "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at BIGINT NOT NULL)",
      "CREATE TABLE IF NOT EXISTS admin_rotation (id INTEGER PRIMARY KEY CHECK (id=1), start_at BIGINT NOT NULL)",
      "INSERT INTO admin_rotation(id,start_at) VALUES(1," + str(int(time.time())) + ") ON CONFLICT(id) DO NOTHING",
      "CREATE TABLE IF NOT EXISTS game_settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
      "INSERT INTO game_settings(key,value) VALUES('min_players',1) ON CONFLICT(key) DO NOTHING",
      "CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_reference ON requests(reference) WHERE reference IS NOT NULL AND reference <> ''",
      # Support high-frequency per-player history and pending-withdrawal queries.
      "CREATE INDEX IF NOT EXISTS idx_transactions_uid_id ON transactions(uid, id DESC)",
      "CREATE INDEX IF NOT EXISTS idx_requests_uid_pending ON requests(uid, req_type, status, id DESC)",
      # Support phone verification/lookups and the public top-winners list.
      "CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone) WHERE phone IS NOT NULL",
      "CREATE INDEX IF NOT EXISTS idx_users_top_winners ON users(total_won DESC) WHERE total_won > 0"
    ]
    for statement in statements:
      c.execute(statement)
    if not c.execute("SELECT 1 FROM schema_migrations WHERE name='unify_player_wallets_v1'").fetchone():
      # Preserve every player's full balance by combining both legacy wallets exactly once.
      c.execute("UPDATE users SET play_wallet=COALESCE(play_wallet,0)+COALESCE(win_wallet,0), win_wallet=0")
      c.execute("INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)", ('unify_player_wallets_v1', int(time.time())))

def ensure_user(uid, name='Player', ref=None):
  with get_db() as c:
    r = c.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone()
    if not r:
      c.execute('INSERT INTO users (uid,name,play_wallet,win_wallet,referred_by,created_at) VALUES (?,?,0,0,?,?)', (uid, name or 'Player', ref, int(time.time())))
      r = c.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone()
    elif name and name != 'Player' and r['name'] != name:
      c.execute('UPDATE users SET name=? WHERE uid=?', (name, uid))
      r = c.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone()
    return dict(r)

def add_tx(c, uid, ic, title, amt):
  c.execute('INSERT INTO transactions (uid,icon,title,amount,created_at) VALUES (?,?,?,?,?)', (uid, ic, title, float(amt), int(time.time())))

def finance_service_status(now=None):
  # Ethiopia uses UTC+3. Service is closed from 9:00 PM until 8:00 AM Ethiopian time.
  local = time.gmtime((time.time() if now is None else now) + 3 * 60 * 60)
  closed = local.tm_hour >= 21 or local.tm_hour < 8
  return {
    'closed': closed,
    'local_hour': local.tm_hour,
    'message': '⛔ የገቢና ወጪ አገልግሎት በየቀኑ ከማታ 3:00 (9:00 PM) እስከ ጠዋት 2:00 (8:00 AM) ድረስ ዝግ ነው። እባክዎ ከጠዋት 2:00 በኋላ ይመለሱ።'
  }

def ensure_finance_service_open(uid=None):
  status = finance_service_status()
  # Finance hours restrict players only; both verified admins remain exempt.
  if status['closed'] and not (uid is not None and is_adm(uid)):
    raise HTTPException(503, status['message'])
  return status

def finance_service_closed_for(uid):
  return finance_service_status()['closed'] and not is_adm(uid)

def finance_closed_bot_message():
  return '⛔ የገቢና ወጪ አገልግሎት በየቀኑ ከማታ 3:00 (9:00 PM) እስከ ጠዋት 2:00 (8:00 AM) ድረስ ዝግ ነው። እባክዎ ከጠዋት 2:00 በኋላ ይሞክሩ።'

def available_wallet(c, uid):
  row = c.execute("SELECT COALESCE(play_wallet,0)+COALESCE(win_wallet,0)-COALESCE((SELECT SUM(amount) FROM requests WHERE uid=? AND req_type='withdraw' AND status='pending' AND details LIKE 'PENDING RESERVED |%'),0) AS available FROM users WHERE uid=?", (uid, uid)).fetchone()
  return float(row['available'] or 0) if row else 0.0

def gen_card(no):
  rng = random.SystemRandom()
  cols = [rng.sample(range(a, b + 1), 5) for a, b in [(1,15),(16,30),(31,45),(46,60),(61,75)]]
  return ['FREE' if (r == 2 and c == 2) else cols[c][r] for r in range(5) for c in range(5)]

WIN_LINES = [[0,1,2,3,4],[5,6,7,8,9],[10,11,12,13,14],[15,16,17,18,19],[20,21,22,23,24],
             [0,5,10,15,20],[1,6,11,16,21],[2,7,12,17,22],[3,8,13,18,23],[4,9,14,19,24],
             [0,6,12,18,24],[4,8,12,16,20],[0,4,20,24]]
check_win = lambda g, cs: next((ln for ln in WIN_LINES if all(g[i] == 'FREE' or g[i] in cs for i in ln)), None)

def mk_room(s):
  return {'stake': s, 'phase': 'WAITING', 'countdown': CD_SEC, 'cards': {}, 'called': [], 'deck': [],
          'tick': time.time(), 'winner': None, 'win_until': 0.0, 'round_id': secrets.token_hex(12)}

def save_room(r, c=None):
  payload = json.dumps({k: r[k] for k in ('stake','phase','countdown','cards','called','deck','tick','winner','win_until','round_id')}, separators=(',', ':'))
  if c is not None:
    c.execute('INSERT INTO game_rooms(stake,snapshot,updated_at) VALUES(?,?,?) ON CONFLICT(stake) DO UPDATE SET snapshot=excluded.snapshot, updated_at=excluded.updated_at', (r['stake'], payload, int(time.time())))
  else:
    with get_db() as db:
      db.execute('INSERT INTO game_rooms(stake,snapshot,updated_at) VALUES(?,?,?) ON CONFLICT(stake) DO UPDATE SET snapshot=excluded.snapshot, updated_at=excluded.updated_at', (r['stake'], payload, int(time.time())))

def load_rooms():
  with get_db() as c:
    rows = c.execute('SELECT stake,snapshot FROM game_rooms').fetchall()
  for row in rows:
    try:
      saved = json.loads(row['snapshot'])
      stake = int(row['stake'])
      if stake not in rooms or not isinstance(saved.get('cards'), dict): continue
      room = mk_room(stake)
      room.update(saved)
      room['cards'] = {int(k): v for k, v in room['cards'].items()}
      room['called'] = [int(x) for x in room.get('called', [])]
      room['deck'] = [int(x) for x in room.get('deck', [])]
      room.setdefault('round_id', secrets.token_hex(12))
      room['tick'] = time.time()
      if room['phase'] == 'WINNER':
        room['win_until'] = time.time() + min(WIN_SEC, max(0, float(room.get('win_until', 0)) - time.time()))
      elif room['phase'] == 'COUNTDOWN':
        room['countdown'] = max(1, min(CD_SEC, int(room.get('countdown', CD_SEC))))
      elif room['phase'] not in ('WAITING', 'PLAYING'):
        room = mk_room(stake)
      rooms[stake] = room
    except Exception as exc:
      print('Room restore error:', row['stake'], exc)
rooms = {10: mk_room(10), 20: mk_room(20), 50: mk_room(50)}
n_players = lambda r: len({v['uid'] for v in r['cards'].values()})
derash = lambda r: round(len(r['cards']) * r['stake'] * (1.0 - HOUSE_CUT), 2)

def finish_round(r, uid, name, cno, wl):
  prize = derash(r)
  comm = round(len(r['cards']) * r['stake'] - prize, 2)
  r['phase'], r['win_until'] = 'WINNER', time.time() + WIN_SEC
  r['winner'] = {'uid': uid, 'name': name, 'card_no': cno, 'prize': prize, 'grid': r['cards'][cno]['grid'], 'win_line': wl}
  with get_db() as c:
    c.execute('BEGIN IMMEDIATE')
    cur = c.execute('INSERT OR IGNORE INTO game_payouts(round_id,uid,prize,card_no,created_at) VALUES(?,?,?,?,?)', (r['round_id'], uid, prize, cno, int(time.time())))
    if cur.rowcount == 1:
      c.execute('UPDATE users SET play_wallet=play_wallet+?, wins=wins+1, total_won=total_won+? WHERE uid=?', (prize, prize, uid))
      add_tx(c, uid, '🏆', f'BINGO አሸነፉ! (Card #{cno})', prize)
      c.execute('UPDATE house_stats SET total_commission=total_commission+?, total_rounds=total_rounds+1 WHERE id=1', (comm,))
    save_room(r, c)

def reset_room(r):
  r.update({'phase': 'WAITING', 'countdown': CD_SEC, 'cards': {}, 'called': [], 'deck': [], 'winner': None, 'tick': time.time(), 'win_until': 0.0, 'round_id': secrets.token_hex(12)})
  save_room(r)

def start_round(r, now):
  r.update({'phase': 'PLAYING', 'countdown': 0, 'called': [], 'deck': list(range(1, 76)), 'tick': now})
  secrets.SystemRandom().shuffle(r['deck'])
  save_room(r)
  with get_db() as c:
    for uid in {v['uid'] for v in r['cards'].values()}:
      c.execute('UPDATE users SET matches_played=matches_played+1 WHERE uid=?', (uid,))

def game_loop():
  while True:
    now = time.time()
    with room_lock:
      for r in rooms.values():
        pc = n_players(r)
        if r['phase'] == 'WAITING' and pc >= min_players_for(r['stake']):
          # Every round, including solo play, begins with a full 40-second lobby countdown.
          r.update({'phase': 'COUNTDOWN', 'countdown': CD_SEC, 'tick': now})
          save_room(r)
        elif r['phase'] == 'COUNTDOWN':
          if pc < min_players_for(r['stake']):
            r.update({'phase': 'WAITING', 'countdown': CD_SEC, 'tick': now})
            save_room(r)
          else:
            elapsed = int(now - float(r.get('tick', now)))
            if elapsed >= 1:
              remaining = max(0, int(r.get('countdown', CD_SEC)) - elapsed)
              if remaining <= 0:
                start_round(r, now)
              else:
                r.update({'countdown': remaining, 'tick': float(r.get('tick', now)) + elapsed})
                save_room(r)
        elif r['phase'] == 'PLAYING' and now - r['tick'] >= CALL_SEC:
          r['tick'] = now
          if r['deck']:
            r['called'].insert(0, r['deck'].pop())
            save_room(r)
            cs = set(r['called'])
            for cno, info in sorted(r['cards'].items()):
              wl = check_win(info['grid'], cs)
              if wl:
                finish_round(r, info['uid'], info['name'], cno, wl)
                break
          else:
            reset_room(r)
        elif r['phase'] == 'WINNER' and now >= r['win_until']:
          reset_room(r)
    time.sleep(0.35)

def auth_user(init_data: str):
  if not init_data or not TOKEN:
    raise HTTPException(401, 'እባክዎ አፑን በቴሌግራም ቦት ውስጥ ብቻ ይክፈቱ!')
  try:
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    if len(dict(pairs)) != len(pairs):
      raise ValueError('duplicate fields')
    p = dict(pairs)
    rh = p.pop('hash', None)
    dc = '\n'.join(f'{k}={v}' for k, v in sorted(p.items()))
    sec = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(sec, dc.encode(), hashlib.sha256).hexdigest()
    if not rh or not hmac.compare_digest(expected, rh):
      raise ValueError('invalid hash')
    auth_date = int(p.get('auth_date', '0'))
    now = int(time.time())
    if auth_date <= 0 or auth_date > now + 30 or now - auth_date > 86400:
      raise ValueError('expired initData')
    uo = json.loads(p.get('user', '{}'))
    uid = int(uo['id'])
    if uid <= 0:
      raise ValueError('invalid user')
    nm = ' '.join(x for x in [uo.get('first_name'), uo.get('last_name')] if x) or 'Player'
    user = ensure_user(uid, nm)
    if not user.get('phone'):
      raise HTTPException(403, 'መጀመሪያ በRoyal Bingo Bot ውስጥ /start ብለው የራስዎን ስልክ ቁጥር ያጋሩ።')
    # Preserve the verified second-admin identity: Mini App Telegram profile names
    # must not overwrite the canonical finance-admin name stored during contact verification.
    if normalize_phone(user.get('phone')) == normalize_phone(SECOND_ADMIN_PHONE):
      with get_db() as db:
        db.execute('UPDATE users SET name=? WHERE uid=?', (SECOND_ADMIN_NAME, uid))
        user = dict(db.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone())
    return user
  except HTTPException:
    raise
  except Exception:
    raise HTTPException(401, 'የTelegram መግቢያ መረጃ ተሳስቷል ወይም ጊዜው አልፎበታል። እባክዎ አፑን እንደገና ይክፈቱ!')

bot = telebot.TeleBot(TOKEN) if TOKEN else None

# Second administrator identity must match both the registered full name and
# phone number verified through Telegram's own-contact sharing flow.
SECOND_ADMIN_NAME = 'BEREKET DAGNACHEW ALEMU'
SECOND_ADMIN_PHONE = '0932760267'
ADMIN_ROTATION_SECONDS = 6 * 60 * 60

def normalize_phone(value):
  digits = re.sub(r'\D', '', str(value or ''))
  if digits.startswith('251') and len(digits) == 12:
    return '+' + digits
  if digits.startswith('0') and len(digits) == 10:
    return '+251' + digits[1:]
  return '+' + digits if digits else ''

def normalize_person_name(value):
  return ' '.join(str(value or '').upper().split())

def bereket_admin_id():
  with get_db() as c:
    rows = c.execute('SELECT uid, name, phone FROM users WHERE phone IS NOT NULL').fetchall()
  for row in rows:
    # The phone number is saved only after Telegram's own-contact verification.
    # Do not depend on the mutable Telegram profile/display name for admin identity.
    if normalize_phone(row['phone']) == normalize_phone(SECOND_ADMIN_PHONE):
      return int(row['uid'])
  return None

def admin_ids():
  ids = {int(ADMIN_ID)} if ADMIN_ID else set()
  second_id = bereket_admin_id()
  if second_id:
    ids.add(second_id)
  return sorted(ids)

def finance_admin_ids(now=None):
  owner_id = int(ADMIN_ID) if ADMIN_ID else None
  second_id = bereket_admin_id()
  if not owner_id:
    return []
  # Never strand deposits/withdrawals if the second admin has not verified yet.
  if not second_id:
    return [owner_id]
  with get_db() as c:
    row = c.execute('SELECT start_at FROM admin_rotation WHERE id=1').fetchone()
  if not row:
    return [owner_id]
  elapsed_slots = max(0, int((int(now or time.time()) - int(row['start_at'])) // ADMIN_ROTATION_SECONDS))
  return [second_id if elapsed_slots % 2 == 0 else owner_id]

def finance_rotation_status(now=None):
  now = int(now or time.time())
  owner_id = int(ADMIN_ID) if ADMIN_ID else None
  second_id = bereket_admin_id()
  with get_db() as db:
    row = db.execute('SELECT start_at FROM admin_rotation WHERE id=1').fetchone()
  if not row or not owner_id:
    return {'configured': False, 'verified': False, 'active_name': 'Not configured', 'next_name': 'Not configured', 'remaining': 0, 'active_id': None}
  if not second_id:
    return {
      'configured': True, 'verified': False,
      'active_name': 'SINSHAW JEMANEH ESHETE',
      'next_name': 'BEREKET DAGNACHEW ALEMU (verification required)',
      'remaining': 0, 'active_id': owner_id
    }
  elapsed = max(0, now - int(row['start_at']))
  slot = elapsed // ADMIN_ROTATION_SECONDS
  remaining = ADMIN_ROTATION_SECONDS - (elapsed % ADMIN_ROTATION_SECONDS)
  second_active = slot % 2 == 0
  active_id = second_id if second_active else owner_id
  active_name = SECOND_ADMIN_NAME if second_active else 'SINSHAW JEMANEH ESHETE'
  active_phone = SECOND_ADMIN_PHONE if second_active else os.environ.get('TELEBIRR_PHONE', '0941882333')
  return {
    'configured': True, 'verified': True,
    'active_name': active_name, 'active_phone': active_phone,
    'next_name': 'SINSHAW JEMANEH ESHETE' if second_active else SECOND_ADMIN_NAME,
    'remaining': int(remaining), 'active_id': active_id
  }

def active_payment_accounts():
  """Return payment destination and active administrator identity from the same rotation state."""
  active_ids = finance_admin_ids()
  active_id = active_ids[0] if active_ids else (int(ADMIN_ID) if ADMIN_ID else None)
  second_id = bereket_admin_id()
  if second_id and active_id == second_id:
    name, phone = SECOND_ADMIN_NAME, SECOND_ADMIN_PHONE
    return {
      'telebirr_name': name, 'telebirr_phone': phone,
      'cbe_name': name, 'cbe_phone': phone,
      'active_admin_name': name, 'active_admin_phone': phone
    }
  # The primary owner's payment details must not inherit stale Bereket values
  # from Render environment variables after a finance-turn handover.
  tele_name = 'SINSHAW JEMANEH ESHETE'
  tele_phone = '0941882333'
  cbe_name = 'Sinshaw Jemaneh Eshete'
  cbe_phone = '0941882333'
  return {
    'telebirr_name': tele_name, 'telebirr_phone': tele_phone,
    'cbe_name': cbe_name, 'cbe_phone': cbe_phone,
    'active_admin_name': tele_name, 'active_admin_phone': tele_phone
  }

def switch_finance_turn():
  """Immediately hand finance duty to the other verified admin and restart the six-hour timer."""
  now = int(time.time())
  status = finance_rotation_status(now)
  second_id = bereket_admin_id()
  owner_id = int(ADMIN_ID) if ADMIN_ID else None
  if not status.get('configured') or not second_id or not owner_id:
    raise ValueError('ሁለቱም አድሚኖች መረጋገጥ አለባቸው።')
  currently_second = int(status.get('active_id') or 0) == int(second_id)
  # Slot 0 belongs to Bereket; slot 1 belongs to the primary owner.
  new_start = now - (ADMIN_ROTATION_SECONDS if currently_second else 0)
  with get_db() as c:
    c.execute('UPDATE admin_rotation SET start_at=? WHERE id=1', (new_start,))
  return finance_rotation_status(now)

def is_finance_admin(uid):
  try:
    return int(uid) in finance_admin_ids()
  except Exception:
    return False

def is_adm(uid):
  try:
    uid = int(uid)
    return bool(ADMIN_ID and uid == int(ADMIN_ID)) or uid in admin_ids()
  except Exception:
    return False

def menu_kb(uid=None):
  # Keep Telegram menu actions as normal text buttons so bot workflows run.
  kb = RKB(resize_keyboard=True, row_width=2)
  kb.row(KBtn('📝 Register'), KBtn('🎮 Play'))
  kb.row(KBtn('💳 Deposit'), KBtn('📢 Join Channel'))
  kb.row(KBtn('💸 Withdraw'), KBtn('🔄 Transfer'))
  if uid and is_adm(uid):
    kb.row(KBtn('👑 Admin Panel'), KBtn('💳 Balance'))
  else:
    kb.row(KBtn('💳 Balance'))
  kb.row(KBtn('👥 Invite'), KBtn('🎧 Support'))
  kb.row(KBtn('📜 Rules'))
  return kb

def play_kb(lbl='🎲 Play Royal Bingo'):
  kb = IKB(); kb.add(IBtn(lbl, web_app=WebAppInfo(url=APP_URL))); return kb

def section_kb(section, action=None, label='📲 Open Royal Bingo'):
  query = f'?section={section}'
  if action:
    query += f'&action={action}'
  kb = IKB()
  kb.add(IBtn(label, web_app=WebAppInfo(url=APP_URL + query)))
  return kb

def adm_kb():
  kb = IKB()
  kb.row(IBtn('📊 ሪፖርትና ትርፍ', callback_data='adm_stats'), IBtn('👥 ተጫዋቾች', callback_data='adm_users'))
  kb.row(IBtn('➕ ብር ጨምር', callback_data='adm_add'), IBtn('➖ ብር ቀንስ', callback_data='adm_sub'))
  kb.row(IBtn(f'⚙️ ዝቅተኛ ተጫዋች: {MIN_PLAYERS}', callback_data='adm_minp'))
  kb.row(IBtn('➖ ቀንስ', callback_data='adm_minp_dec'), IBtn('➕ ጨምር', callback_data='adm_minp_inc'))
  kb.row(IBtn('💾 DB Backup', callback_data='adm_backup'))
  kb.row(IBtn('⏱️ የገቢ/ወጪ ተራ ሰዓት', callback_data='adm_timer'))
  kb.row(IBtn('🔄 የገቢ/ወጪ ተራ ቀይር', callback_data='adm_switch_finance'))
  kb.row(IBtn('📢 ለሁሉም መልዕክት ላክ', callback_data='adm_bcast'))
  return kb

def notify_req(rtype, uid, amt, md, det, reference=None):
  u = ensure_user(uid)
  with get_db() as c:
    rid = c.execute('INSERT INTO requests (req_type,uid,amount,method,details,status,created_at,reference) VALUES (?,?,?,?,?,?,?,?)',
                    (rtype, uid, float(amt), md, det, 'pending', int(time.time()), reference)).lastrowid
  aids = finance_admin_ids()
  if not bot or not aids: return rid
  kb = IKB()
  if rtype == 'deposit':
    hdr = '🔔 አዲስ የገቢ (DEPOSIT) ጥያቄ!'
    kb.row(IBtn('✅ ፍቀድ', callback_data=f'ap_dep_{rid}'), IBtn('❌ ሰርዝ', callback_data=f'rj_dep_{rid}'))
  else:
    hdr = '🔔 አዲስ የወጪ (WITHDRAW) ጥያቄ!'
    kb.row(IBtn('✅ ተልኳል', callback_data=f'ap_wd_{rid}'), IBtn('❌ ሰርዝና መልስ', callback_data=f'rj_wd_{rid}'))
  msg = f'{hdr}\\n\\n🆔 Request #{rid}\\n👤 {u["name"]}\\n🆔 Player ID: {u["phone"] or "Not registered"}\\n💵 {float(amt):.2f} ETB\\n🏦 {md}\\n📝 {det}'
  for aid in aids:
    try: bot.send_message(aid, msg, reply_markup=kb)
    except Exception: pass
  return rid

if bot:
  @bot.message_handler(commands=['myid'])
  def c_myid(m): bot.reply_to(m, f'🆔 የአንተ Telegram ID፦ `{m.from_user.id}`', parse_mode='Markdown')

  @bot.message_handler(commands=['admin', 'adminpanel'])
  @bot.message_handler(func=lambda m: bool(m.text) and m.text.strip().replace('👑', '').strip().lower() in ('admin panel', 'admin'))
  def c_admin(m):
    uid = getattr(getattr(m, 'from_user', None), 'id', None)
    print(f'Admin panel requested: uid={uid}, chat={getattr(getattr(m, "chat", None), "id", None)}')
    try:
      if not is_adm(uid):
        return bot.reply_to(m, '⛔ ይህ መቆጣጠሪያ ለተፈቀደላቸው አድሚኖች ብቻ ነው። የTelegram ID ለማየት /myid ይጻፉ።')
      bot.send_message(m.chat.id, '👑 ROYAL BINGO — የአድሚን መቆጣጠሪያ', reply_markup=adm_kb())
    except Exception as exc:
      print(f'Admin panel open failed for uid={uid}: {type(exc).__name__}: {exc}')
      try:
        bot.send_message(m.chat.id, '❌ Admin Panel ሲከፈት ስህተት ተፈጥሯል። እባክዎ /admin እንደገና ይሞክሩ።')
      except Exception:
        pass

  @bot.message_handler(commands=['start'])
  def c_start(m):
    ps = (m.text or '').split()
    ref = int(ps[1]) if len(ps) > 1 and ps[1].isdigit() and int(ps[1]) != m.from_user.id else None
    telegram_name = ' '.join(x for x in [m.from_user.first_name, m.from_user.last_name] if x) or 'Player'
    u = ensure_user(m.from_user.id, telegram_name, ref)
    if not u['phone']:
      kb = RKB(resize_keyboard=True, one_time_keyboard=True); kb.add(KBtn('📱 ስልክ ቁጥር አጋራ', request_contact=True))
      return bot.send_message(m.chat.id, '👑 እንኳን ወደ ROYAL BINGO በደህና መጡ!\n\nለመመዝገብ ስልክ ቁጥርዎን ያጋሩ 👇', reply_markup=kb)
    bot.send_message(m.chat.id, f'👑 ወደ ROYAL BINGO በደህና መጡ!\n🎧 ድጋፍ፦ {SUPPORT}', reply_markup=menu_kb(m.from_user.id))
    bot.send_message(m.chat.id, '🎰 ጨዋታውን ለመክፈት ከታች ይጫኑ 👇', reply_markup=play_kb('🎮 Play Now'))

  @bot.message_handler(content_types=['contact'])
  def c_contact(m):
    if getattr(m.contact, 'user_id', None) and m.contact.user_id != m.from_user.id:
      return bot.reply_to(m, '❌ እባክዎ የራስዎን ስልክ ቁጥር ብቻ ያጋሩ።')
    phone = (m.contact.phone_number or '').strip()
    if normalize_phone(phone) == normalize_phone(SECOND_ADMIN_PHONE) and getattr(m.contact, 'user_id', None) != m.from_user.id:
      return bot.reply_to(m, '❌ ለአድሚን ማረጋገጫ የራስዎን Telegram Contact ከስልክዎ ያጋሩ።')
    telegram_name = ' '.join(x for x in [m.from_user.first_name, m.from_user.last_name] if x) or 'Player'
    with get_db() as c:
      owner = c.execute('SELECT uid FROM users WHERE phone=? AND uid<>?', (phone, m.from_user.id)).fetchone()
      if owner:
        return bot.reply_to(m, '❌ ይህ ስልክ ቁጥር በሌላ ተጫዋች ተመዝግቧል። ድጋፍን ያነጋግሩ።')
      # Normalize the verified contact and bind the explicitly supplied
      # second-admin full name only after Telegram confirms this is the sender's own contact.
      verified_second_admin = normalize_phone(phone) == normalize_phone(SECOND_ADMIN_PHONE)
      saved_name = SECOND_ADMIN_NAME if verified_second_admin else telegram_name
      ensure_user(m.from_user.id, saved_name)
      c.execute('UPDATE users SET name=?, phone=? WHERE uid=?', (saved_name, normalize_phone(phone), m.from_user.id))
      # Start the automatic six-hour cycle at the moment Bereket verifies his own Telegram contact.
      # Slot zero is Bereket; after six hours the duty moves to the primary owner.
      if verified_second_admin:
        c.execute('UPDATE admin_rotation SET start_at=? WHERE id=1', (int(time.time()),))
    bot.send_message(m.chat.id, f'✅ ተመዝግበዋል! ({m.contact.phone_number})', reply_markup=menu_kb(m.from_user.id))
    if verified_second_admin:
      bot.send_message(m.chat.id, '✅ የአድሚን ማረጋገጫ ተሳክቷል። የገቢ/ወጪ 6 ሰዓት ተራዎ አሁን ተጀምሯል። ከ6 ሰዓት በኋላ ወደ Sinshaw ይቀየራል።')
    bot.send_message(m.chat.id, '🎰 ጨዋታውን ለመክፈት ከታች ይጫኑ 👇', reply_markup=play_kb('🎮 Play Now'))

  @bot.message_handler(commands=['takeover'])
  def c_takeover(m):
    second_id = bereket_admin_id()
    if not second_id or int(m.from_user.id) != second_id:
      return bot.reply_to(m, '❌ የአድሚን ማረጋገጫ አልተሟላም። ስምና የራስዎን ስልክ በContact ያረጋግጡ።')
    started = int(time.time())
    with get_db() as c:
      c.execute('UPDATE admin_rotation SET start_at=? WHERE id=1', (started,))
    bot.reply_to(m, '✅ የገቢና ወጪ ተራው አሁን ወደ Bereket ተቀይሯል። ከዚህ ሰዓት ጀምሮ 6 ሰዓት ይቆያል። ከዚያ ወደ ዋናው Admin ይመለሳል።')
    if ADMIN_ID:
      try: bot.send_message(int(ADMIN_ID), '🔔 Bereket የ6 ሰዓት የገቢና ወጪ ተራውን ጀምሯል።')
      except Exception: pass

  def dispatch_menu_from_step(m):
    # A menu tap must cancel the currently pending text prompt and run its own action.
    t = (m.text or '').strip()
    routes = {
      '🎮 Play': c_play, '🎮 Play Royal Bingo': c_play,
      '📝 Register': c_reg, '📝 ምዝገባ (Register)': c_reg,
      '👥 Invite': c_inv, '👥 ግብዣ (Invite)': c_inv,
      '💳 Deposit': c_dep, '💰 ገቢ (Deposit)': c_dep,
      '💸 Withdraw': c_wd, '💸 ወጪ (Withdraw)': c_wd,
      '🔄 Transfer': c_transfer,
      '💳 Balance': c_bal, '💳 ባላንስ (Balance)': c_bal,
      '👑 Admin Panel': c_admin, 'Admin': c_admin,
      '📢 Join Channel': c_channel,
      '🎧 Support': c_sup, '🎧 ድጋፍ (Support)': c_sup,
      '📜 Rules': c_rules, '📜 ህግና መመሪያ': c_rules,
    }
    handler = routes.get(t)
    if handler:
      handler(m)
      return True
    return False

  @bot.message_handler(commands=['play'])
  @bot.message_handler(func=lambda m: m.text in ('🎮 Play Royal Bingo', '🎮 Play'))
  def c_play(m): bot.send_message(m.chat.id, '🎮 ጨዋታውን ለመክፈት ከታች ይጫኑ።', reply_markup=play_kb('🎮 Play Royal Bingo'))

  @bot.message_handler(commands=['balance'])
  @bot.message_handler(func=lambda m: m.text in ('💳 ባላንስ (Balance)', '💳 Balance'))
  def c_bal(m):
    u = ensure_user(m.from_user.id, m.from_user.first_name or 'Player')
    bot.send_message(m.chat.id, f'💰 ጠቅላላ ባላንስ: {u["play_wallet"]+u["win_wallet"]:.2f} ETB', reply_markup=play_kb('🎮 Play'))

  @bot.message_handler(commands=['deposit'])
  @bot.message_handler(func=lambda m: m.text in ('💰 ገቢ (Deposit)', '💳 Deposit'))
  def c_dep(m):
    if finance_service_closed_for(m.from_user.id): return bot.send_message(m.chat.id, finance_closed_bot_message())
    kb = IKB(); kb.row(IBtn('📲 TeleBirr', callback_data='dep_TeleBirr'), IBtn('🏦 CBE Birr', callback_data='dep_CBE'))
    bot.send_message(m.chat.id, '💳 የክፍያ አማራጭ ይምረጡ፦', reply_markup=kb)

  @bot.callback_query_handler(func=lambda c: c.data in ('dep_TeleBirr', 'dep_CBE'))
  def cb_dep(call):
    if finance_service_closed_for(call.from_user.id):
      return bot.answer_callback_query(call.id, finance_closed_bot_message(), show_alert=True)
    bot.answer_callback_query(call.id); md = call.data.split('_')[1]
    bot.register_next_step_handler(bot.send_message(call.message.chat.id, f'💵 በ {md} የሚያስገቡትን መጠን ይጻፉ (ዝቅተኛ 20 ETB)፦'), s_dep_amt, md)

  def s_dep_amt(m, md):
    if dispatch_menu_from_step(m): return
    if finance_service_closed_for(m.from_user.id): return bot.reply_to(m, finance_closed_bot_message())
    try:
      amt = float((m.text or '').strip())
      if not math.isfinite(amt) or amt < 20 or amt > 1000000:
        return bot.reply_to(m, '❌ 20 ETB እስከ 1,000,000 ETB ያለ ትክክለኛ መጠን ያስገቡ።')
    except Exception: return bot.reply_to(m, '❌ ትክክለኛ ቁጥር ያስገቡ።')
    pay = active_payment_accounts()
    bot.register_next_step_handler(bot.send_message(m.chat.id, f"🏦 {md}\n👤 {pay['telebirr_name'] if md == 'TeleBirr' else pay['cbe_name']}\n📱 {pay['telebirr_phone'] if md == 'TeleBirr' else pay['cbe_phone']}\n💵 {amt:.2f} ETB\n\nከላኩ በኋላ የSMS Transaction ID ይላኩ 👇"), s_dep_tx, amt, md)

  def s_dep_tx(m, amt, md):
    if dispatch_menu_from_step(m): return
    if finance_service_status()['closed']: return bot.reply_to(m, finance_closed_bot_message())
    tx = (m.text or '').strip()
    if len(tx) < 4 or len(tx) > 120: return bot.reply_to(m, '❌ ትክክለኛ Transaction ID ያስገቡ።')
    try: notify_req('deposit', m.from_user.id, amt, md, f'TX: {tx}', reference=tx)
    except sqlite3.IntegrityError: return bot.reply_to(m, '❌ ይህ Transaction ID ቀድሞ ተጠቅሟል።')
    bot.reply_to(m, f'✅ የ {amt:.2f} ETB ገቢ ጥያቄዎ ለአድሚን ተልኳል!', reply_markup=menu_kb(m.from_user.id))

  @bot.message_handler(commands=['withdraw'])
  @bot.message_handler(func=lambda m: m.text in ('💸 ወጪ (Withdraw)', '💸 Withdraw'))
  def c_wd(m):
    if finance_service_status()['closed']: return bot.send_message(m.chat.id, finance_closed_bot_message())
    kb = IKB(); kb.row(IBtn('📲 TeleBirr', callback_data='wd_TeleBirr'), IBtn('🏦 CBE Birr', callback_data='wd_CBE'))
    bot.send_message(m.chat.id, '💸 ወደ የትኛው አካውንት ማውጣት ይፈልጋሉ?', reply_markup=kb)

  @bot.callback_query_handler(func=lambda c: c.data in ('wd_TeleBirr', 'wd_CBE'))
  def cb_wd(call):
    if finance_service_status()['closed']:
      return bot.answer_callback_query(call.id, finance_closed_bot_message(), show_alert=True)
    bot.answer_callback_query(call.id); md = call.data.split('_')[1]
    bot.register_next_step_handler(bot.send_message(call.message.chat.id, f'💸 በ {md} የሚወጣውን መጠን ይጻፉ (ዝቅተኛ 100 ETB)፦'), s_wd_amt, md)

  def s_wd_amt(m, md):
    if dispatch_menu_from_step(m): return
    if finance_service_status()['closed']: return bot.reply_to(m, finance_closed_bot_message())
    u = ensure_user(m.from_user.id)
    try: amt = float((m.text or '').strip())
    except Exception: return bot.reply_to(m, '❌ ትክክለኛ ቁጥር ያስገቡ።')
    if not math.isfinite(amt) or amt < 100 or amt > 1000000:
      return bot.reply_to(m, '❌ 100–1,000,000 ETB ያለ ትክክለኛ መጠን ያስገቡ።')
    with get_db() as c:
      available = available_wallet(c, m.from_user.id)
    if available < amt:
      return bot.reply_to(m, f'❌ በቂ ያልሆነ ባላንስ። ለወጪ የሚገኘው: {available:.2f} ETB')
    bot.register_next_step_handler(bot.reply_to(m, f'📱 የ {md} ስልክ/አካውንት ቁጥርዎን ያስገቡ፦'), s_wd_acc, amt, md)

  def s_wd_acc(m, amt, md):
    if dispatch_menu_from_step(m): return
    if finance_service_status()['closed']: return bot.reply_to(m, finance_closed_bot_message())
    acc = (m.text or '').strip()
    if len(acc) < 6 or len(acc) > 64:
      return bot.reply_to(m, '❌ ትክክለኛ የስልክ/አካውንት ቁጥር ያስገቡ።')
    try:
      with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        u = c.execute('SELECT name, play_wallet FROM users WHERE uid=?', (m.from_user.id,)).fetchone()
        if not u or not math.isfinite(float(amt)) or amt < 100 or available_wallet(c, m.from_user.id) < amt:
          return bot.reply_to(m, '❌ በቂ ባላንስ የለዎትም ወይም የገንዘብ መጠኑ ትክክል አይደለም!')
        rid = c.execute('INSERT INTO requests (req_type,uid,amount,method,details,status,created_at) VALUES (?,?,?,?,?,?,?)',
                        ('withdraw', m.from_user.id, amt, md, f'PENDING RESERVED | Acc: {acc}', 'pending', int(time.time()))).lastrowid
      aids = finance_admin_ids()
      if bot and aids:
        kb = IKB()
        kb.row(IBtn('✅ ተልኳል', callback_data=f'ap_wd_{rid}'), IBtn('❌ ሰርዝና መልስ', callback_data=f'rj_wd_{rid}'))
        msg = f'🔔 አዲስ የወጪ ጥያቄ!\n\n🆔 Request #{rid}\n👤 {u["name"]}\n🆔 Player ID: {m.from_user.id}\n💵 {amt:.2f} ETB\n🏦 የወጪ ዘዴ: {md}\n📱 የተቀባይ ቁጥር: {acc}'
        for aid in aids:
          try: bot.send_message(aid, msg, reply_markup=kb)
          except Exception as notify_exc:
            print(f'Withdrawal admin notification failed for request #{rid}: {type(notify_exc).__name__}: {notify_exc}')
      bot.reply_to(m, f'✅ የ {amt:.2f} ETB የወጪ ጥያቄዎ ተመዝግቧል።', reply_markup=menu_kb(m.from_user.id))
    except sqlite3.Error:
      bot.reply_to(m, '❌ ጥያቄውን ማስመዝገብ አልተቻለም። እባክዎ እንደገና ይሞክሩ።')

  @bot.callback_query_handler(func=lambda c: c.data.startswith(('adm_', 'ap_', 'rj_')))
  def cb_adm(call):
    global MIN_PLAYERS
    if not is_adm(call.from_user.id): return bot.answer_callback_query(call.id, '⛔ ለአድሚን ብቻ!', show_alert=True)
    d = call.data
    if d.startswith(('ap_', 'rj_')) and not is_finance_admin(call.from_user.id):
      return bot.answer_callback_query(call.id, '⏳ የገቢና ወጪ ማጽደቅ ተራው አሁን የሌላ አድሚን ነው።', show_alert=True)
    bot.answer_callback_query(call.id)
    if d == 'adm_switch_finance':
      try:
        t = switch_finance_turn()
        bot.send_message(call.message.chat.id, f'✅ የገቢና ወጪ ተራ ተቀይሯል።\\n🟢 አሁን ተረኛ: {t["active_name"]}\\n🔜 ቀጣይ: {t["next_name"]}\\n⏳ አዲሱ 6 ሰዓት ቆጠራ ተጀምሯል።', reply_markup=adm_kb())
      except ValueError as exc:
        bot.send_message(call.message.chat.id, f'❌ {exc}')
    elif d == 'adm_timer':
      t = finance_rotation_status()
      if t['configured']:
        hh, rem = divmod(t['remaining'], 3600)
        mm, ss = divmod(rem, 60)
        bot.send_message(call.message.chat.id, f'⏱️ የገቢና ወጪ ማጽደቅ ተራ\n\n🟢 አሁን ተረኛ: {t["active_name"]}\n🔜 ቀጣይ: {t["next_name"]}\n⏳ የሚቀረው: {hh:02d}:{mm:02d}:{ss:02d}')
      else:
        bot.send_message(call.message.chat.id, '⚠️ የ6 ሰዓት ተራ ስርዓቱ ገና አልተዋቀረም።')
    elif d == 'adm_stats':
      with get_db() as c:
        uc = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        pw = c.execute('SELECT COALESCE(SUM(play_wallet+win_wallet),0) FROM users').fetchone()[0]
        pn = c.execute("SELECT COUNT(*) FROM requests WHERE status='pending'").fetchone()[0]
        hs = c.execute('SELECT total_commission,total_rounds FROM house_stats WHERE id=1').fetchone()
      with room_lock: lv = ' | '.join(f'{k}ብር:{n_players(r)}ሰው({r["phase"]})' for k, r in rooms.items())
      bot.send_message(call.message.chat.id, f'📊 ሪፖርት\n👥 ተጫዋቾች: {uc}\n💰 ጠቅላላ Wallet: {pw:.2f} ETB\n💰 ትርፍ(20%): {hs["total_commission"]:.2f} ETB\n🎲 ዙሮች: {hs["total_rounds"]}\n⏳ ጥያቄዎች: {pn}\n🔴 {lv}')
    elif d == 'adm_users':
      with get_db() as c: rows = c.execute('SELECT * FROM users ORDER BY created_at DESC LIMIT 20').fetchall()
      bot.send_message(call.message.chat.id, '\n'.join(f'• {r["name"]} (Player ID: {r["phone"] or "Not registered"}) | Wallet:{(r["play_wallet"]+r["win_wallet"]):.1f} ETB' for r in rows) or 'ምንም ተጫዋች የለም።', parse_mode='Markdown')
    elif d in ('adm_add', 'adm_sub'):
      act = 'add' if d == 'adm_add' else 'sub'
      bot.register_next_step_handler(bot.send_message(call.message.chat.id, '✏️ Player Phone ID እና መጠን ይላኩ (ምሳሌ፦ `+2519XXXXXXXX 100`)' , parse_mode='Markdown'), s_adm_bal, act)
    elif d == 'adm_minp':
      bot.send_message(call.message.chat.id, f'⚙️ የሁሉም የጨዋታ ክፍሎች ዝቅተኛ ተጫዋች ብዛት: {MIN_PLAYERS}\\n➕ ለመጨመር ጨምርን፣ ➖ ለመቀነስ ቀንስን ይጫኑ።', reply_markup=adm_kb())
    elif d in ('adm_minp_inc', 'adm_minp_dec'):
      change = 1 if d == 'adm_minp_inc' else -1
      updated = set_min_players(MIN_PLAYERS + change)
      try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=adm_kb())
      except Exception:
        pass
      bot.edit_message_text(f'⚙️ ዝቅተኛ ተጫዋች: {updated}\\n\\n➖ ቀንስ ወይም ➕ ጨምር በመጫን ቁጥሩን ቀጥታ ይቀይሩ።\\n🎮 በ10/20/50 ETB ክፍሎች ሁሉ ይሠራል።', call.message.chat.id, call.message.message_id, reply_markup=adm_kb())
    elif d == 'adm_backup':
      bot.send_message(call.message.chat.id, '💾 Neon PostgreSQL ዳታቤዝ በRender local file አይደለም። Backup ከNeon Console ወይም pg_dump መውሰድ ያስፈልጋል።')
    elif d == 'adm_bcast':
      bot.register_next_step_handler(bot.send_message(call.message.chat.id, '📢 ለሁሉም የሚላከውን መልዕክት ይጻፉ፦'), s_bcast)
    elif d.startswith(('ap_', 'rj_')):
      act, rt, rid = d.split('_')
      with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        rq = c.execute('SELECT * FROM requests WHERE id=?', (int(rid),)).fetchone()
        if not rq or rq['status'] != 'pending': return bot.send_message(call.message.chat.id, '⚠️ ቀድሞ ተወስኗል።')
        uid, amt = rq['uid'], rq['amount']
        if rt == 'dep' and act == 'ap':
          c.execute("UPDATE requests SET status='approved' WHERE id=?", (int(rid),))
          c.execute('UPDATE users SET play_wallet=play_wallet+? WHERE uid=?', (amt, uid))
          add_tx(c, uid, '💰', f'ገቢ ጸድቋል ({rq["method"]})', amt)
          u = c.execute('SELECT referred_by FROM users WHERE uid=?', (uid,)).fetchone()
          if u and u['referred_by']:
            c.execute('UPDATE users SET play_wallet=play_wallet+15 WHERE uid=?', (u['referred_by'],))
            c.execute('UPDATE users SET referred_by=NULL WHERE uid=?', (uid,))
            add_tx(c, u['referred_by'], '🎁', 'የግብዣ ሽልማት', 15.0)
          bot.edit_message_text(call.message.text + f'\n\n✅ ጸድቋል (+{amt:.2f} ETB)', call.message.chat.id, call.message.message_id)
          try: bot.send_message(uid, f'🎉 የ {amt:.2f} ETB ገቢዎ ጸድቋል!')
          except Exception: pass
        elif rt == 'dep':
          c.execute("UPDATE requests SET status='rejected' WHERE id=?", (int(rid),))
          bot.edit_message_text(call.message.text + '\n\n❌ ተሰርዟል', call.message.chat.id, call.message.message_id)
        elif rt == 'wd' and act == 'ap':
          if str(rq['details'] or '').startswith('PENDING RESERVED |'):
            cur = c.execute('UPDATE users SET play_wallet=play_wallet-? WHERE uid=? AND play_wallet>=?', (amt, uid, amt))
            if cur.rowcount != 1:
              return bot.send_message(call.message.chat.id, f'❌ Request #{rid}: በተጫዋቹ ባላንስ ላይ ችግር አለ፤ ክፍያውን አትመዝግቡ።')
          c.execute("UPDATE requests SET status='paid' WHERE id=?", (int(rid),))
          add_tx(c, uid, '💸', f'ወጪ ተልኳል ({rq["method"]})', -amt)
          bot.edit_message_text(call.message.text + f'\n\n✅ ተልኳል ({amt:.2f} ETB)', call.message.chat.id, call.message.message_id)
          try: bot.send_message(uid, f'✅ የ {amt:.2f} ETB ወጪዎ በ {rq["method"]} ተልኳል!')
          except Exception: pass
        else:
          c.execute("UPDATE requests SET status='rejected' WHERE id=?", (int(rid),))
          bot.edit_message_text(call.message.text + '\n\n❌ ተሰርዞ ተመልሷል', call.message.chat.id, call.message.message_id)
          try: bot.send_message(uid, f'❌ የ {amt:.2f} ETB ወጪ ጥያቄዎ ተሰርዟል። ገንዘቡ በWallet ውስጥ አሁንም አለ።')
          except Exception: pass

  def s_adm_bal(m, act):
    if dispatch_menu_from_step(m): return
    if not is_adm(m.from_user.id): return
    try:
      u_s, a_s = (m.text or '').strip().split()
      player_id, amt = u_s.strip(), float(a_s)
      if not player_id or len(player_id) > 32 or not math.isfinite(amt) or amt <= 0 or amt > 1000000: raise ValueError()
      with get_db() as c:
        row = c.execute('SELECT uid,phone FROM users WHERE phone=?', (player_id,)).fetchone()
        if not row and player_id.isdigit():
          row = c.execute('SELECT uid,phone FROM users WHERE uid=?', (int(player_id),)).fetchone()
        if not row: raise ValueError('Player ID not found')
        uid = row['uid']
        if act == 'add': c.execute('UPDATE users SET play_wallet=play_wallet+? WHERE uid=?', (amt, uid)); add_tx(c, uid, '🎁', 'አድሚን ጨምሯል', amt)
        else: c.execute('UPDATE users SET play_wallet=MAX(0,play_wallet-?) WHERE uid=?', (amt, uid)); add_tx(c, uid, '➖', 'አድሚን ቀንሷል', -amt)
        pw = c.execute('SELECT play_wallet FROM users WHERE uid=?', (uid,)).fetchone()[0]
      bot.reply_to(m, f'✅ Player ID `{row["phone"] or player_id}` ተስተካክሏል! Play: {pw:.2f} ETB', parse_mode='Markdown')
    except Exception: bot.reply_to(m, '❌ ምሳሌ፦ `+2519XXXXXXXX 100`', parse_mode='Markdown')

  def s_bcast(m):
    if dispatch_menu_from_step(m): return
    if not is_adm(m.from_user.id): return
    with get_db() as c: uids = [r[0] for r in c.execute('SELECT uid FROM users').fetchall()]
    keyboard = IKB()
    keyboard.add(IBtn('🎮 Play Now', web_app=WebAppInfo(url=APP_URL)))
    sent, failed = 0, 0
    caption = (m.caption or '').strip() if m.content_type == 'photo' else (m.text or '').strip()
    if not caption:
      caption = '🎮 ጨዋታውን ለመክፈት Play Now ይጫኑ።'
    photo_bytes = None
    if m.content_type == 'photo':
      try:
        file_info = bot.get_file(m.photo[-1].file_id)
        photo_bytes = bot.download_file(file_info.file_path)
      except Exception as exc:
        return bot.reply_to(m, f'❌ ፎቶውን ማውረድ አልተቻለም: {type(exc).__name__}: {exc}')
    for uid in uids:
      try:
        if photo_bytes:
          photo = io.BytesIO(photo_bytes)
          photo.name = 'announcement.jpg'
          bot.send_photo(uid, photo, caption=f'📢 ማስታወቂያ\n\n{caption}', reply_markup=keyboard)
        else:
          bot.send_message(uid, f'📢 ማስታወቂያ\n\n{caption}', reply_markup=keyboard)
        sent += 1
        time.sleep(0.05)
      except Exception as exc:
        failed += 1
        print(f'Bot admin broadcast failed for {uid}: {type(exc).__name__}: {exc}')
    bot.reply_to(m, f'✅ ተሳክቷል: {sent} | አልተሳካም: {failed} | ጠቅላላ: {len(uids)}')

  @bot.message_handler(func=lambda m: m.text in ('👥 ግብዣ (Invite)', '👥 Invite'))
  def c_inv(m):
    me = bot.get_me()
    bot.send_message(m.chat.id, f'🎁 ለእያንዳንዱ ስኬታማ ግብዣ 15 ETB ይሸለሙ!\n🔗 https://t.me/{me.username}?start={m.from_user.id}')

  @bot.message_handler(func=lambda m: m.text in ('📝 ምዝገባ (Register)', '📝 Register'))
  def c_reg(m):
    kb = RKB(resize_keyboard=True, one_time_keyboard=True); kb.add(KBtn('📱 ስልክ ቁጥር አጋራ', request_contact=True))
    bot.send_message(m.chat.id, '📱 ስልክ ቁጥርዎን ያጋሩ 👇', reply_markup=kb)

  @bot.message_handler(func=lambda m: m.text == '🔄 Transfer')
  def c_transfer(m):
    bot.register_next_step_handler(bot.send_message(m.chat.id, '🔄 ወደ ሚልኩለት ተጫዋች የተመዘገበ ስልክ ቁጥር (09...) ያስገቡ፦'), s_transfer_phone)

  def s_transfer_phone(m):
    if dispatch_menu_from_step(m): return
    phone = normalize_phone((m.text or '').strip())
    if not phone or len(re.sub(r'\\D', '', phone)) != 12:
      return bot.register_next_step_handler(bot.reply_to(m, '❌ ትክክለኛ የስልክ ቁጥር (09...) ያስገቡ።'), s_transfer_phone)
    with get_db() as c:
      matches = c.execute('SELECT uid, name FROM users WHERE phone=?', (phone,)).fetchall()
    if len(matches) != 1:
      return bot.reply_to(m, '❌ ይህ ቁጥር የተመዘገበ ተጫዋች አልተገኘም ወይም ከአንድ ተጫዋች ጋር ተያይዟል።', reply_markup=menu_kb(m.from_user.id))
    if int(matches[0]['uid']) == int(m.from_user.id):
      return bot.register_next_step_handler(bot.reply_to(m, '❌ ወደ ራስዎ መላክ አይችሉም። የሌላ ተጫዋች ቁጥር ያስገቡ፦'), s_transfer_phone)
    bot.register_next_step_handler(bot.send_message(m.chat.id, f'👤 ተቀባይ: {matches[0]["name"]}\\n📱 {phone}\\n\\n💵 የሚልኩትን መጠን ETB ያስገቡ (ዝቅተኛ 1 ETB)፦'), s_transfer_amount, phone, matches[0]['name'])

  def s_transfer_amount(m, phone, recipient_name):
    if dispatch_menu_from_step(m): return
    try:
      amt = round(float((m.text or '').strip()), 2)
      if not math.isfinite(amt) or amt < 1 or amt > 100000: raise ValueError()
    except Exception:
      return bot.register_next_step_handler(bot.reply_to(m, '❌ 1–100,000 ETB መካከል ትክክለኛ መጠን ያስገቡ፦'), s_transfer_amount, phone, recipient_name)
    with get_db() as c:
      available = available_wallet(c, m.from_user.id)
    if available < amt:
      return bot.reply_to(m, f'❌ በቂ ባላንስ የለዎትም። የሚገኘው {available:.2f} ETB ነው።', reply_markup=menu_kb(m.from_user.id))
    bot.register_next_step_handler(bot.send_message(m.chat.id, f'⚠️ እባክዎ ያረጋግጡ\\n👤 ተቀባይ: {recipient_name}\\n📱 {phone}\\n💵 መጠን: {amt:.2f} ETB\\n\\nለመላክ YES ይጻፉ፤ ለመሰረዝ NO ይጻፉ።'), s_transfer_confirm, phone, amt, recipient_name)

  def s_transfer_confirm(m, phone, amt, recipient_name):
    if dispatch_menu_from_step(m): return
    if (m.text or '').strip().upper() != 'YES':
      return bot.reply_to(m, 'ℹ️ ማስተላለፉ ተሰርዟል።', reply_markup=menu_kb(m.from_user.id))
    try:
      with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('SELECT uid FROM users WHERE uid=? FOR UPDATE', (m.from_user.id,))
        matches = c.execute('SELECT uid, name FROM users WHERE phone=?', (phone,)).fetchall()
        if len(matches) != 1 or int(matches[0]['uid']) == int(m.from_user.id):
          return bot.reply_to(m, '❌ የተቀባዩ መረጃ ተቀይሯል። እንደገና ይጀምሩ።', reply_markup=menu_kb(m.from_user.id))
        receiver = matches[0]
        available = available_wallet(c, m.from_user.id)
        if available < amt:
          return bot.reply_to(m, f'❌ በቂ ባላንስ የለዎትም። የሚገኘው {available:.2f} ETB ነው።', reply_markup=menu_kb(m.from_user.id))
        sender = c.execute('SELECT name FROM users WHERE uid=?', (m.from_user.id,)).fetchone()
        c.execute('UPDATE users SET play_wallet=COALESCE(play_wallet,0)-? WHERE uid=?', (amt, m.from_user.id))
        c.execute('UPDATE users SET play_wallet=COALESCE(play_wallet,0)+? WHERE uid=?', (amt, receiver['uid']))
        add_tx(c, m.from_user.id, '↗️', f'ለ {receiver["name"]} የተላከ ብር', -amt)
        add_tx(c, receiver['uid'], '↙️', f'ከ {sender["name"]} የተቀበለ ብር', amt)
      bot.reply_to(m, f'✅ {amt:.2f} ETB ለ {recipient_name} ተልኳል።', reply_markup=menu_kb(m.from_user.id))
      try: bot.send_message(int(receiver['uid']), f'🎉 {sender["name"]} ከእርስዎ ጋር {amt:.2f} ETB አጋርቷል።')
      except Exception: pass
    except Exception as exc:
      print('Player transfer failed:', type(exc).__name__, str(exc))
      bot.reply_to(m, '❌ ማስተላለፉ አልተሳካም። እባክዎ እንደገና ይሞክሩ።', reply_markup=menu_kb(m.from_user.id))

  @bot.message_handler(func=lambda m: m.text == '📢 Join Channel')
  def c_channel(m):
    channel_url = os.environ.get('CHANNEL_URL', 'https://t.me/royalbingo99').strip()
    if channel_url.startswith(('https://t.me/', 'http://t.me/')):
      kb = IKB(); kb.add(IBtn('📢 Join Royal Bingo Channel', url=channel_url))
      bot.send_message(m.chat.id, '📢 የRoyal Bingo ቻናላችንን ለመቀላቀል ከታች ይጫኑ።', reply_markup=kb)
    else:
      bot.send_message(m.chat.id, '📢 የRoyal Bingo Channel ሊንክ ገና አልተዘጋጀም።')

  @bot.message_handler(func=lambda m: m.text in ('🎧 ድጋፍ (Support)', '🎧 Support'))
  def c_sup(m): bot.send_message(m.chat.id, f'🎧 ድጋፍ፦ {SUPPORT}')

  @bot.message_handler(func=lambda m: m.text in ('📜 ህግና መመሪያ', '📜 Rules'))
  def c_rules(m): bot.send_message(m.chat.id, '📜 ህግና መመሪያ\n• ቢያንስ 2 ተጫዋቾች ካርድ ሲገዙ ይጀምራል።\n• ካርድ: 10/20/50 ብር (እስከ 4)።\n• ደራሽ: 80% ለአሸናፊው።\n• ዝቅተኛ ወጪ: 100 ETB።')

def run_bot():
  if not bot: return print('WARNING: BOT_TOKEN not set; bot polling skipped.')
  # Render briefly runs old and new instances during deploys. Wait for the old
  # instance to shut down before claiming Telegram's single getUpdates stream.
  print('Telegram bot polling will start after a 20-second deploy handover delay.')
  time.sleep(20)
  while True:
    try: bot.infinity_polling(timeout=20, long_polling_timeout=15)
    except Exception as e: print('Bot error:', e); time.sleep(5)

app = FastAPI(title='ROYAL BINGO Live')
allowed_origins = [x.strip().rstrip('/') for x in os.environ.get('CORS_ORIGINS', APP_URL).split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'X-Tg-Init-Data'])
if os.path.isdir('sounds'): app.mount('/sounds', StaticFiles(directory='sounds'), name='sounds')
if os.path.isdir('assets'): app.mount('/assets', StaticFiles(directory='assets'), name='assets')

@app.on_event('startup')
def on_startup():
  init_db()
  load_game_settings()
  load_rooms()
  threading.Thread(target=game_loop, daemon=True).start()
  threading.Thread(target=run_bot, daemon=True).start()

@app.get('/health')
def health(): return {'ok': True, 'time': int(time.time())}

@app.head('/')
def head_root(): return Response(status_code=200)

@app.get('/', response_class=HTMLResponse)
def serve_app():
  if not os.path.exists('index.html'):
    return HTMLResponse('index.html missing', 404)
  return FileResponse('index.html', headers={'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0', 'Pragma': 'no-cache', 'Expires': '0'})

@app.get('/api/state')
def api_state(stake: int = 10, x_tg_init_data: str = Header(default='')):
  u = auth_user(x_tg_init_data)
  if stake not in rooms: raise HTTPException(400, 'ልክ ያልሆነ የጨዋታ ክፍል!')
  r = rooms[stake]
  with room_lock:
    my_c = sorted([k for k, v in r['cards'].items() if v['uid'] == u['uid']])
    tk_c = sorted([k for k, v in r['cards'].items() if v['uid'] != u['uid']])
    snap = {'stake': r['stake'], 'phase': r['phase'], 'countdown': r['countdown'], 'players': n_players(r),
            'min_players': min_players_for(stake), 'total_cards': len(r['cards']), 'derash': derash(r),
            'taken_cards': tk_c, 'my_cards': my_c, 'my_boards': {str(n): r['cards'][n]['grid'] for n in my_c},
            'called': r['called'], 'winner': r['winner'], 'winner_left': max(0, int(r['win_until'] - time.time())) if r['phase'] == 'WINNER' else 0}
  with get_db() as c:
    txs = [dict(x) for x in c.execute('SELECT icon,title,amount,created_at, NULL AS status FROM transactions WHERE uid=? ORDER BY id DESC LIMIT 25', (u['uid'],)).fetchall()]
    pending = [dict(x) for x in c.execute("SELECT '⏳' AS icon, 'የወጪ ጥያቄ ('||method||') - PENDING' AS title, -amount AS amount, created_at, status FROM requests WHERE uid=? AND req_type='withdraw' AND status='pending' AND details LIKE 'PENDING RESERVED |%' ORDER BY id DESC LIMIT 25", (u['uid'],)).fetchall()]
    txs = sorted(txs + pending, key=lambda x: x['created_at'], reverse=True)[:25]
    top = [dict(x) for x in c.execute('SELECT name,total_won FROM users WHERE total_won>0 ORDER BY total_won DESC LIMIT 10').fetchall()]
  admin_view = is_adm(u['uid'])
  return {'finance_service': finance_service_status(), 'user': {'player_id': u['phone'], 'name': u['name'], 'balance': round(u['play_wallet'] + u['win_wallet'],2), 'play_wallet': round(u['play_wallet'] + u['win_wallet'],2), 'win_wallet': 0.0, 'matches_played': u['matches_played'], 'wins': u['wins']}, 'is_admin': admin_view,
          'finance_rotation': finance_rotation_status() if admin_view else None,
          'config': {**active_payment_accounts(), 'support': SUPPORT}, 'room': snap, 'history': txs, 'rank': top}

@app.post('/api/buy_card')
async def api_buy(req: Request, x_tg_init_data: str = Header(default='')):
  u, b = auth_user(x_tg_init_data), await req.json()
  try: st, cno = int(b.get('stake', 10)), int(b.get('card_no', 0))
  except (TypeError, ValueError): raise HTTPException(400, 'ልክ ያልሆነ ካርድ!')
  if st not in rooms or not (1 <= cno <= 400): raise HTTPException(400, 'ልክ ያልሆነ ካርድ!')
  with room_lock:
    r = rooms[st]
    if r['phase'] not in ('WAITING', 'COUNTDOWN'): raise HTTPException(400, 'ጨዋታው ተጀምሯል!')
    if cno in r['cards']:
      if r['cards'][cno]['uid'] == u['uid']:
        with get_db() as c:
          c.execute('BEGIN IMMEDIATE')
          c.execute('UPDATE users SET play_wallet=play_wallet+? WHERE uid=?', (st, u['uid']))
          add_tx(c, u['uid'], '↩️', f'ካርድ #{cno} ተሰርዟል', st)
          del r['cards'][cno]
          save_room(r, c)
        return {'ok': True, 'action': 'removed'}
      raise HTTPException(400, f'ካርድ #{cno} ተይዟል!')
    if sum(1 for v in r['cards'].values() if v['uid'] == u['uid']) >= 4: raise HTTPException(400, 'እስከ 4 ካርድ ብቻ!')
    with get_db() as c:
      c.execute('BEGIN IMMEDIATE')
      wallet = c.execute('SELECT play_wallet FROM users WHERE uid=?', (u['uid'],)).fetchone()
      if not wallet or available_wallet(c, u['uid']) < st: raise HTTPException(400, 'በቂ Play Wallet የለዎትም! ገቢ ያድርጉ።')
      c.execute('UPDATE users SET play_wallet=play_wallet-? WHERE uid=?', (st, u['uid'])); add_tx(c, u['uid'], '🎴', f'ካርድ #{cno} ({st} ብር)', -st)
      r['cards'][cno] = {'uid': u['uid'], 'name': u['name'], 'grid': gen_card(cno)}
      save_room(r, c)
    return {'ok': True, 'action': 'bought'}

@app.post('/api/claim_bingo')
async def api_claim(req: Request, x_tg_init_data: str = Header(default='')):
  u, b = auth_user(x_tg_init_data), await req.json()
  try: st, cno = int(b.get('stake', 10)), int(b.get('card_no', 0))
  except (TypeError, ValueError): raise HTTPException(400, 'ልክ ያልሆነ ክፍል/ካርድ!')
  if st not in rooms or not (1 <= cno <= 400): raise HTTPException(400, 'ልክ ያልሆነ ክፍል/ካርድ!')
  with room_lock:
    r = rooms[st]
    if r['phase'] != 'PLAYING': raise HTTPException(400, 'ጨዋታው አልተጀመረም!')
    own = r['cards'].get(cno)
    if not own or own['uid'] != u['uid']: raise HTTPException(403, 'ይህ ካርድ የእርስዎ አይደለም!')
    wl = check_win(own['grid'], set(r['called']))
    if not wl: raise HTTPException(400, 'ገና BINGO አልተሟላም!')
    finish_round(r, u['uid'], u['name'], cno, wl)
    return {'ok': True, 'prize': r['winner']['prize']}

@app.post('/api/transfer')
async def api_transfer(req: Request, x_tg_init_data: str = Header(default='')):
  u = auth_user(x_tg_init_data)
  body = await req.json()
  phone = normalize_phone(body.get('phone'))
  try:
    amt = round(float(body.get('amount', 0)), 2)
  except (TypeError, ValueError):
    raise HTTPException(400, 'የሚላከው ብር መጠን ትክክል አይደለም።')
  if not phone or len(re.sub(r'\D', '', phone)) != 12:
    raise HTTPException(400, 'የተቀባዩን የተመዘገበ ስልክ ቁጥር በትክክል ያስገቡ።')
  if not math.isfinite(amt) or amt < 1 or amt > 100000:
    raise HTTPException(400, 'ዝቅተኛው 1 ETB ነው፤ የብር መጠኑን ያረጋግጡ።')
  with get_db() as c:
    c.execute('BEGIN IMMEDIATE')
    c.execute('SELECT uid FROM users WHERE uid=? FOR UPDATE', (u['uid'],))
    matches = c.execute('SELECT uid, name FROM users WHERE phone=?', (phone,)).fetchall()
    if not matches:
      raise HTTPException(404, 'ይህ ስልክ ቁጥር በRoyal Bingo አልተመዘገበም።')
    if len(matches) != 1:
      raise HTTPException(409, 'ይህ ስልክ ቁጥር ከአንድ ተጫዋች ጋር ተያይዟል፤ ድጋፍን ያነጋግሩ።')
    receiver = matches[0]
    if int(receiver['uid']) == int(u['uid']):
      raise HTTPException(400, 'ገንዘብን ወደ ራስዎ መላክ አይችሉም።')
    available = available_wallet(c, u['uid'])
    if available < amt:
      raise HTTPException(400, f'በቂ ባላንስ የለዎትም። የሚገኘው {available:.2f} ETB ነው።')
    c.execute('UPDATE users SET play_wallet=COALESCE(play_wallet,0)-? WHERE uid=?', (amt, u['uid']))
    c.execute('UPDATE users SET play_wallet=COALESCE(play_wallet,0)+? WHERE uid=?', (amt, receiver['uid']))
    add_tx(c, u['uid'], '↗️', f'ለ {receiver["name"]} የተላከ ብር', -amt)
    add_tx(c, receiver['uid'], '↙️', f'ከ {u["name"]} የተቀበለ ብር', amt)
  return {'ok': True, 'amount': amt, 'recipient': receiver['name']}

@app.post('/api/deposit')
async def api_dep(req: Request, x_tg_init_data: str = Header(default='')):
  u = auth_user(x_tg_init_data)
  ensure_finance_service_open(u['uid'])
  b = await req.json()
  try: amt = float(b.get('amount', 0))
  except (TypeError, ValueError): raise HTTPException(400, 'የገንዘብ መጠኑ ትክክል አይደለም!')
  tx = str(b.get('tx_id', '')).strip()
  method = str(b.get('method', 'TeleBirr')).strip()
  if method not in ('TeleBirr', 'CBE Birr'):
    raise HTTPException(400, 'TeleBirr ወይም CBE Birr ይምረጡ!')
  if not math.isfinite(amt) or amt < 20 or amt > 1000000 or len(tx) < 4 or len(tx) > 120:
    raise HTTPException(400, 'ዝቅተኛ 20 ETB፣ ትክክለኛ Transaction ID እና ተገቢ መጠን ያስገቡ!')
  try: notify_req('deposit', u['uid'], amt, method, f'App TX: {tx}', reference=tx)
  except sqlite3.IntegrityError: raise HTTPException(409, 'ይህ Transaction ID ቀድሞ ተጠቅሟል!')
  return {'ok': True}


@app.post('/api/admin/finance/switch')
async def api_admin_finance_switch(x_tg_init_data: str = Header(default='')):
  u = auth_user(x_tg_init_data)
  if not is_adm(u['uid']):
    raise HTTPException(403, 'ይህ ክፍል ለአድሚን ብቻ ነው!')
  try:
    return {'ok': True, 'finance_rotation': switch_finance_turn()}
  except ValueError as exc:
    raise HTTPException(409, str(exc))

@app.post('/api/admin/broadcast')
async def api_admin_broadcast(req: Request, x_tg_init_data: str = Header(default='')):
  u = auth_user(x_tg_init_data)
  if not is_adm(u['uid']):
    raise HTTPException(403, 'ይህ ክፍል ለአድሚን ብቻ ነው!')
  if not bot:
    raise HTTPException(503, 'Telegram Bot አልተገናኘም!')
  b = await req.json()
  raw_message = b.get('message') if isinstance(b, dict) else None
  message = raw_message.strip() if isinstance(raw_message, str) else ''
  image_data = b.get('image') if isinstance(b, dict) else None
  image_bytes, image_name = None, 'announcement.jpg'
  if not message or message.lower() == 'none' or len(message) > 4000:
    raise HTTPException(400, 'መልእክቱ ባዶ ነው፤ ከ1 እስከ 4000 ቁምፊ ይጻፉ!')
  if image_data:
    if not isinstance(image_data, str) or ',' not in image_data:
      raise HTTPException(400, 'የምስሉ ፋይል ትክክል አይደለም!')
    image_header, encoded = image_data.split(',', 1)
    if 'image/jpeg' in image_header:
      image_name = 'announcement.jpg'
    elif 'image/png' in image_header:
      image_name = 'announcement.png'
    elif 'image/webp' in image_header:
      image_name = 'announcement.webp'
    else:
      raise HTTPException(400, 'JPG, PNG ወይም WEBP ምስል ብቻ ይጠቀሙ!')
    try:
      image_bytes = base64.b64decode(encoded, validate=True)
    except Exception:
      raise HTTPException(400, 'የምስሉ ፋይል ማንበብ አልተቻለም!')
    if not image_bytes or len(image_bytes) > 8 * 1024 * 1024:
      raise HTTPException(413, 'ምስሉ ከ8MB በታች መሆን አለበት!')
    if len(message) > 900:
      raise HTTPException(400, 'ምስል ሲያያዝ መልእክቱ ከ900 ቁምፊ መብለጥ የለበትም!')
  with get_db() as c:
    uids = [row[0] for row in c.execute('SELECT uid FROM users ORDER BY created_at').fetchall()]
  keyboard = IKB()
  keyboard.add(IBtn('🎮 Play Now', web_app=WebAppInfo(url=APP_URL)))
  sent, failed = 0, 0
  for uid in uids:
    try:
      if image_bytes:
        photo = io.BytesIO(image_bytes)
        photo.name = image_name
        try:
          bot.send_photo(uid, photo, caption=f'📢 ማስታወቂያ\n\n{message}')
          time.sleep(0.15)
          bot.send_message(uid, '👇 ጨዋታውን ለመክፈት ከታች ይጫኑ፦', reply_markup=keyboard)
        except Exception as photo_exc:
          print(f'Photo broadcast failed for {uid}: {type(photo_exc).__name__}: {photo_exc}')
          # Always deliver the text announcement with the Play Now button
          # even when Telegram cannot accept the attached image.
          bot.send_message(uid, f'📢 ማስታወቂያ\n\n{message}', reply_markup=keyboard)
      else:
        bot.send_message(uid, f'📢 ማስታወቂያ\n\n{message}', reply_markup=keyboard)
      sent += 1
      time.sleep(0.05)
    except Exception as exc:
      failed += 1
      print(f'Broadcast failed for {uid}: {type(exc).__name__}: {exc}')
  return {'ok': True, 'sent': sent, 'failed': failed, 'total': len(uids)}

@app.post('/api/withdraw')
async def api_wd(req: Request, x_tg_init_data: str = Header(default='')):
  u = auth_user(x_tg_init_data)
  ensure_finance_service_open(u['uid'])
  b = await req.json()
  try: amt = float(b.get('amount', 0))
  except (TypeError, ValueError): raise HTTPException(400, 'የገንዘብ መጠኑ ትክክል አይደለም!')
  raw_phone = str(b.get('phone', '')).strip()
  # Accept Ethiopian mobile numbers in local (09XXXXXXXX) or international
  # (+2519XXXXXXXX / 2519XXXXXXXX) format; store a consistent local number.
  phone_digits = ''.join(ch for ch in raw_phone if ch.isdigit())
  if phone_digits.startswith('251') and len(phone_digits) == 12:
    ph = '0' + phone_digits[3:]
  elif len(phone_digits) == 10 and phone_digits.startswith('09'):
    ph = phone_digits
  else:
    ph = ''
  method = str(b.get('method', 'TeleBirr')).strip()
  if method not in ('TeleBirr', 'CBE Birr'):
    raise HTTPException(400, 'TeleBirr ወይም CBE Birr ይምረጡ!')
  if not math.isfinite(amt) or amt < 100 or amt > 1000000 or not ph:
    raise HTTPException(400, 'ዝቅተኛ 100 ETB እና ትክክለኛ 09XXXXXXXX ስልክ ቁጥር ያስገቡ!')
  with get_db() as c:
    c.execute('BEGIN IMMEDIATE')
    if available_wallet(c, u['uid']) < amt: raise HTTPException(400, 'በቂ ያልሆነ ባላንስ አለዎት!')
    rid = c.execute('INSERT INTO requests (req_type,uid,amount,method,details,status,created_at) VALUES (?,?,?,?,?,?,?)',
                    ('withdraw', u['uid'], amt, method, f'PENDING RESERVED | App Account: {ph}', 'pending', int(time.time()))).lastrowid
  try:
    # Send the request only to the admin whose finance turn is currently active.
    aids = finance_admin_ids()
    if bot and aids:
      kb = IKB()
      kb.row(IBtn('✅ ተልኳል', callback_data=f'ap_wd_{rid}'), IBtn('❌ ሰርዝና መልስ', callback_data=f'rj_wd_{rid}'))
      player_phone = u.get('phone') or 'Not registered'
      msg = f'🔔 አዲስ የወጪ ጥያቄ!\\n\\n🆔 Request #{rid}\\n👤 {u["name"]}\\n🆔 Player ID: {player_phone}\\n💵 {amt:.2f} ETB\\n🏦 የወጪ ዘዴ: {method}\\n📱 የተቀባይ ቁጥር: {ph}\\n⏳ ሁኔታ: PENDING'
      for aid in aids:
        try:
          bot.send_message(aid, msg, reply_markup=kb)
        except Exception:
          pass
  except Exception:
    pass
  return {'ok': True}
