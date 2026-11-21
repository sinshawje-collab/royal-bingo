import hashlib
import hmac
import json
import os
import random
import sqlite3
import threading
import time
import urllib.parse
from contextlib import contextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import telebot
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

# ================= ENVIRONMENT CONFIG =================
TOKEN = os.environ.get('BOT_TOKEN', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0') or '0')
APP_URL = os.environ.get('APP_URL', '').strip().rstrip('/')
SUPPORT_USERNAME = os.environ.get('SUPPORT_USERNAME', '@royal_bingo_99_Support')
TELEBIRR_NAME = os.environ.get('TELEBIRR_NAME', 'SINSHAW JEMANEH ESHETE')
TELEBIRR_PHONE = os.environ.get('TELEBIRR_PHONE', '0941882333')
DB_PATH = os.environ.get('DB_PATH', 'royal_bingo.db')
HOUSE_CUT = 0.20  # 20% ኮሚሽን፣ 80% ደራሽ ለአሸናፊ
# ======================================================

db_lock = threading.RLock()
room_lock = threading.RLock()

MIN_PLAYERS = int(os.environ.get('MIN_PLAYERS', '2'))
COUNTDOWN_SECONDS = 25
CALL_INTERVAL = 3.0
WINNER_DISPLAY_SECONDS = 10


@contextmanager
def get_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            uid INTEGER PRIMARY KEY,
            name TEXT DEFAULT 'Player',
            phone TEXT,
            play_wallet REAL DEFAULT 0.0,
            win_wallet REAL DEFAULT 0.0,
            matches_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            total_won REAL DEFAULT 0.0,
            referred_by INTEGER,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            icon TEXT,
            title TEXT,
            amount REAL,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            req_type TEXT,
            uid INTEGER,
            amount REAL,
            method TEXT,
            details TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS house_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_commission REAL DEFAULT 0.0,
            total_rounds INTEGER DEFAULT 0
        );
        INSERT OR IGNORE INTO house_stats (id, total_commission, total_rounds) VALUES (1, 0.0, 0);
        ''')


def ensure_user(uid, name='Player', referred_by=None):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone()
        if not row:
            conn.execute(
                'INSERT INTO users (uid, name, play_wallet, win_wallet, referred_by, created_at) VALUES (?, ?, 0.0, 0.0, ?, ?)',
                (uid, name or 'Player', referred_by, int(time.time())),
            )
            row = conn.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone()
        elif name and name != 'Player' and row['name'] != name:
            conn.execute('UPDATE users SET name=? WHERE uid=?', (name, uid))
            row = conn.execute('SELECT * FROM users WHERE uid=?', (uid,)).fetchone()
        return dict(row)


def add_tx(conn, uid, icon, title, amount):
    conn.execute(
        'INSERT INTO transactions (uid, icon, title, amount, created_at) VALUES (?, ?, ?, ?, ?)',
        (uid, icon, title, float(amount), int(time.time())),
    )


def generate_card(card_no):
    rng = random.Random(card_no * 7919 + 104729)
    ranges = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
    cols = [rng.sample(range(a, b + 1), 5) for a, b in ranges]
    grid = []
    for r in range(5):
        for c in range(5):
            grid.append('FREE' if (r == 2 and c == 2) else cols[c][r])
    return grid


WIN_LINES = [
    [0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12, 13, 14], [15, 16, 17, 18, 19], [20, 21, 22, 23, 24],
    [0, 5, 10, 15, 20], [1, 6, 11, 16, 21], [2, 7, 12, 17, 22], [3, 8, 13, 18, 23], [4, 9, 14, 19, 24],
    [0, 6, 12, 18, 24], [4, 8, 12, 16, 20],
    [0, 4, 20, 24],
]


def check_win_line(grid, called_set):
    for line in WIN_LINES:
        if all(grid[idx] == 'FREE' or grid[idx] in called_set for idx in line):
            return line
    return None


def make_room(stake):
    return {
        'stake': stake,
        'phase': 'WAITING',
        'countdown': COUNTDOWN_SECONDS,
        'cards': {},
        'called': [],
        'deck': [],
        'last_tick': time.time(),
        'winner': None,
        'winner_until': 0.0,
    }


rooms = {10: make_room(10), 20: make_room(20), 50: make_room(50)}


def distinct_players(room):
    return len({info['uid'] for info in room['cards'].values()})


def room_derash(room):
    return round(len(room['cards']) * room['stake'] * (1.0 - HOUSE_CUT), 2)


def finish_round_with_winner(room, uid, name, card_no, win_line):
    prize = room_derash(room)
    gross = len(room['cards']) * room['stake']
    commission = round(gross - prize, 2)
    with get_db() as conn:
        conn.execute(
            'UPDATE users SET win_wallet = win_wallet + ?, wins = wins + 1, total_won = total_won + ? WHERE uid=?',
            (prize, prize, uid),
        )
        add_tx(conn, uid, '🏆', f'BINGO አሸነፉ! (Card #{card_no})', prize)
        conn.execute(
            'UPDATE house_stats SET total_commission = total_commission + ?, total_rounds = total_rounds + 1 WHERE id=1',
            (commission,),
        )
    room['phase'] = 'WINNER'
    room['winner'] = {
        'uid': uid,
        'name': name,
        'card_no': card_no,
        'prize': prize,
        'grid': generate_card(card_no),
        'win_line': win_line,
    }
    room['winner_until'] = time.time() + WINNER_DISPLAY_SECONDS


def reset_room(room):
    room['phase'] = 'WAITING'
    room['countdown'] = COUNTDOWN_SECONDS
    room['cards'] = {}
    room['called'] = []
    room['deck'] = []
    room['winner'] = None
    room['last_tick'] = time.time()


def game_engine_loop():
    while True:
        now = time.time()
        with room_lock:
            for room in rooms.values():
                p_cnt = distinct_players(room)
                if room['phase'] == 'WAITING':
                    if p_cnt >= MIN_PLAYERS:
                        room['phase'] = 'COUNTDOWN'
                        room['countdown'] = COUNTDOWN_SECONDS
                        room['last_tick'] = now

                elif room['phase'] == 'COUNTDOWN':
                    if p_cnt < MIN_PLAYERS:
                        room['phase'] = 'WAITING'
                        room['countdown'] = COUNTDOWN_SECONDS
                    elif now - room['last_tick'] >= 1.0:
                        room['last_tick'] = now
                        room['countdown'] -= 1
                        if room['countdown'] <= 0:
                            room['phase'] = 'PLAYING'
                            room['called'] = []
                            room['deck'] = list(range(1, 76))
                            random.shuffle(room['deck'])
                            room['last_tick'] = now
                            uids = {info['uid'] for info in room['cards'].values()}
                            with get_db() as conn:
                                for uid in uids:
                                    conn.execute('UPDATE users SET matches_played = matches_played + 1 WHERE uid=?', (uid,))

                elif room['phase'] == 'PLAYING':
                    if now - room['last_tick'] >= CALL_INTERVAL:
                        room['last_tick'] = now
                        if room['deck']:
                            ball = room['deck'].pop()
                            room['called'].insert(0, ball)
                        else:
                            called_set = set(room['called'])
                            awarded = False
                            for cno, info in room['cards'].items():
                                wl = check_win_line(generate_card(cno), called_set)
                                if wl:
                                    finish_round_with_winner(room, info['uid'], info['name'], cno, wl)
                                    awarded = True
                                    break
                            if not awarded:
                                reset_room(room)

                elif room['phase'] == 'WINNER':
                    if now >= room['winner_until']:
                        reset_room(room)
        time.sleep(0.35)


# ================= TELEGRAM AUTH VERIFICATION =================
def verify_telegram_init_data(init_data: str):
    if not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        recv_hash = parsed.pop('hash', None)
        if not recv_hash or not TOKEN:
            return None
        data_check = '\n'.join(f'{k}={v}' for k, v in sorted(parsed.items()))
        secret = hmac.new(b'WebAppData', TOKEN.encode('utf-8'), hashlib.sha256).digest()
        calc_hash = hmac.new(secret, data_check.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, recv_hash):
            return None
        user_obj = json.loads(parsed.get('user', '{}'))
        uid = int(user_obj['id'])
        full_name = ' '.join(x for x in [user_obj.get('first_name'), user_obj.get('last_name')] if x) or 'Player'
        return {'uid': uid, 'name': full_name}
    except Exception:
        return None


def get_auth_user(x_tg_init_data: str):
    info = verify_telegram_init_data(x_tg_init_data)
    if not info:
        raise HTTPException(status_code=401, detail='እባክዎ አፑን በቴሌግራም ቦት ውስጥ ብቻ ይክፈቱ!')
    return ensure_user(info['uid'], info['name'])


# ================= TELEGRAM BOT & ADMIN PANEL =================
bot = telebot.TeleBot(TOKEN) if TOKEN else None


def is_admin(uid):
    return ADMIN_ID != 0 and int(uid) == int(ADMIN_ID)


def get_game_url():
    return APP_URL or 'https://example.com'


def main_menu_kb(uid=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if APP_URL.startswith('https://'):
        kb.row(KeyboardButton('🎮 Play Royal Bingo', web_app=WebAppInfo(url=get_game_url())))
    kb.row(KeyboardButton('💰 ገቢ (Deposit)'), KeyboardButton('💸 ወጪ (Withdraw)'))
    kb.row(KeyboardButton('💳 ባላንስ (Balance)'), KeyboardButton('👥 ግብዣ (Invite)'))
    kb.row(KeyboardButton('📝 ምዝገባ (Register)'), KeyboardButton('🎧 ድጋፍ (Support)'))
    if uid and is_admin(uid):
        kb.row(KeyboardButton('📜 ህግና መመሪያ'), KeyboardButton('👑 Admin Panel'))
    else:
        kb.row(KeyboardButton('📜 ህግና መመሪያ'))
    return kb


def play_inline_kb(label='🎲 Play'):
    kb = InlineKeyboardMarkup()
    if APP_URL.startswith('https://'):
        kb.add(InlineKeyboardButton(label, web_app=WebAppInfo(url=get_game_url())))
    return kb


def admin_panel_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton('📊 ሪፖርትና ትርፍ', callback_data='adm_stats'),
        InlineKeyboardButton('👥 ተጫዋቾች', callback_data='adm_users'),
    )
    kb.row(
        InlineKeyboardButton('➕ ብር ጨምር', callback_data='adm_add'),
        InlineKeyboardButton('➖ ብር ቀንስ', callback_data='adm_sub'),
    )
    kb.row(
        InlineKeyboardButton(f'⚙️ ዝቅተኛ ተጫዋች: {MIN_PLAYERS}', callback_data='adm_minp'),
        InlineKeyboardButton('💾 DB Backup', callback_data='adm_backup'),
    )
    kb.row(InlineKeyboardButton('📢 ለሁሉም መልዕክት ላክ', callback_data='adm_bcast'))
    return kb


def create_and_notify_request(req_type, uid, amount, method, details):
    u = ensure_user(uid)
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO requests (req_type, uid, amount, method, details, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (req_type, uid, float(amount), method, details, 'pending', int(time.time())),
        )
        req_id = cur.lastrowid

    if not bot or ADMIN_ID == 0:
        return req_id

    kb = InlineKeyboardMarkup()
    if req_type == 'deposit':
        hdr = '🔔 አዲስ የገቢ (DEPOSIT) ጥያቄ!'
        kb.row(
            InlineKeyboardButton('✅ ፍቀድ (Approve)', callback_data=f'ap_dep_{req_id}'),
            InlineKeyboardButton('❌ ሰርዝ (Reject)', callback_data=f'rj_dep_{req_id}'),
        )
    else:
        hdr = '🔔 አዲስ የወጪ (WITHDRAW) ጥያቄ!'
        kb.row(
            InlineKeyboardButton('✅ ተልኳል (Paid)', callback_data=f'ap_wd_{req_id}'),
            InlineKeyboardButton('❌ ሰርዝና መልስ', callback_data=f'rj_wd_{req_id}'),
        )

    text = (
        f'{hdr}\n\n'
        f'🆔 ጥያቄ፦ #{req_id}\n'
        f'👤 ተጫዋች፦ {u["name"]} (ID: {uid})\n'
        f'📞 ስልክ፦ {u["phone"] or "-"}\n'
        f'💵 መጠን፦ {float(amount):.2f} ETB\n'
        f'🏦 ዘዴ፦ {method}\n'
        f'📝 ዝርዝር፦ {details}'
    )
    try:
        bot.send_message(ADMIN_ID, text, reply_markup=kb)
    except Exception:
        pass
    return req_id


if bot:
    @bot.message_handler(commands=['myid'])
    def cmd_myid(m):
        bot.reply_to(m, f'🆔 የአንተ Telegram ID፦ `{m.from_user.id}`', parse_mode='Markdown')

    @bot.message_handler(commands=['admin'])
    @bot.message_handler(func=lambda m: m.text == '👑 Admin Panel')
    def cmd_admin(m):
        if not is_admin(m.from_user.id):
            bot.reply_to(m, '⛔ ለአድሚን ብቻ የተፈቀደ ነው።')
            return
        bot.send_message(m.chat.id, '👑 ROYAL BINGO — የአድሚን መቆጣጠሪያ', reply_markup=admin_panel_kb())

    @bot.message_handler(commands=['start'])
    def cmd_start(m):
        ref = None
        parts = (m.text or '').split()
        if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) != m.from_user.id:
            ref = int(parts[1])
        u = ensure_user(m.from_user.id, m.from_user.first_name or 'Player', referred_by=ref)
        if not u['phone']:
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.add(KeyboardButton('📱 ስልክ ቁጥር አጋራ (Share Contact)', request_contact=True))
            bot.send_message(
                m.chat.id,
                '👑 እንኳን ወደ ROYAL BINGO በደህና መጡ!\n\nለመመዝገብ ከታች ያለውን ቁልፍ ተጭነው ስልክ ቁጥርዎን ያጋሩ 👇',
                reply_markup=kb,
            )
        else:
            bot.send_message(
                m.chat.id,
                f'👑 ወደ ROYAL BINGO በደህና መጡ!\n\n💳 ለመጫወት መጀመሪያ "💰 ገቢ (Deposit)" በመጫን ሂሳብ ይሙሉ።\n🎧 ድጋፍ፦ {SUPPORT_USERNAME}',
                reply_markup=main_menu_kb(m.from_user.id),
            )
            bot.send_message(m.chat.id, '🎰 ROYAL BINGO — ቀጥታ የእውነተኛ ተጫዋቾች ጨዋታ! 🔥', reply_markup=play_inline_kb('🎮 Play Now'))

    @bot.message_handler(content_types=['contact'])
    def on_contact(m):
        ensure_user(m.from_user.id, m.from_user.first_name or 'Player')
        with get_db() as conn:
            conn.execute('UPDATE users SET phone=? WHERE uid=?', (m.contact.phone_number, m.from_user.id))
        bot.send_message(
            m.chat.id,
            f'✅ ተመዝግበዋል! ({m.contact.phone_number})\n\nአሁን ገቢ አድርገው መጫወት ይችላሉ 👇',
            reply_markup=main_menu_kb(m.from_user.id),
        )

    @bot.message_handler(commands=['play'])
    def cmd_play(m):
        bot.send_message(m.chat.id, 'Good luck Royal Bingo 🍀', reply_markup=play_inline_kb('🎲 Play'))

    @bot.message_handler(commands=['balance'])
    @bot.message_handler(func=lambda m: m.text == '💳 ባላንስ (Balance)')
    def cmd_balance(m):
        u = ensure_user(m.from_user.id, m.from_user.first_name or 'Player')
        bot.send_message(
            m.chat.id,
            f'🏦 Your Balance\n\n🎮 Play Wallet: {u["play_wallet"]:.2f} ETB\n🏆 Win Wallet: {u["win_wallet"]:.2f} ETB\n\n💰 ጠቅላላ: {u["play_wallet"] + u["win_wallet"]:.2f} ETB',
            reply_markup=play_inline_kb('🎲 Play'),
        )

    @bot.message_handler(commands=['deposit'])
    @bot.message_handler(func=lambda m: m.text == '💰 ገቢ (Deposit)')
    def cmd_deposit(m):
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton('📲 TeleBirr', callback_data='dep_TeleBirr'), InlineKeyboardButton('🏦 CBE', callback_data='dep_CBE'))
        bot.send_message(m.chat.id, '💳 የክፍያ አማራጭ ይምረጡ፦', reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data in ('dep_TeleBirr', 'dep_CBE'))
    def cb_dep_method(call):
        bot.answer_callback_query(call.id)
        method = call.data.split('_')[1]
        msg = bot.send_message(call.message.chat.id, f'💵 በ {method} የሚያስገቡትን መጠን ይጻፉ (ዝቅተኛ 20 ETB)፦')
        bot.register_next_step_handler(msg, step_dep_amount, method)

    def step_dep_amount(m, method):
        try:
            amt = float((m.text or '').strip())
            if amt < 20:
                bot.reply_to(m, '❌ ዝቅተኛው የገቢ መጠን 20 ETB ነው።')
                return
        except Exception:
            bot.reply_to(m, '❌ ትክክለኛ ቁጥር ያስገቡ (ለምሳሌ፦ 100)።')
            return
        msg = bot.send_message(
            m.chat.id,
            f'🏦 የክፍያ አድራሻ ({method})\n\n👤 ስም፦ {TELEBIRR_NAME}\n📱 ቁጥር፦ {TELEBIRR_PHONE}\n💵 መጠን፦ {amt:.2f} ETB\n\nከላኩ በኋላ የSMS Transaction ID ይላኩ 👇',
        )
        bot.register_next_step_handler(msg, step_dep_tx, amt, method)

    def step_dep_tx(m, amt, method):
        tx = (m.text or '').strip()
        create_and_notify_request('deposit', m.from_user.id, amt, method, f'TX: {tx}')
        bot.reply_to(m, f'✅ የ {amt:.2f} ETB ገቢ ጥያቄዎ ለአድሚን ተልኳል! ሲጸድቅ ወዲያውኑ አፑ ውስጥ ይገባል።', reply_markup=main_menu_kb(m.from_user.id))

    @bot.message_handler(commands=['withdraw'])
    @bot.message_handler(func=lambda m: m.text == '💸 ወጪ (Withdraw)')
    def cmd_withdraw(m):
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton('📲 TeleBirr', callback_data='wd_TeleBirr'), InlineKeyboardButton('🏦 CBE', callback_data='wd_CBE'))
        bot.send_message(m.chat.id, '💸 ወደ የትኛው አካውንት ማውጣት ይፈልጋሉ?', reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data in ('wd_TeleBirr', 'wd_CBE'))
    def cb_wd_method(call):
        bot.answer_callback_query(call.id)
        method = call.data.split('_')[1]
        msg = bot.send_message(call.message.chat.id, f'💸 በ {method} የሚወጣውን መጠን ይጻፉ (ዝቅተኛ 100 ETB)፦')
        bot.register_next_step_handler(msg, step_wd_amount, method)

    def step_wd_amount(m, method):
        u = ensure_user(m.from_user.id)
        try:
            amt = float((m.text or '').strip())
        except Exception:
            bot.reply_to(m, '❌ ትክክለኛ ቁጥር ያስገቡ።')
            return
        if amt < 100:
            bot.reply_to(m, '❌ ዝቅተኛው የወጪ መጠን 100 ETB ነው።')
            return
        if u['win_wallet'] < amt:
            bot.reply_to(m, f'❌ በቂ Win Wallet ባላንስ የለዎትም! (ያለዎት፦ {u["win_wallet"]:.2f} ETB)')
            return
        msg = bot.reply_to(m, f'📱 የ {method} ስልክ/አካውንት ቁጥርዎን ያስገቡ፦')
        bot.register_next_step_handler(msg, step_wd_acc, amt, method)

    def step_wd_acc(m, amt, method):
        acc = (m.text or '').strip()
        with get_db() as conn:
            u = conn.execute('SELECT win_wallet FROM users WHERE uid=?', (m.from_user.id,)).fetchone()
            if not u or u['win_wallet'] < amt:
                bot.reply_to(m, '❌ በቂ Win Wallet የለዎትም!')
                return
            conn.execute('UPDATE users SET win_wallet = win_wallet - ? WHERE uid=?', (amt, m.from_user.id))
            add_tx(conn, m.from_user.id, '💸', f'የወጪ ጥያቄ ({method})', -amt)
        create_and_notify_request('withdraw', m.from_user.id, amt, method, f'Acc: {acc}')
        bot.reply_to(m, f'✅ የ {amt:.2f} ETB የወጪ ጥያቄዎ ለአድሚን ተልኳል።', reply_markup=main_menu_kb(m.from_user.id))

    @bot.callback_query_handler(func=lambda c: c.data.startswith(('adm_', 'ap_', 'rj_')))
    def cb_admin(call):
        global MIN_PLAYERS
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔ ለአድሚን ብቻ!', show_alert=True)
            return
        data = call.data
        bot.answer_callback_query(call.id)

        if data == 'adm_stats':
            with get_db() as conn:
                u_cnt = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
                s_pw = conn.execute('SELECT COALESCE(SUM(play_wallet),0) FROM users').fetchone()[0]
                s_ww = conn.execute('SELECT COALESCE(SUM(win_wallet),0) FROM users').fetchone()[0]
                pend = conn.execute("SELECT COUNT(*) FROM requests WHERE status='pending'").fetchone()[0]
                hs = conn.execute('SELECT total_commission, total_rounds FROM house_stats WHERE id=1').fetchone()
            with room_lock:
                live_str = ' | '.join(f'{k}ብር:{distinct_players(r)}ሰው({r["phase"]})' for k, r in rooms.items())
            bot.send_message(
                call.message.chat.id,
                f'📊 ROYAL BINGO ቀጥታ ሪፖርት\n\n'
                f'👥 ተጫዋቾች፦ {u_cnt}\n'
                f'🎮 ጠቅላላ Play Wallet፦ {s_pw:.2f} ETB\n'
                f'🏆 ጠቅላላ Win Wallet፦ {s_ww:.2f} ETB\n'
                f'💰 የቤት ኮሚሽን ትርፍ (20%)፦ {hs["total_commission"]:.2f} ETB\n'
                f'🎲 የተጠናቀቁ ዙሮች፦ {hs["total_rounds"]}\n'
                f'⏳ ያልተወሰኑ ጥያቄዎች፦ {pend}\n'
                f'🔴 ቀጥታ ክፍሎች፦ {live_str}',
            )

        elif data == 'adm_users':
            with get_db() as conn:
                rows = conn.execute('SELECT * FROM users ORDER BY created_at DESC LIMIT 20').fetchall()
            if not rows:
                bot.send_message(call.message.chat.id, 'ምንም ተጫዋች የለም።')
                return
            lines = ['👥 የመጨረሻ 20 ተጫዋቾች፦\n']
            for r in rows:
                lines.append(f'• {r["name"]} (`{r["uid"]}`) | 📞 {r["phone"] or "-"}\n  Play: {r["play_wallet"]:.1f} | Win: {r["win_wallet"]:.1f}')
            bot.send_message(call.message.chat.id, '\n'.join(lines), parse_mode='Markdown')

        elif data in ('adm_add', 'adm_sub'):
            act = 'add' if data == 'adm_add' else 'sub'
            lbl = 'መጨመር' if act == 'add' else 'መቀነስ'
            msg = bot.send_message(call.message.chat.id, f'✏️ ብር {lbl} የሚፈልጉትን ID እና መጠን ይላኩ (ምሳሌ፦ `490567751 100`)', parse_mode='Markdown')
            bot.register_next_step_handler(msg, step_adm_bal, act)

        elif data == 'adm_minp':
            MIN_PLAYERS = 1 if MIN_PLAYERS >= 3 else MIN_PLAYERS + 1
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=admin_panel_kb())
            mode = 'ለሙከራ (1 ተጫዋች)' if MIN_PLAYERS == 1 else f'እውነተኛ ({MIN_PLAYERS}+ ተጫዋቾች)'
            bot.send_message(call.message.chat.id, f'⚙️ ዝቅተኛ የተጫዋች ብዛት ወደ **{MIN_PLAYERS}** ({mode}) ተቀይሯል።', parse_mode='Markdown')

        elif data == 'adm_backup':
            if os.path.exists(DB_PATH):
                with open(DB_PATH, 'rb') as f:
                    bot.send_document(call.message.chat.id, f, caption='💾 ROYAL BINGO Database Backup')

        elif data == 'adm_bcast':
            msg = bot.send_message(call.message.chat.id, '📢 ለሁሉም ተጫዋቾች የሚላከውን መልዕክት ይጻፉ፦')
            bot.register_next_step_handler(msg, step_adm_bcast)

        elif data.startswith(('ap_', 'rj_')):
            act, rtype, req_id = data.split('_')
            with get_db() as conn:
                req = conn.execute('SELECT * FROM requests WHERE id=?', (int(req_id),)).fetchone()
                if not req or req['status'] != 'pending':
                    bot.send_message(call.message.chat.id, '⚠️ ይህ ጥያቄ ቀድሞ ተወስኗል።')
                    return
                uid, amt = req['uid'], req['amount']
                if rtype == 'dep' and act == 'ap':
                    conn.execute("UPDATE requests SET status='approved' WHERE id=?", (int(req_id),))
                    conn.execute('UPDATE users SET play_wallet = play_wallet + ? WHERE uid=?', (amt, uid))
                    add_tx(conn, uid, '💰', f'ገቢ ጸድቋል ({req["method"]})', amt)
                    u = conn.execute('SELECT referred_by FROM users WHERE uid=?', (uid,)).fetchone()
                    if u and u['referred_by']:
                        ref_id = u['referred_by']
                        conn.execute('UPDATE users SET play_wallet = play_wallet + 15.0 WHERE uid=?', (ref_id,))
                        conn.execute('UPDATE users SET referred_by = NULL WHERE uid=?', (uid,))
                        add_tx(conn, ref_id, '🎁', 'የግብዣ ሽልማት (Referral)', 15.0)
                    bot.edit_message_text(call.message.text + f'\n\n✅ ጸድቋል (+{amt:.2f} ETB)', call.message.chat.id, call.message.message_id)
                    try:
                        bot.send_message(uid, f'🎉 የ {amt:.2f} ETB ገቢዎ ጸድቋል! አሁን አፑን ከፍተው መጫወት ይችላሉ።')
                    except Exception:
                        pass
                elif rtype == 'dep' and act == 'rj':
                    conn.execute("UPDATE requests SET status='rejected' WHERE id=?", (int(req_id),))
                    bot.edit_message_text(call.message.text + '\n\n❌ ተሰርዟል', call.message.chat.id, call.message.message_id)
                    try:
                        bot.send_message(uid, f'❌ የ {amt:.2f} ETB ገቢ ጥያቄዎ አልጸደቀም። {SUPPORT_USERNAME} ያነጋግሩ።')
                    except Exception:
                        pass
                elif rtype == 'wd' and act == 'ap':
                    conn.execute("UPDATE requests SET status='paid' WHERE id=?", (int(req_id),))
                    bot.edit_message_text(call.message.text + f'\n\n✅ ተልኳል ({amt:.2f} ETB)', call.message.chat.id, call.message.message_id)
                    try:
                        bot.send_message(uid, f'✅ የ {amt:.2f} ETB ወጪዎ ወደ አካውንትዎ ተልኳል!')
                    except Exception:
                        pass
                elif rtype == 'wd' and act == 'rj':
                    conn.execute("UPDATE requests SET status='rejected' WHERE id=?", (int(req_id),))
                    conn.execute('UPDATE users SET win_wallet = win_wallet + ? WHERE uid=?', (amt, uid))
                    add_tx(conn, uid, '↩️', 'የወጪ ጥያቄ ተመልሷል', amt)
                    bot.edit_message_text(call.message.text + '\n\n❌ ተሰርዞ ወደ Win Wallet ተመልሷል', call.message.chat.id, call.message.message_id)
                    try:
                        bot.send_message(uid, f'❌ የ {amt:.2f} ETB የወጪ ጥያቄዎ ተሰርዞ ገንዘቡ ወደ Win Wallet ተመልሷል።')
                    except Exception:
                        pass

    def step_adm_bal(m, act):
        if not is_admin(m.from_user.id):
            return
        try:
            uid_s, amt_s = (m.text or '').strip().split()
            uid, amt = int(uid_s), float(amt_s)
            ensure_user(uid)
            with get_db() as conn:
                if act == 'add':
                    conn.execute('UPDATE users SET play_wallet = play_wallet + ? WHERE uid=?', (amt, uid))
                    add_tx(conn, uid, '🎁', 'አድሚን ጨምሯል', amt)
                else:
                    conn.execute('UPDATE users SET play_wallet = MAX(0, play_wallet - ?) WHERE uid=?', (amt, uid))
                    add_tx(conn, uid, '➖', 'አድሚን ቀንሷል', -amt)
                pw = conn.execute('SELECT play_wallet FROM users WHERE uid=?', (uid,)).fetchone()[0]
            bot.reply_to(m, f'✅ ተስተካክሏል! ID `{uid}` አዲስ Play Wallet: {pw:.2f} ETB', parse_mode='Markdown')
        except Exception:
            bot.reply_to(m, '❌ ምሳሌ፦ `490567751 100`', parse_mode='Markdown')

    def step_adm_bcast(m):
        if not is_admin(m.from_user.id):
            return
        with get_db() as conn:
            uids = [r[0] for r in conn.execute('SELECT uid FROM users').fetchall()]
        sent = 0
        for uid in uids:
            try:
                bot.send_message(uid, f'📢 ማስታወቂያ (ROYAL BINGO)\n\n{m.text}')
                sent += 1
            except Exception:
                pass
        bot.reply_to(m, f'✅ ለ {sent} ተጫዋቾች ተልኳል።')

    @bot.message_handler(func=lambda m: m.text == '👥 ግብዣ (Invite)')
    def cmd_invite(m):
        me = bot.get_me()
        bot.send_message(m.chat.id, f'🎁 ጓደኛ ይጋብዙና የመጀመሪያ ገቢ ሲያደርግ 15 ETB ይሸለሙ!\n\n🔗 https://t.me/{me.username}?start={m.from_user.id}')

    @bot.message_handler(func=lambda m: m.text == '📝 ምዝገባ (Register)')
    def cmd_reg(m):
        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(KeyboardButton('📱 ስልክ ቁጥር አጋራ', request_contact=True))
        bot.send_message(m.chat.id, '📱 ስልክ ቁጥርዎን ለማዘመን ከታች ይጫኑ 👇', reply_markup=kb)

    @bot.message_handler(func=lambda m: m.text == '🎧 ድጋፍ (Support)')
    def cmd_sup(m):
        bot.send_message(m.chat.id, f'🎧 ድጋፍ፦ {SUPPORT_USERNAME}')

    @bot.message_handler(func=lambda m: m.text == '📜 ህግና መመሪያ')
    def cmd_rules(m):
        bot.send_message(
            m.chat.id,
            '📜 የ ROYAL BINGO ህግና መመሪያ\n\n'
            '• እውነተኛ ተጫዋቾች ብቻ! ቢያንስ 2 ተጫዋቾች ካርድ ሲገዙ ጨዋታው ይጀምራል።\n'
            '• የካርድ ዋጋ፦ 10 / 20 / 50 ብር (እስከ 4 ካርድ)።\n'
            '• ደራሽ፦ 80% ለአሸናፊው ወዲያውኑ ወደ Win Wallet ይገባል።\n'
            '• ዝቅተኛ የወጪ መጠን፦ 100 ETB (ከ Win Wallet ብቻ)።',
        )


def run_bot_polling():
    if not bot:
        print('WARNING: BOT_TOKEN not set; bot polling skipped.')
        return
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=15)
        except Exception as e:
            print('Bot polling error:', e)
            time.sleep(5)


# ================= FASTAPI WEB APP SERVER =================
app = FastAPI(title='ROYAL BINGO Live Server')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

if os.path.isdir('sounds'):
    app.mount('/sounds', StaticFiles(directory='sounds'), name='sounds')


@app.on_event('startup')
def on_startup():
    init_db()
    threading.Thread(target=game_engine_loop, daemon=True).start()
    threading.Thread(target=run_bot_polling, daemon=True).start()


@app.get('/health')
def health():
    return {'ok': True, 'time': int(time.time())}


@app.get('/', response_class=HTMLResponse)
def serve_app():
    if os.path.exists('index.html'):
        return FileResponse('index.html')
    return HTMLResponse('<h1>index.html not found</h1>', status_code=404)


@app.get('/api/state')
def api_state(stake: int = 10, x_tg_init_data: str = Header(default='')):
    u = get_auth_user(x_tg_init_data)
    if stake not in rooms:
        stake = 10
    with room_lock:
        r = rooms[stake]
        my_cards = sorted([cno for cno, info in r['cards'].items() if info['uid'] == u['uid']])
        taken_cards = sorted([cno for cno, info in r['cards'].items() if info['uid'] != u['uid']])
        my_boards = {str(cno): generate_card(cno) for cno in my_cards}
        room_snap = {
            'stake': stake,
            'phase': r['phase'],
            'countdown': r['countdown'],
            'players': distinct_players(r),
            'min_players': MIN_PLAYERS,
            'total_cards': len(r['cards']),
            'derash': room_derash(r),
            'taken_cards': taken_cards,
            'my_cards': my_cards,
            'my_boards': my_boards,
            'called': r['called'],
            'winner': r['winner'],
            'winner_left': max(0, int(r['winner_until'] - time.time())) if r['phase'] == 'WINNER' else 0,
        }
    with get_db() as conn:
        txs = [dict(x) for x in conn.execute('SELECT icon, title, amount, created_at FROM transactions WHERE uid=? ORDER BY id DESC LIMIT 25', (u['uid'],)).fetchall()]
        top = [dict(x) for x in conn.execute('SELECT name, total_won FROM users WHERE total_won > 0 ORDER BY total_won DESC LIMIT 10').fetchall()]
    return {
        'user': {
            'uid': u['uid'],
            'name': u['name'],
            'play_wallet': round(u['play_wallet'], 2),
            'win_wallet': round(u['win_wallet'], 2),
            'matches_played': u['matches_played'],
            'wins': u['wins'],
        },
        'config': {
            'telebirr_name': TELEBIRR_NAME,
            'telebirr_phone': TELEBIRR_PHONE,
            'support': SUPPORT_USERNAME,
        },
        'room': room_snap,
        'history': txs,
        'rank': top,
    }


@app.post('/api/buy_card')
async def api_buy_card(request: Request, x_tg_init_data: str = Header(default='')):
    u = get_auth_user(x_tg_init_data)
    body = await request.json()
    stake = int(body.get('stake', 10))
    card_no = int(body.get('card_no', 0))
    if stake not in rooms or not (1 <= card_no <= 100):
        raise HTTPException(400, 'ልክ ያልሆነ ካርድ!')

    with room_lock:
        r = rooms[stake]
        if r['phase'] not in ('WAITING', 'COUNTDOWN'):
            raise HTTPException(400, 'ጨዋታው ተጀምሯል፤ በቀጣዩ ዙር ይምረጡ!')
        if card_no in r['cards']:
            if r['cards'][card_no]['uid'] == u['uid']:
                del r['cards'][card_no]
                with get_db() as conn:
                    conn.execute('UPDATE users SET play_wallet = play_wallet + ? WHERE uid=?', (stake, u['uid']))
                    add_tx(conn, u['uid'], '↩️', f'ካርድ #{card_no} ተሰርዟል', stake)
                return {'ok': True, 'action': 'removed'}
            raise HTTPException(400, f'ካርድ #{card_no} በሌላ ተጫዋች ተይዟል!')

        my_cnt = sum(1 for info in r['cards'].values() if info['uid'] == u['uid'])
        if my_cnt >= 4:
            raise HTTPException(400, 'በአንድ ዙር እስከ 4 ካርድ ብቻ መግዛት ይችላሉ!')

        with get_db() as conn:
            cur_u = conn.execute('SELECT play_wallet FROM users WHERE uid=?', (u['uid'],)).fetchone()
            if cur_u['play_wallet'] < stake:
                raise HTTPException(400, 'በቂ Play Wallet ባላንስ የለዎትም! እባክዎ መጀመሪያ ገቢ ያድርጉ።')
            conn.execute('UPDATE users SET play_wallet = play_wallet - ? WHERE uid=?', (stake, u['uid']))
            add_tx(conn, u['uid'], '🎴', f'ካርድ #{card_no} ({stake} ብር)', -stake)

        r['cards'][card_no] = {'uid': u['uid'], 'name': u['name']}
        return {'ok': True, 'action': 'bought'}


@app.post('/api/claim_bingo')
async def api_claim_bingo(request: Request, x_tg_init_data: str = Header(default='')):
    u = get_auth_user(x_tg_init_data)
    body = await request.json()
    stake = int(body.get('stake', 10))
    card_no = int(body.get('card_no', 0))
    if stake not in rooms:
        raise HTTPException(400, 'ልክ ያልሆነ ክፍል!')

    with room_lock:
        r = rooms[stake]
        if r['phase'] != 'PLAYING':
            raise HTTPException(400, 'ጨዋታው አልተጀመረም ወይም ተጠናቋል!')
        owner = r['cards'].get(card_no)
        if not owner or owner['uid'] != u['uid']:
            raise HTTPException(403, 'ይህ ካርድ የእርስዎ አይደለም!')
        wl = check_win_line(generate_card(card_no), set(r['called']))
        if not wl:
            raise HTTPException(400, 'ገና ሙሉ መስመር (BINGO) አልተሟላም!')
        finish_round_with_winner(r, u['uid'], u['name'], card_no, wl)
        return {'ok': True, 'prize': r['winner']['prize']}


@app.post('/api/transfer')
async def api_transfer(x_tg_init_data: str = Header(default='')):
    u = get_auth_user(x_tg_init_data)
    with get_db() as conn:
        row = conn.execute('SELECT win_wallet FROM users WHERE uid=?', (u['uid'],)).fetchone()
        amt = row['win_wallet']
        if amt <= 0:
            raise HTTPException(400, 'በ Win Wallet ውስጥ ገንዘብ የለም!')
        conn.execute('UPDATE users SET play_wallet = play_wallet + ?, win_wallet = 0 WHERE uid=?', (amt, u['uid']))
        add_tx(conn, u['uid'], '🔄', 'ከ Win ወደ Play ተዛውሯል', amt)
    return {'ok': True, 'amount': amt}


@app.post('/api/deposit')
async def api_deposit(request: Request, x_tg_init_data: str = Header(default='')):
    u = get_auth_user(x_tg_init_data)
    body = await request.json()
    amt = float(body.get('amount', 0))
    tx_id = str(body.get('tx_id', '')).strip()
    if amt < 20 or len(tx_id) < 4:
        raise HTTPException(400, 'ዝቅተኛ 20 ETB እና ትክክለኛ Transaction ID ያስገቡ!')
    create_and_notify_request('deposit', u['uid'], amt, 'TeleBirr/CBE', f'App TX: {tx_id}')
    return {'ok': True}


@app.post('/api/withdraw')
async def api_withdraw(request: Request, x_tg_init_data: str = Header(default='')):
    u = get_auth_user(x_tg_init_data)
    body = await request.json()
    amt = float(body.get('amount', 0))
    phone = str(body.get('phone', '')).strip()
    if amt < 100 or len(phone) < 9:
        raise HTTPException(400, 'ዝቅተኛ 100 ETB እና ትክክለኛ ስልክ ቁጥር ያስገቡ!')
    with get_db() as conn:
        row = conn.execute('SELECT win_wallet FROM users WHERE uid=?', (u['uid'],)).fetchone()
        if row['win_wallet'] < amt:
            raise HTTPException(400, 'በቂ Win Wallet ባላንስ የለዎትም!')
        conn.execute('UPDATE users SET win_wallet = win_wallet - ? WHERE uid=?', (amt, u['uid']))
        add_tx(conn, u['uid'], '💸', f'የወጪ ጥያቄ ({phone})', -amt)
    create_and_notify_request('withdraw', u['uid'], amt, 'TeleBirr/CBE', f'App Phone: {phone}')
    return {'ok': True}
