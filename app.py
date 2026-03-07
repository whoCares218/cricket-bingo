# MUST BE ABSOLUTELY FIRST — patch before any import touches ssl/socket
from gevent import monkey; monkey.patch_all()

"""
Cricket Bingo — v7 (Full Production Build)
All body templates included. Rating display fix applied to GAME_BODY.
All features preserved from v6. Additional fixes:
  - GAME_BODY fully implemented with endGame rating update UI
  - All HTML body templates filled in (were placeholders)
  - #rating-result rendered in results modal
  - showLevelUp overlay wired to XP response
  - Streak toast wired to streak response
"""

import os, json, random as _random_module, string, hashlib, time, smtplib, logging
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template_string, request, session, redirect, url_for, jsonify, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_dance.contrib.google import make_google_blueprint, google
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="public", static_url_path="/public")
_secret = os.getenv("SECRET_KEY")
app.secret_key = _secret or "dev-secret-key-change-me"
app.config["OAUTHLIB_INSECURE_TRANSPORT"] = os.getenv("OAUTHLIB_INSECURE_TRANSPORT", "0") == "1"

socketio = SocketIO(app, async_mode="gevent", cors_allowed_origins="*")
login_manager = LoginManager(app)
login_manager.login_view = "home"

google_bp = make_google_blueprint(
    client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
    scope=["openid", "https://www.googleapis.com/auth/userinfo.email",
           "https://www.googleapis.com/auth/userinfo.profile"],
    redirect_to="oauth_callback"
)
app.register_blueprint(google_bp, url_prefix="/login")

DATABASE      = "cricket_bingo.db"
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "tehm8111@gmail.com")

def send_email(to_addr, subject, html_body, text_body=""):
    if not SMTP_USER or not SMTP_PASSWORD:
        log.warning("SMTP not configured"); return False, "Email service not configured"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = SMTP_USER; msg["To"] = to_addr
        if text_body: msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo(); server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_addr, msg.as_string())
        return True, ""
    except Exception as e:
        log.error(f"Email send failed: {e}"); return False, str(e)

TEAM_LOGOS = {
    "Chennai Super Kings":          "csk.png",
    "Delhi Capitals":               "dc.png",
    "Delhi Daredevils":             "dd.png",
    "Deccan Chargers":              "deccan.png",
    "Gujarat Titans":               "gt.png",
    "Kolkata Knight Riders":        "kkr.png",
    "Kochi Tuskers Kerala":         "kochi.jpeg",
    "Lucknow Super Giants":         "lsg.png",
    "Mumbai Indians":               "mi.png",
    "Punjab Kings":                 "pun.png",
    "Kings XI Punjab":              "pun.png",
    "Royal Challengers Bengaluru":  "rcb.png",
    "Royal Challengers Bangalore":  "rcb.png",
    "Rajasthan Royals":             "rr.png",
    "Sunrisers Hyderabad":          "srh.png",
    "Pune Warriors India":          "pune.jpeg",
    "Rising Pune Supergiant":       "pune.jpeg",
    "Rising Pune Supergiants":      "pune.jpeg",
}

FLAG_MAP = {
    'India': '🇮🇳', 'Australia': '🇦🇺', 'England': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    'South Africa': '🇿🇦', 'New Zealand': '🇳🇿', 'Pakistan': '🇵🇰',
    'Sri Lanka': '🇱🇰', 'Bangladesh': '🇧🇩', 'Afghanistan': '🇦🇫',
    'Zimbabwe': '🇿🇼', 'West Indies': '🏝️'
}

# ── DB ─────────────────────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_database", None)
    if db: db.close()

def query_db(sql, args=(), one=False, commit=False):
    db = get_db(); cur = db.execute(sql, args)
    if commit: db.commit()
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS season_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, season_id INTEGER NOT NULL,
            rating REAL DEFAULT 1200,
            solo_rating REAL DEFAULT 1200,
            wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            total_games INTEGER DEFAULT 0,
            solo_games INTEGER DEFAULT 0,
            accuracy_sum REAL DEFAULT 0, time_sum REAL DEFAULT 0,
            win_streak INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0,
            UNIQUE(user_id, season_id), FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player1_id INTEGER, player2_id INTEGER, winner_id INTEGER,
            player1_score REAL DEFAULT 0, player2_score REAL DEFAULT 0,
            player1_time REAL DEFAULT 0, player2_time REAL DEFAULT 0,
            player1_accuracy REAL DEFAULT 0, player2_accuracy REAL DEFAULT 0,
            rating_change REAL DEFAULT 0, mode TEXT DEFAULT 'rated',
            data_source TEXT DEFAULT 'overall', grid_size INTEGER DEFAULT 3,
            difficulty TEXT DEFAULT 'normal', season_id INTEGER,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(player1_id) REFERENCES users(id),
            FOREIGN KEY(player2_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS active_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT, room_code TEXT UNIQUE NOT NULL,
            player1_id INTEGER, player2_id INTEGER, game_state TEXT NOT NULL,
            status TEXT DEFAULT 'waiting', mode TEXT DEFAULT 'rated',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(player1_id) REFERENCES users(id),
            FOREIGN KEY(player2_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS matchmaking_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE NOT NULL,
            rating REAL NOT NULL, data_source TEXT DEFAULT 'overall',
            grid_size INTEGER DEFAULT 3, difficulty TEXT DEFAULT 'normal',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS daily_challenge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_date TEXT UNIQUE NOT NULL, game_state TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, challenge_date TEXT NOT NULL,
            score REAL DEFAULT 0, completion_time REAL DEFAULT 0, accuracy REAL DEFAULT 0,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, challenge_date), FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS daily_streaks (
            user_id INTEGER PRIMARY KEY,
            current_streak INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0,
            last_played_date TEXT,
            freeze_available INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS cell_picks (
            challenge_date TEXT NOT NULL,
            cell_index INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            pick_count INTEGER DEFAULT 1,
            UNIQUE(challenge_date, cell_index, player_name)
        );
        CREATE TABLE IF NOT EXISTS user_xp (
            user_id INTEGER PRIMARY KEY,
            total_xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        db.commit()
        _ensure_season()

def _ensure_season():
    db = get_db(); today = date.today().isoformat()
    if not db.execute("SELECT id FROM seasons WHERE start_date<=? AND end_date>=?", (today, today)).fetchone():
        last = db.execute("SELECT MAX(id) as m FROM seasons").fetchone()
        n = (last["m"] or 0) + 1
        s = date.today(); e = s + timedelta(days=90)
        db.execute("INSERT INTO seasons(name,start_date,end_date) VALUES(?,?,?)",
                   (f"Season {n}", s.isoformat(), e.isoformat()))
        db.commit()

def get_current_season():
    today = date.today().isoformat()
    return query_db("SELECT * FROM seasons WHERE start_date<=? AND end_date>=?", (today, today), one=True)

class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]; self.google_id = row["google_id"]
        self.email = row["email"]; self.name = row["name"]; self.avatar = row["avatar"]

@login_manager.user_loader
def load_user(uid):
    row = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    return User(row) if row else None

def load_json(fp):
    if not os.path.exists(fp): return []
    try:
        with open(fp, "r", encoding="utf-8") as f: data = json.load(f)
        for i, p in enumerate(data):
            if "id" not in p or not p["id"]: p["id"] = f"player_{i}"
            if "name" not in p: p["name"] = f"Player {i+1}"
        return data
    except Exception as e:
        log.error(f"Failed to load {fp}: {e}"); return []

OVERALL_DATA = load_json("overall.json")
IPL26_DATA   = load_json("ipl26.json")
log.info(f"Loaded {len(OVERALL_DATA)} overall players, {len(IPL26_DATA)} ipl26 players")

def get_pool(ds):
    return OVERALL_DATA if ds == "overall" else IPL26_DATA

def player_matches_cell(player, cell, ds):
    ct, cv = cell["type"], cell["value"]
    teams    = player.get("iplTeams", []) if ds == "overall" else [player.get("team", "")]
    nation   = player.get("nation", "")
    trophies = player.get("trophies", []) if ds == "overall" else []
    if ct == "team":   return cv in teams
    if ct == "nation": return cv == nation
    if ct == "trophy": return cv in trophies
    if ct == "combo":
        parts = [p.strip() for p in cv.split("+")]
        return all(
            pt in teams or pt == nation or pt in trophies
            for pt in parts
        )
    return False

# ══════════════════════════════════════════════════════════════════════════════
#  FAME-BASED PLAYER SELECTION
# ══════════════════════════════════════════════════════════════════════════════

DIFFICULTY_CONFIG = {
    "easy":   {"high": 0.75, "medium": 0.25, "low": 0.00, "grid": "easy"},
    "normal": {"high": 0.50, "medium": 0.50, "low": 0.00, "grid": "normal"},
    "hard":   {"high": 0.30, "medium": 0.60, "low": 0.10, "grid": "hard"},
}

def select_players_by_fame(pool, difficulty, n=25, player_type=None, rng=None):
    if rng is None:
        rng = _random_module

    filtered = list(pool)
    high_f   = [p for p in filtered if p.get("famous") == "high"]
    medium_f = [p for p in filtered if p.get("famous") == "medium"]
    low_f    = [p for p in filtered if p.get("famous") == "low"]

    dist     = DIFFICULTY_CONFIG.get(difficulty, DIFFICULTY_CONFIG["normal"])
    n_high   = round(n * dist["high"])
    n_medium = round(n * dist["medium"])
    n_low    = max(0, n - n_high - n_medium)

    rng.shuffle(high_f); rng.shuffle(medium_f); rng.shuffle(low_f)
    selected  = []
    selected += high_f[:n_high]
    selected += medium_f[:n_medium]
    selected += low_f[:n_low]

    if len(selected) < n:
        used_ids = {id(p) for p in selected}
        rest = [p for p in filtered if id(p) not in used_ids]
        rng.shuffle(rest)
        selected += rest[: n - len(selected)]

    rng.shuffle(selected)
    log.info(
        f"select_players_by_fame: diff={difficulty} → {len(selected)} players "
        f"(high={sum(1 for p in selected if p.get('famous')=='high')}, "
        f"medium={sum(1 for p in selected if p.get('famous')=='medium')}, "
        f"low={sum(1 for p in selected if p.get('famous')=='low')})"
    )
    return selected[:n]

# ══════════════════════════════════════════════════════════════════════════════
#  GRID BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_grid_validated(size, ds, difficulty, pool, rng=None):
    if rng is None:
        rng = _random_module

    n = size * size
    valid_teams    = list({t for p in pool for t in p.get("iplTeams", [])} if ds == "overall"
                          else {p["team"] for p in pool if p.get("team")})
    valid_nations  = list({p["nation"] for p in pool if p.get("nation")})
    valid_trophies = list({t for p in pool for t in p.get("trophies", [])} if ds == "overall" else [])

    def has_player(cell):
        return any(player_matches_cell(p, cell, ds) for p in pool)

    def get_valid_category(cell_type, seen, max_tries=50):
        if cell_type == "team" and valid_teams:
            candidates = [t for t in valid_teams if t not in seen]
            rng.shuffle(candidates)
            for v in candidates:
                cell = {"type": "team", "value": v}
                if has_player(cell): return cell
        elif cell_type == "nation" and valid_nations:
            candidates = [nn for nn in valid_nations if nn not in seen]
            rng.shuffle(candidates)
            for v in candidates:
                cell = {"type": "nation", "value": v}
                if has_player(cell): return cell
        elif cell_type == "trophy" and valid_trophies:
            candidates = [t for t in valid_trophies if t not in seen]
            rng.shuffle(candidates)
            for v in candidates:
                cell = {"type": "trophy", "value": v}
                if has_player(cell): return cell
        elif cell_type == "combo":
            for _ in range(max_tries):
                p = rng.choice(pool)
                teams_p    = p.get("iplTeams", []) if ds == "overall" else [p.get("team", "")]
                nation_p   = p.get("nation", "")
                trophies_p = p.get("trophies", []) if ds == "overall" else []
                combos = []
                if teams_p and nation_p:
                    combos.append(f"{rng.choice(teams_p)} + {nation_p}")
                if teams_p and trophies_p:
                    combos.append(f"{rng.choice(teams_p)} + {rng.choice(trophies_p)}")
                if nation_p and trophies_p:
                    combos.append(f"{nation_p} + {rng.choice(trophies_p)}")
                for combo_v in combos:
                    if combo_v not in seen:
                        cell = {"type": "combo", "value": combo_v}
                        if has_player(cell): return cell
        return None

    if difficulty == "easy":
        type_pool = ["team"] * n
    elif difficulty == "hard":
        type_pool = (["team"] * (n // 3)
                     + ["nation"] * (n // 3)
                     + ["combo"] * (n - 2 * (n // 3)))
    else:
        type_pool = ["team"] * (n // 2) + ["nation"] * (n - n // 2)
    rng.shuffle(type_pool)

    cells, seen = [], set()
    for ct in type_pool:
        cell = get_valid_category(ct, seen)
        if cell is None:
            cell = get_valid_category("team", seen)
        if cell is None and valid_teams:
            for t in valid_teams:
                c = {"type": "team", "value": t}
                if has_player(c) and t not in seen:
                    cell = c; break
        if cell:
            seen.add(cell["value"]); cells.append(cell)

    while len(cells) < n and valid_teams:
        added = False
        for t in valid_teams:
            if t not in seen:
                c = {"type": "team", "value": t}
                if has_player(c):
                    seen.add(t); cells.append(c)
                    added = True
                    if len(cells) >= n: break
        if not added: break

    return cells[:n]

def create_game_state(ds, grid_size, difficulty, seed=None, player_type=None):
    actual_seed = seed if seed is not None else _random_module.randint(0, 9999999)
    rng = _random_module.Random(actual_seed)

    full_pool = list(get_pool(ds))
    if not full_pool:
        log.error(f"No players found for data source: {ds}"); return None

    selected_players = select_players_by_fame(full_pool, difficulty, n=25, rng=rng)
    if not selected_players:
        log.error("Player selection returned empty list"); return None

    grid = build_grid_validated(grid_size, ds, difficulty, selected_players, rng=rng)
    if not grid:
        log.error("Grid build failed"); return None

    solutions = {}
    for i, cell in enumerate(grid):
        matching = [p.get("name", p.get("id")) for p in selected_players
                    if player_matches_cell(p, cell, ds)]
        solutions[str(i)] = matching[:20]

    state = {
        "data_source":        ds,
        "grid_size":          grid_size,
        "difficulty":         difficulty,
        "player_type":        player_type,
        "grid":               grid,
        "players":            selected_players,
        "current_player_idx": 0,
        "grid_state":         [None] * (grid_size * grid_size),
        "skips_used":         0,
        "wildcard_used":      False,
        "correct":            0,
        "wrong":              0,
        "started_at":         time.time(),
        "seed":               actual_seed,
        "solutions":          solutions,
    }
    return state

def elo_expected(a, b): return 1 / (1 + 10 ** ((b - a) / 400))
def elo_update(r, exp, act, k=32): return r + k * (act - exp)

DIFFICULTY_K        = {"easy": 12, "normal": 24, "hard": 40}
DIFFICULTY_PAR_BASE = {"easy": 600, "normal": 480, "hard": 320}

def calc_par(difficulty, grid_size, current_rating):
    base      = DIFFICULTY_PAR_BASE.get(difficulty, 480)
    adjust    = (current_rating - 1200) * 0.08
    size_mult = 1.0 if grid_size <= 3 else 1.25
    return (base + adjust) * size_mult

_ALLOWED_RATING_COLS = frozenset({"rating", "solo_rating"})

def get_user_rating(uid, sid, col="rating"):
    if col not in _ALLOWED_RATING_COLS:
        raise ValueError(f"Invalid rating column: {col!r}")
    row = query_db(f"SELECT {col} FROM season_ratings WHERE user_id=? AND season_id=?",
                   (uid, sid), one=True)
    return row[col] if row else 1200.0

def ensure_season_rating(uid, sid):
    query_db(
        "INSERT OR IGNORE INTO season_ratings(user_id,season_id,rating,solo_rating) VALUES(?,?,1200,1200)",
        (uid, sid), commit=True)

def rating_tier(r):
    if r < 1000:   return ("Beginner", "#9CA3AF", "🟤")
    elif r < 1200: return ("Amateur",  "#60A5FA", "🔵")
    elif r < 1400: return ("Pro",      "#34D399", "🟢")
    elif r < 1600: return ("Elite",    "#FBBF24", "🟡")
    else:          return ("Legend",   "#F87171", "🔴")

def get_user_rank(uid, sid, col="rating"):
    if col not in _ALLOWED_RATING_COLS:
        raise ValueError(f"Invalid rating column: {col!r}")
    rows = query_db(
        f"SELECT user_id FROM season_ratings WHERE season_id=? ORDER BY {col} DESC",
        (sid,))
    for i, r in enumerate(rows):
        if r["user_id"] == uid: return i + 1
    return None

def get_or_create_daily():
    today = date.today().isoformat()
    row = query_db("SELECT * FROM daily_challenge WHERE challenge_date=?", (today,), one=True)
    if row: return json.loads(row["game_state"])
    seed  = int(hashlib.sha256(today.encode()).hexdigest(), 16) % 9999999
    state = create_game_state("overall", 3, "normal", seed)
    if state:
        query_db("INSERT INTO daily_challenge(challenge_date,game_state) VALUES(?,?)",
                 (today, json.dumps(state, default=str)), commit=True)
    return state

def gen_room_code():
    return "".join(_random_module.choices(string.digits, k=6))

# ══════════════════════════════════════════════════════════════════════════════
#  STREAK SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

def get_streak_data(uid):
    row = query_db("SELECT * FROM daily_streaks WHERE user_id=?", (uid,), one=True)
    if not row:
        return {"current": 0, "best": 0, "freeze": 0, "last_played": None}
    return {
        "current": row["current_streak"],
        "best":    row["best_streak"],
        "freeze":  row["freeze_available"],
        "last_played": row["last_played_date"]
    }

def update_streak(uid):
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_days  = (date.today() - timedelta(days=2)).isoformat()

    row = query_db("SELECT * FROM daily_streaks WHERE user_id=?", (uid,), one=True)
    if not row:
        query_db(
            "INSERT INTO daily_streaks(user_id,current_streak,best_streak,last_played_date,freeze_available)"
            " VALUES(?,1,1,?,0)", (uid, today), commit=True)
        return {"current": 1, "best": 1, "is_new": True, "broken": False, "used_freeze": False}

    last = row["last_played_date"]
    if last == today:
        return {"current": row["current_streak"], "best": row["best_streak"],
                "is_new": False, "broken": False, "used_freeze": False}

    used_freeze = False
    if last == yesterday:
        consecutive = True
    elif last == two_days and row["freeze_available"] > 0:
        consecutive = True; used_freeze = True
    else:
        consecutive = False

    if consecutive:
        new_streak = row["current_streak"] + 1
        new_best   = max(new_streak, row["best_streak"])
        new_freeze = max(0, row["freeze_available"] - (1 if used_freeze else 0))
        if new_streak % 7 == 0:
            new_freeze = min(new_freeze + 1, 3)
        query_db(
            "UPDATE daily_streaks SET current_streak=?,best_streak=?,last_played_date=?,freeze_available=?"
            " WHERE user_id=?",
            (new_streak, new_best, today, new_freeze, uid), commit=True)
        return {"current": new_streak, "best": new_best,
                "is_new": True, "broken": False, "used_freeze": used_freeze}
    else:
        query_db(
            "UPDATE daily_streaks SET current_streak=1,last_played_date=? WHERE user_id=?",
            (today, uid), commit=True)
        return {"current": 1, "best": row["best_streak"],
                "is_new": True, "broken": True, "used_freeze": False}

# ══════════════════════════════════════════════════════════════════════════════
#  XP / LEVEL SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

XP_THRESHOLDS = [0, 100, 250, 500, 900, 1500, 2500, 4000, 6000, 9000,
                 13000, 18000, 25000, 35000, 50000]
LEVEL_NAMES   = [
    "Rookie", "Club Cricketer", "District Level", "State Level", "Ranji Pro",
    "Test Debut", "Test Regular", "Test Star", "Test Legend", "Test Icon",
    "National Hero", "World Class", "Living Legend", "Cricket God", "Cricket Immortal"
]

def xp_to_level(xp):
    for i in range(len(XP_THRESHOLDS) - 1, -1, -1):
        if xp >= XP_THRESHOLDS[i]:
            return min(i + 1, len(LEVEL_NAMES))
    return 1

def level_name(lvl):
    return LEVEL_NAMES[min(lvl - 1, len(LEVEL_NAMES) - 1)]

def xp_next_level(xp):
    lvl = xp_to_level(xp)
    if lvl >= len(XP_THRESHOLDS):
        return 0, 0
    needed        = XP_THRESHOLDS[lvl] if lvl < len(XP_THRESHOLDS) else XP_THRESHOLDS[-1]
    current_floor = XP_THRESHOLDS[lvl - 1]
    return needed - xp, needed - current_floor

def calc_xp_gain(score, accuracy, streak, difficulty):
    base       = max(10, int(score) // 10)
    acc_bonus  = round(accuracy * 0.5)
    diff_mult  = {"easy": 1.0, "normal": 1.5, "hard": 2.0}.get(difficulty, 1.0)
    streak_bon = min(streak * 5, 50)
    return max(5, round((base + acc_bonus + streak_bon) * diff_mult))

def get_xp_data(uid):
    row = query_db("SELECT * FROM user_xp WHERE user_id=?", (uid,), one=True)
    if not row:
        return {"total": 0, "level": 1, "name": "Rookie"}
    lvl = row["level"]
    return {"total": row["total_xp"], "level": lvl, "name": level_name(lvl)}

def update_xp(uid, gain):
    row = query_db("SELECT * FROM user_xp WHERE user_id=?", (uid,), one=True)
    if not row:
        query_db("INSERT INTO user_xp(user_id,total_xp,level) VALUES(?,?,1)", (uid, gain), commit=True)
        new_total, old_lvl, new_lvl = gain, 0, xp_to_level(gain)
    else:
        old_total = row["total_xp"]
        old_lvl   = row["level"]
        new_total = old_total + gain
        new_lvl   = xp_to_level(new_total)
        query_db("UPDATE user_xp SET total_xp=?,level=? WHERE user_id=?",
                 (new_total, new_lvl, uid), commit=True)
    return {
        "total":      new_total,
        "level":      new_lvl,
        "name":       level_name(new_lvl),
        "gained":     gain,
        "leveled_up": new_lvl > old_lvl,
        "old_level":  old_lvl,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  RARITY TRACKING
# ══════════════════════════════════════════════════════════════════════════════

def track_cell_picks_for_daily(challenge_date, filled_by):
    for idx_str, pname in filled_by.items():
        if not pname or pname.endswith("_wc"):
            continue
        try:
            cidx = int(idx_str)
        except (ValueError, TypeError):
            continue
        query_db(
            """INSERT INTO cell_picks(challenge_date,cell_index,player_name,pick_count)
               VALUES(?,?,?,1)
               ON CONFLICT(challenge_date,cell_index,player_name)
               DO UPDATE SET pick_count=pick_count+1""",
            (challenge_date, cidx, str(pname)[:120]), commit=True)

def get_rarity_for_cells(challenge_date, n_cells):
    result = []
    for cidx in range(n_cells):
        total_row = query_db(
            "SELECT SUM(pick_count) as t FROM cell_picks WHERE challenge_date=? AND cell_index=?",
            (challenge_date, cidx), one=True)
        top_row = query_db(
            "SELECT player_name,pick_count FROM cell_picks WHERE challenge_date=? AND cell_index=?"
            " ORDER BY pick_count DESC LIMIT 5",
            (challenge_date, cidx))
        total = total_row["t"] or 0
        result.append({
            "total": total,
            "top":   [{"name": r["player_name"], "count": r["pick_count"]} for r in top_row],
        })
    return result

# ═══════════════════════════════════════════════════════════════════════════════
#  DESIGN SYSTEM — Outfit + DM Sans
# ═══════════════════════════════════════════════════════════════════════════════

GOOGLE_ANALYTICS = """<script async src="https://www.googletagmanager.com/gtag/js?id=G-JGCTR9L8JJ"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-JGCTR9L8JJ');</script>"""

SEO_META = """
<meta name="description" content="Cricket Bingo – Match IPL cricket legends to their teams, nations and trophies. Play solo, compete in rated matches, or challenge friends.">
<meta name="keywords" content="cricket bingo, IPL quiz, cricket game, IPL teams, cricket trivia">
<meta name="author" content="Cricket Bingo">
<meta property="og:type" content="website">
<meta property="og:title" content="Cricket Bingo – IPL Player Quiz Game">
<meta property="og:description" content="Match cricket legends to teams, nations & trophies. Compete online!">
<meta property="og:image" content="/public/csk.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="robots" content="index, follow">
"""

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');

:root {
  --bg:#0A0C12;--bg2:#0F1118;--sur:#161923;--sur2:#1B1F2B;--sur3:#222736;--sur4:#2A2F3E;
  --bdr:rgba(255,255,255,.07);--bdr2:rgba(255,255,255,.11);--bdr3:rgba(255,255,255,.18);
  --acc:#F5A623;--acc2:#D48E1A;--acc-dim:rgba(245,166,35,.12);--acc-glow:rgba(245,166,35,.22);
  --blue:#4F8EF7;--red:#F0524F;--green:#2DD36F;--pur:#9B72F7;--teal:#2EC4B6;
  --txt:#EDF0F7;--txt2:#8591A8;--txt3:#424C61;
  --font-head:'Outfit',sans-serif;--font-body:'DM Sans',sans-serif;
  --r-sm:5px;--r-md:8px;--r-lg:12px;--r-xl:16px;--r-2xl:22px;
  --shadow:0 4px 24px rgba(0,0,0,.55);--shadow-lg:0 16px 56px rgba(0,0,0,.7);
}
[data-theme="light"] {
  --bg:#F2F4F9;--bg2:#E9EDF5;--sur:#FFFFFF;--sur2:#F5F7FC;--sur3:#EDF0F7;--sur4:#E2E6F0;
  --bdr:rgba(0,0,0,.07);--bdr2:rgba(0,0,0,.11);--bdr3:rgba(0,0,0,.18);
  --acc:#D48E1A;--acc2:#B87A12;--acc-dim:rgba(212,142,26,.1);--acc-glow:rgba(212,142,26,.18);
  --txt:#0F1420;--txt2:#4A5468;--txt3:#9BA5B8;
  --shadow:0 4px 24px rgba(0,0,0,.09);--shadow-lg:0 16px 56px rgba(0,0,0,.13);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{font-family:var(--font-body);background:var(--bg);color:var(--txt);min-height:100vh;overflow-x:hidden;line-height:1.55;-webkit-font-smoothing:antialiased;transition:background .3s,color .3s;}
.nav{height:58px;background:var(--bg2);border-bottom:1px solid var(--bdr);display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:500;backdrop-filter:blur(12px);}
.nav-logo{display:flex;align-items:center;gap:9px;text-decoration:none;font-family:var(--font-head);font-weight:800;font-size:1.05rem;color:var(--acc);letter-spacing:-.3px;}
.nav-logo-icon{font-size:1.15rem;}
.nav-links{display:flex;align-items:center;}
.nav-link{color:var(--txt2);font-size:.875rem;font-weight:500;font-family:var(--font-body);padding:8px 14px;text-decoration:none;transition:color .15s;border-radius:var(--r-md);}
.nav-link:hover{color:var(--txt);background:var(--sur2);}
.nav-actions{display:flex;align-items:center;gap:8px;}
.nav-burger{display:none;flex-direction:column;gap:5px;cursor:pointer;padding:8px;}
.nav-burger span{width:20px;height:2px;background:var(--txt2);border-radius:2px;display:block;transition:.3s;}
.mobile-menu{display:none;position:fixed;top:58px;left:0;right:0;background:var(--bg2);border-bottom:1px solid var(--bdr);padding:8px 16px 16px;flex-direction:column;gap:2px;z-index:499;backdrop-filter:blur(12px);}
.mobile-menu.open{display:flex;}
.mobile-menu .nav-link{padding:12px 14px;border-radius:var(--r-lg);font-size:.9rem;}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:9px 18px;border-radius:var(--r-md);font-family:var(--font-body);font-size:.875rem;font-weight:600;cursor:pointer;border:none;transition:all .18s;text-decoration:none;white-space:nowrap;line-height:1;letter-spacing:-.1px;}
.btn:disabled{opacity:.38;cursor:not-allowed;pointer-events:none;}
.btn:focus-visible{outline:2px solid var(--acc);outline-offset:2px;}
.btn-primary{background:var(--acc);color:#000;font-weight:700;}
.btn-primary:hover{background:var(--acc2);transform:translateY(-1px);}
.btn-secondary{background:var(--sur2);color:var(--txt);border:1px solid var(--bdr2);}
.btn-secondary:hover{background:var(--sur3);border-color:var(--bdr3);}
.btn-outline{background:transparent;color:var(--txt2);border:1px solid var(--bdr2);}
.btn-outline:hover{color:var(--txt);border-color:var(--bdr3);background:var(--sur2);}
.btn-ghost{background:transparent;color:var(--txt2);border:none;}
.btn-ghost:hover{color:var(--txt);background:var(--sur2);}
.btn-danger{background:var(--red);color:#fff;}
.btn-danger:hover{filter:brightness(1.1);}
.btn-lg{padding:12px 26px;font-size:1rem;border-radius:var(--r-lg);}
.btn-sm{padding:6px 13px;font-size:.8rem;}
.btn-xs{padding:4px 9px;font-size:.72rem;}
.w-full{width:100%;}
.container{max-width:1160px;margin:0 auto;padding:0 28px;}
.container-sm{max-width:740px;margin:0 auto;padding:0 28px;}
.container-xs{max-width:520px;margin:0 auto;padding:0 28px;}
.page{padding:40px 0 88px;}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;}
.flex{display:flex;}.flex-col{flex-direction:column;}
.items-center{align-items:center;}.justify-between{justify-content:space-between;}
.justify-center{justify-content:center;}.flex-wrap{flex-wrap:wrap;}
.text-center{text-align:center;}
.gap-2{gap:8px;}.gap-3{gap:12px;}.gap-4{gap:16px;}.gap-6{gap:24px;}
.mt-2{margin-top:8px;}.mt-3{margin-top:12px;}.mt-4{margin-top:16px;}.mt-6{margin-top:24px;}.mt-8{margin-top:32px;}
.mb-2{margin-bottom:8px;}.mb-3{margin-bottom:12px;}.mb-4{margin-bottom:16px;}.mb-6{margin-bottom:24px;}.mb-8{margin-bottom:32px;}
.text-acc{color:var(--acc);}.text-muted{color:var(--txt2);}.text-subtle{color:var(--txt3);}
.text-green{color:var(--green);}.text-red{color:var(--red);}.text-blue{color:var(--blue);}.text-pur{color:var(--pur);}
.display{font-family:var(--font-head);font-size:clamp(2.1rem,5.5vw,3.4rem);font-weight:800;letter-spacing:-1.5px;line-height:1.08;}
.title{font-family:var(--font-head);font-size:clamp(1.3rem,3vw,1.75rem);font-weight:700;letter-spacing:-.4px;}
.heading{font-family:var(--font-head);font-size:1rem;font-weight:700;}
.subhead{font-size:.9rem;color:var(--txt2);}
.label{display:block;font-size:.72rem;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;font-family:var(--font-body);}
.card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-xl);padding:20px;}
.card-sm{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-lg);padding:16px;}
.card-hover{transition:border-color .2s,transform .2s,box-shadow .2s;cursor:pointer;}
.card-hover:hover{border-color:var(--bdr3);transform:translateY(-2px);box-shadow:var(--shadow);}
.card-accent{border-color:rgba(245,166,35,.35)!important;}
.card-glow{box-shadow:0 0 0 1px rgba(245,166,35,.18),0 8px 32px rgba(0,0,0,.4);}
.input{background:var(--sur2);border:1px solid var(--bdr2);border-radius:var(--r-md);padding:10px 13px;color:var(--txt);font-size:.875rem;font-family:var(--font-body);width:100%;outline:none;transition:border-color .2s,box-shadow .2s;}
.input:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-glow);}
.input::placeholder{color:var(--txt3);}
select.input option{background:var(--sur);color:var(--txt);}
.input-group{display:flex;flex-direction:column;}
.section-header{display:flex;align-items:center;gap:14px;margin-bottom:20px;}
.section-header h2{font-family:var(--font-head);font-size:.75rem;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.12em;white-space:nowrap;}
.section-header::after{content:'';flex:1;height:1px;background:var(--bdr);}
.tab-bar{display:flex;gap:2px;background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-lg);padding:4px;width:fit-content;margin-bottom:28px;}
.tab-btn{padding:7px 16px;border-radius:var(--r-md);font-size:.83rem;font-weight:500;color:var(--txt2);cursor:pointer;border:none;background:transparent;font-family:var(--font-body);transition:all .18s;display:flex;align-items:center;gap:6px;}
.tab-btn.active{background:var(--sur3);color:var(--txt);font-weight:600;}
.tab-btn:hover:not(.active){color:var(--txt);}
.table-wrap{overflow-x:auto;border-radius:var(--r-xl);border:1px solid var(--bdr);}
table{width:100%;border-collapse:collapse;}
th{padding:11px 16px;text-align:left;font-size:.7rem;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;background:var(--sur2);border-bottom:1px solid var(--bdr);font-family:var(--font-body);}
td{padding:13px 16px;font-size:.875rem;border-bottom:1px solid var(--bdr);color:var(--txt2);}
tr:last-child td{border-bottom:none;}
tr:hover td{background:var(--sur2);}
.stat-card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-xl);padding:18px 14px;text-align:center;}
.stat-value{font-family:var(--font-head);font-size:1.9rem;font-weight:800;line-height:1;letter-spacing:-1px;color:var(--txt);}
.stat-label{font-size:.68rem;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;margin-top:5px;}
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:99px;font-size:.68rem;font-weight:700;border:1px solid currentColor;font-family:var(--font-body);}
.timer-wrap{background:var(--sur3);border-radius:99px;height:5px;overflow:hidden;}
.timer-bar{height:100%;border-radius:99px;transition:width 1s linear,background .4s;}
.bingo-grid{display:grid;gap:14px;margin:0 auto;width:100%;}
.bingo-grid.size-3{grid-template-columns:repeat(3,1fr);max-width:680px;}
.bingo-grid.size-4{grid-template-columns:repeat(4,1fr);max-width:820px;}
.cell{background:var(--sur2);border:1.5px solid var(--bdr);border-radius:var(--r-xl);padding:12px 8px;text-align:center;cursor:pointer;transition:border-color .2s,background .2s,transform .15s,box-shadow .2s;min-height:140px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;user-select:none;overflow:hidden;position:relative;}
.cell-logo{width:90px;height:90px;object-fit:contain;border-radius:10px;transition:transform .2s;}
.cell-label{font-size:.72rem;font-weight:600;color:var(--txt2);line-height:1.3;font-family:var(--font-body);}
.cell.nation-cell{font-size:.95rem;font-weight:700;color:var(--txt);}
.cell.trophy-cell{font-size:.78rem;font-weight:600;color:var(--acc);}
.cell.combo-cell{font-size:.68rem;font-weight:600;color:var(--pur);line-height:1.45;}
.cell:hover:not(.filled):not(.cell-disabled):not(.wc-filled){border-color:var(--acc);background:var(--acc-dim);transform:translateY(-2px);box-shadow:0 6px 20px var(--acc-glow);}
.cell:hover .cell-logo{transform:scale(1.06);}
.cell.filled{background:rgba(45,211,111,.09);border-color:rgba(45,211,111,.45);cursor:default;animation:cell-fill-glow 1.2s ease forwards;}
.cell.filled .cell-logo{filter:brightness(0.85);}
.cell.filled .cell-label{opacity:.4;}
.cell.filled::after{content:'✓';position:absolute;bottom:6px;right:8px;font-size:.72rem;color:var(--green);font-weight:700;opacity:0;animation:check-appear .5s .4s ease forwards;}
@keyframes cell-fill-glow{0%{background:rgba(45,211,111,.25);box-shadow:0 0 20px rgba(45,211,111,.35);}100%{background:rgba(45,211,111,.09);box-shadow:none;}}
@keyframes check-appear{from{opacity:0;transform:scale(0);}to{opacity:1;transform:scale(1);}}
.cell.wildcard-hint{border-color:var(--acc);background:var(--acc-dim);animation:wc-pulse 1.5s ease infinite;}
@keyframes wc-pulse{0%,100%{box-shadow:0 0 0 0 var(--acc-glow);}50%{box-shadow:0 0 0 6px rgba(245,166,35,0);}}
.cell.wrong{animation:cell-shake .4s ease;border-color:var(--red);background:rgba(240,82,79,.08);}
@keyframes cell-shake{0%,100%{transform:translateX(0);}25%{transform:translateX(-7px);}75%{transform:translateX(7px);}}
.cell.wc-filled{background:rgba(155,114,247,.1);border-color:rgba(155,114,247,.45);cursor:default;animation:wc-fill .6s ease forwards;}
.cell.wc-filled::after{content:'✦';position:absolute;bottom:6px;right:8px;font-size:.7rem;color:var(--pur);font-weight:700;opacity:0;animation:check-appear .4s .2s ease forwards;}
@keyframes wc-fill{0%{background:rgba(155,114,247,.3);box-shadow:0 0 18px rgba(155,114,247,.35);}100%{background:rgba(155,114,247,.1);box-shadow:none;}}
.player-card{background:var(--sur);border:1px solid var(--bdr2);border-radius:var(--r-xl);padding:22px 26px;text-align:center;}
.player-name{font-family:var(--font-head);font-size:clamp(1.25rem,3.5vw,2rem);font-weight:800;color:var(--acc);letter-spacing:-.5px;}
.player-hint{font-size:.78rem;color:var(--txt3);}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;z-index:1000;padding:16px;animation:fade-in .2s ease;}
.modal{background:var(--sur);border:1px solid var(--bdr2);border-radius:var(--r-2xl);padding:36px;max-width:440px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);animation:slide-up .26s ease;}
@keyframes fade-in{from{opacity:0;}to{opacity:1;}}
@keyframes slide-up{from{transform:translateY(22px);opacity:0;}to{transform:none;opacity:1;}}
@keyframes rating-up{0%{transform:translateY(12px);opacity:0;color:var(--green);}100%{transform:none;opacity:1;}}
@keyframes rating-down{0%{transform:translateY(-12px);opacity:0;color:var(--red);}100%{transform:none;opacity:1;}}
.rating-anim-up{animation:rating-up .55s ease forwards;color:var(--green)!important;}
.rating-anim-down{animation:rating-down .55s ease forwards;color:var(--red)!important;}
#toasts{position:fixed;bottom:22px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;}
.toast{background:var(--sur2);border:1px solid var(--bdr2);border-radius:var(--r-lg);padding:11px 15px;font-size:.82rem;font-weight:500;max-width:260px;display:flex;align-items:center;gap:8px;box-shadow:var(--shadow);animation:toast-in .22s ease;font-family:var(--font-body);}
.toast-success{border-left:3px solid var(--green);}
.toast-error{border-left:3px solid var(--red);}
.toast-info{border-left:3px solid var(--blue);}
.toast-warn{border-left:3px solid var(--acc);}
@keyframes toast-in{from{transform:translateX(16px);opacity:0;}to{transform:none;opacity:1;}}
.spinner{width:32px;height:32px;border-radius:50%;border:3px solid var(--sur3);border-top-color:var(--acc);animation:spin .7s linear infinite;margin:0 auto;}
@keyframes spin{to{transform:rotate(360deg);}}
.room-code-display{font-family:var(--font-head);font-size:2.5rem;font-weight:700;letter-spacing:14px;color:var(--acc);text-align:center;padding:20px;background:var(--acc-dim);border-radius:var(--r-xl);border:1px solid rgba(245,166,35,.28);cursor:pointer;transition:all .18s;}
.room-code-display:hover{background:rgba(245,166,35,.18);}
.mm-card{max-width:400px;margin:80px auto;text-align:center;background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-2xl);padding:52px 40px;}
.mm-dots{display:flex;justify-content:center;gap:6px;margin-bottom:26px;}
.mm-dots span{width:9px;height:9px;background:var(--acc);border-radius:50%;animation:mm-pulse 1.4s ease infinite;}
.mm-dots span:nth-child(2){animation-delay:.22s;}.mm-dots span:nth-child(3){animation-delay:.44s;}
@keyframes mm-pulse{0%,100%{opacity:.25;transform:scale(.85);}50%{opacity:1;transform:scale(1);}}
.score-display{font-family:var(--font-head);font-size:3.2rem;font-weight:800;letter-spacing:-2px;color:var(--acc);line-height:1;}
.progress-wrap{background:var(--sur3);border-radius:99px;overflow:hidden;}
.progress-bar{height:100%;border-radius:99px;transition:width .4s ease;}
.step-card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-2xl);padding:30px;max-width:540px;margin:0 auto;animation:fade-in .25s ease;}
.mode-btn{background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-xl);padding:20px 14px;text-align:center;cursor:pointer;transition:all .18s;font-family:var(--font-body);}
.mode-btn:hover{border-color:var(--acc);background:var(--acc-dim);transform:translateY(-2px);box-shadow:0 6px 20px var(--acc-glow);}
.mode-btn .mode-icon{font-size:1.5rem;display:block;margin-bottom:8px;}
.mode-btn .mode-title{font-family:var(--font-head);font-size:.9rem;font-weight:700;color:var(--txt);display:block;margin-bottom:3px;}
.mode-btn .mode-sub{font-size:.74rem;color:var(--txt2);display:block;}
.feature-card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-xl);padding:24px 20px;text-align:center;transition:border-color .18s,transform .18s;}
.feature-card:hover{border-color:var(--bdr3);transform:translateY(-3px);}
.feature-icon{width:48px;height:48px;border-radius:var(--r-lg);background:var(--acc-dim);border:1px solid rgba(245,166,35,.18);display:flex;align-items:center;justify-content:center;font-size:1.4rem;margin:0 auto 14px;}
.feature-card h3{font-family:var(--font-head);font-size:.9rem;font-weight:700;margin-bottom:6px;}
.feature-card p{font-size:.82rem;color:var(--txt2);line-height:1.65;}
.hero-section{text-align:center;padding:68px 0 52px;}
.hero-badge{display:inline-flex;align-items:center;gap:7px;background:var(--acc-dim);border:1px solid rgba(245,166,35,.28);color:var(--acc);font-size:.76rem;font-weight:600;padding:5px 14px;border-radius:99px;margin-bottom:20px;font-family:var(--font-body);}
.opp-bar{background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-xl);padding:12px 16px;}
.opp-score-num{font-family:var(--font-head);font-size:1.4rem;font-weight:800;color:var(--red);transition:all .3s;}
.opp-score-num.pulse{animation:opp-pulse .5s ease;}
@keyframes opp-pulse{0%,100%{transform:scale(1);}50%{transform:scale(1.2);}}
.solutions-grid{display:flex;flex-wrap:wrap;gap:6px;}
.solution-tag{background:var(--sur2);border:1px solid var(--bdr2);border-radius:99px;padding:3px 11px;font-size:.72rem;color:var(--txt2);font-family:var(--font-body);}
.footer{background:var(--bg2);border-top:1px solid var(--bdr);padding:44px 28px 32px;margin-top:64px;}
.footer-grid{max-width:1160px;margin:0 auto;display:grid;grid-template-columns:1.8fr 1fr 1fr 1fr;gap:44px;margin-bottom:36px;}
.footer-brand p{font-size:.83rem;color:var(--txt2);line-height:1.8;margin-top:10px;}
.footer-col h4{font-family:var(--font-head);font-size:.76rem;font-weight:700;color:var(--txt);margin-bottom:14px;text-transform:uppercase;letter-spacing:.07em;}
.footer-col a{display:block;color:var(--txt2);font-size:.83rem;text-decoration:none;margin-bottom:9px;transition:color .15s;}
.footer-col a:hover{color:var(--acc);}
.footer-bottom{max-width:1160px;margin:0 auto;padding-top:22px;border-top:1px solid var(--bdr);}
.footer-bottom p{font-size:.74rem;color:var(--txt3);}
.theme-toggle{width:36px;height:36px;background:var(--sur2);border:1px solid var(--bdr2);border-radius:var(--r-md);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1rem;transition:all .18s;flex-shrink:0;}
.theme-toggle:hover{background:var(--sur3);border-color:var(--bdr3);}
hr{border:none;border-top:1px solid var(--bdr);margin:22px 0;}
::-webkit-scrollbar{width:5px;}
::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:var(--sur3);border-radius:3px;}
.streak-badge{display:inline-flex;align-items:center;gap:4px;background:rgba(245,166,35,.12);border:1px solid rgba(245,166,35,.25);border-radius:99px;padding:4px 10px;font-family:var(--font-head);font-size:.8rem;font-weight:700;color:var(--acc);cursor:default;transition:all .18s;}
.streak-badge:hover{background:rgba(245,166,35,.2);}
.streak-fire{font-size:.95rem;animation:fire-flicker 2s ease-in-out infinite;}
@keyframes fire-flicker{0%,100%{transform:scaleY(1);}50%{transform:scaleY(1.08);}}
.xp-bar-wrap{height:3px;background:var(--sur3);border-radius:99px;overflow:hidden;margin-top:4px;}
.xp-bar{height:100%;border-radius:99px;background:var(--pur);transition:width .6s ease;}
.level-badge{display:inline-flex;align-items:center;gap:5px;font-size:.72rem;font-weight:700;color:var(--pur);background:rgba(155,114,247,.12);border:1px solid rgba(155,114,247,.25);border-radius:99px;padding:3px 9px;font-family:var(--font-head);}
.share-grid-wrap{background:var(--sur2);border:1px solid var(--bdr2);border-radius:var(--r-lg);padding:14px 16px;margin:12px 0;font-family:monospace;font-size:1.3rem;letter-spacing:2px;text-align:center;line-height:1.6;}
.share-btn-row{display:flex;gap:8px;margin-top:10px;}
.share-btn-row .btn{flex:1;font-size:.78rem;}
.rarity-common{background:rgba(148,163,184,.15);color:#94a3b8;}
.rarity-rare{background:rgba(34,197,94,.12);color:var(--green);}
.rarity-epic{background:rgba(79,142,247,.12);color:var(--blue);}
.rarity-legendary{background:rgba(245,166,35,.18);color:var(--acc);}
.rarity-chip{display:inline-flex;align-items:center;gap:4px;border-radius:99px;padding:2px 8px;font-size:.65rem;font-weight:700;font-family:var(--font-head);border:1px solid currentColor;}
.levelup-overlay{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;z-index:2000;pointer-events:none;}
.levelup-card{background:var(--sur);border:2px solid var(--pur);border-radius:var(--r-2xl);padding:32px 40px;text-align:center;box-shadow:0 0 60px rgba(155,114,247,.4);animation:levelup-pop .5s cubic-bezier(.34,1.56,.64,1) forwards;pointer-events:all;}
@keyframes levelup-pop{from{transform:scale(.5);opacity:0;}to{transform:scale(1);opacity:1;}}
.sound-btn{width:32px;height:32px;background:var(--sur2);border:1px solid var(--bdr2);border-radius:var(--r-md);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:.9rem;transition:all .18s;flex-shrink:0;}
.sound-btn:hover{background:var(--sur3);}
.rating-result-box{background:var(--sur2);border:1px solid var(--bdr2);border-radius:var(--r-xl);padding:20px;text-align:center;margin-top:20px;}
.rating-result-box .new-rating-num{font-family:var(--font-head);font-size:2.6rem;font-weight:800;letter-spacing:-1.5px;line-height:1;}
.rating-delta{font-size:1.1rem;font-weight:700;margin-top:6px;}
.rating-delta.pos{color:var(--green);}
.rating-delta.neg{color:var(--red);}
@media(max-width:1024px){.footer-grid{grid-template-columns:1fr 1fr;}.container,.container-sm{padding:0 20px;}}
@media(max-width:768px){.nav-links{display:none;}.nav-burger{display:flex;}.nav{padding:0 16px;}.grid-3{grid-template-columns:1fr 1fr;}.grid-4{grid-template-columns:1fr 1fr;}.footer-grid{grid-template-columns:1fr;gap:24px;}.hide-sm{display:none;}.hero-section{padding:44px 0 32px;}.bingo-grid.size-3{max-width:100%;}.bingo-grid.size-4{max-width:100%;}}
@media(max-width:520px){.grid-2{grid-template-columns:1fr;}.cell{min-height:100px;padding:10px 6px;}.cell-logo{width:60px;height:60px;}.container,.container-sm,.container-xs{padding:0 12px;}.tab-bar{width:100%;}.tab-btn{flex:1;justify-content:center;font-size:.78rem;padding:7px 10px;}.modal{padding:24px 18px;}}
</style>
"""

def NAV_HTML():
    return """
<nav class="nav">
  <a class="nav-logo" href="/">
    <span class="nav-logo-icon">⚡</span>
    <span>Cricket Bingo</span>
  </a>
  <div class="nav-links">
    <a class="nav-link" href="/">Play</a>
    <a class="nav-link" href="/leaderboard">Leaderboard</a>
    <a class="nav-link" href="/daily">Daily</a>
    <a class="nav-link" href="/about">About</a>
    <a class="nav-link" href="/contact">Contact</a>
  </div>
  <div class="nav-actions">
    {% if current_user.is_authenticated %}
      {% if streak_current and streak_current > 0 %}
      <span class="streak-badge hide-sm" title="{{ streak_current }}-day streak!">
        <span class="streak-fire">🔥</span> {{ streak_current }}
      </span>
      {% endif %}
      {% if user_level and user_level > 1 %}
      <span class="level-badge hide-sm" title="Level {{ user_level }}: {{ user_level_name }}">Lv {{ user_level }}</span>
      {% endif %}
      <a href="/profile/{{ current_user.id }}" style="display:flex;align-items:center;gap:7px;text-decoration:none;color:var(--txt2);font-size:.85rem;font-weight:500;padding:4px 0;">
        <img src="{{ current_user.avatar or '' }}" style="width:28px;height:28px;border-radius:50%;object-fit:cover;border:2px solid var(--acc);"
          onerror="this.style.display='none'" alt="{{ current_user.name }}">
        <span class="hide-sm">{{ current_user.name.split()[0] }}</span>
      </a>
      <a href="/logout" class="btn btn-outline btn-sm">Sign Out</a>
    {% else %}
      <a href="/login/google" class="btn btn-primary btn-sm">Sign In</a>
    {% endif %}
    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme" id="themeBtn">☀️</button>
    <div class="nav-burger" onclick="toggleMenu()" id="navBurger" aria-label="Menu" aria-expanded="false">
      <span></span><span></span><span></span>
    </div>
  </div>
</nav>
<div class="mobile-menu" id="mmenu">
  <a class="nav-link" href="/" onclick="closeMenu()">🏠 Play</a>
  <a class="nav-link" href="/leaderboard" onclick="closeMenu()">🏆 Leaderboard</a>
  <a class="nav-link" href="/daily" onclick="closeMenu()">📅 Daily</a>
  <a class="nav-link" href="/about" onclick="closeMenu()">ℹ️ About</a>
  <a class="nav-link" href="/contact" onclick="closeMenu()">✉️ Contact</a>
  <a class="nav-link" href="/privacy" onclick="closeMenu()">🔒 Privacy</a>
  <a class="nav-link" href="/terms" onclick="closeMenu()">📋 Terms</a>
  {% if current_user.is_authenticated %}
    <a class="nav-link" href="/profile/{{ current_user.id }}" onclick="closeMenu()">👤 My Profile</a>
    <a class="nav-link" href="/logout" onclick="closeMenu()">← Sign Out</a>
  {% else %}
    <a class="nav-link" href="/login/google" onclick="closeMenu()">🔑 Sign In with Google</a>
  {% endif %}
</div>
"""

FOOTER_HTML = """
<footer class="footer">
  <div class="footer-grid">
    <div class="footer-brand">
      <a href="/" style="display:inline-flex;align-items:center;gap:9px;text-decoration:none;font-family:'Outfit',sans-serif;font-weight:800;font-size:1rem;color:var(--acc);">
        <span style="font-size:1.1rem;">⚡</span> Cricket Bingo
      </a>
      <p>The ultimate IPL cricket quiz. Match legends to their teams, nations &amp; trophies.</p>
      <p style="font-size:.74rem;color:var(--txt3);margin-top:6px;">Fan-made · Not affiliated with BCCI or IPL</p>
    </div>
    <div class="footer-col"><h4>Play</h4><a href="/">Home</a><a href="/daily">Daily Challenge</a><a href="/leaderboard">Leaderboard</a></div>
    <div class="footer-col"><h4>Info</h4><a href="/about">About</a><a href="/contact">Contact</a></div>
    <div class="footer-col"><h4>Legal</h4><a href="/privacy">Privacy Policy</a><a href="/terms">Terms</a></div>
  </div>
  <div class="footer-bottom flex justify-between items-center flex-wrap gap-3">
    <p>© 2025 Cricket Bingo · Fan-made IPL quiz game</p>
    <button onclick="toggleTheme()" style="background:var(--sur2);border:1px solid var(--bdr2);color:var(--txt2);padding:6px 14px;border-radius:var(--r-md);font-size:.76rem;cursor:pointer;font-family:var(--font-body);">Toggle Theme</button>
  </div>
</footer>
"""

GLOBAL_SCRIPTS = """
<div id="toasts" role="status" aria-live="polite"></div>
<script>
(function(){
  const saved = localStorage.getItem('cb-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcon(saved);
})();
function updateThemeIcon(theme){
  const btn = document.getElementById('themeBtn');
  if(btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
}
function toggleTheme(){
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('cb-theme', next);
  updateThemeIcon(next);
}
function toast(msg, type='info'){
  const d = document.createElement('div');
  d.className = 'toast toast-' + type;
  d.setAttribute('role','alert');
  d.textContent = msg;
  document.getElementById('toasts').appendChild(d);
  setTimeout(()=>{
    d.style.opacity='0'; d.style.transform='translateX(20px)'; d.style.transition='.25s ease';
    setTimeout(()=>d.remove(), 280);
  }, 2800);
}
function toggleMenu(){
  const m=document.getElementById('mmenu');
  const b=document.getElementById('navBurger');
  const isOpen=m.classList.toggle('open');
  if(b) b.setAttribute('aria-expanded', isOpen);
}
function closeMenu(){
  document.getElementById('mmenu').classList.remove('open');
  const b=document.getElementById('navBurger');
  if(b) b.setAttribute('aria-expanded','false');
}
document.addEventListener('click', e => {
  const m=document.getElementById('mmenu');
  if(m && !m.contains(e.target) && !e.target.closest('#navBurger')) m.classList.remove('open');
});
document.addEventListener('keydown', e => { if(e.key==='Escape') closeMenu(); });
</script>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  HOME BODY
# ═══════════════════════════════════════════════════════════════════════════════

HOME_BODY = """
<div class="container page">
  <div class="hero-section">
    <div class="hero-badge">🏏 IPL Edition · Season 2025</div>
    <h1 class="display mb-4">Cricket <span class="text-acc">Bingo</span></h1>
    <p class="subhead" style="font-size:1.05rem;max-width:480px;margin:0 auto 32px;">
      Match IPL cricket legends to their teams, nations &amp; trophies.<br>Compete solo or challenge friends online.
    </p>
    <div id="setupWizard">
      <!-- Step 1: Mode -->
      <div id="step1" class="step-card">
        <div class="section-header"><h2>Choose Mode</h2></div>
        <div class="grid-2 gap-3">
          <div class="mode-btn" onclick="selectMode('solo')">
            <span class="mode-icon">🎯</span>
            <span class="mode-title">Solo Practice</span>
            <span class="mode-sub">Play at your own pace · No pressure</span>
          </div>
          <div class="mode-btn" onclick="selectMode('rated')">
            <span class="mode-icon">⚡</span>
            <span class="mode-title">Rated Match</span>
            <span class="mode-sub">Compete vs real players · Earn rating</span>
          </div>
          <div class="mode-btn" onclick="selectMode('friends')">
            <span class="mode-icon">👥</span>
            <span class="mode-title">Play with Friends</span>
            <span class="mode-sub">Create a private room · Share code</span>
          </div>
          <div class="mode-btn" onclick="window.location='/daily'">
            <span class="mode-icon">📅</span>
            <span class="mode-title">Daily Challenge</span>
            <span class="mode-sub">Same puzzle for everyone · Streak rewards</span>
          </div>
        </div>
      </div>

      <!-- Step 2: Settings -->
      <div id="step2" class="step-card" style="display:none;">
        <div class="section-header"><h2>Game Settings</h2></div>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div class="input-group">
            <label class="label">Data Source</label>
            <select class="input" id="dataSource">
              <option value="overall">Overall (All IPL seasons)</option>
              <option value="ipl26">IPL 2026</option>
            </select>
          </div>
          <div class="input-group">
            <label class="label">Grid Size</label>
            <select class="input" id="gridSize">
              <option value="3">3×3 (9 cells)</option>
              <option value="4">4×4 (16 cells)</option>
            </select>
          </div>
          <div class="input-group">
            <label class="label">Difficulty</label>
            <select class="input" id="difficulty">
              <option value="easy">Easy – Famous players only</option>
              <option value="normal" selected>Normal – Mix of players</option>
              <option value="hard">Hard – Includes obscure players</option>
            </select>
          </div>
          <div class="flex gap-3 mt-2">
            <button class="btn btn-secondary w-full" onclick="showStep(1)">← Back</button>
            <button class="btn btn-primary w-full" onclick="startGame()" id="startBtn">
              Start Game →
            </button>
          </div>
        </div>
      </div>

      <!-- Step 2b: Friends Room -->
      <div id="step2friends" class="step-card" style="display:none;">
        <div class="section-header"><h2>Friends Room</h2></div>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div class="input-group">
            <label class="label">Data Source</label>
            <select class="input" id="friendsDataSource">
              <option value="overall">Overall (All IPL seasons)</option>
              <option value="ipl26">IPL 2026</option>
            </select>
          </div>
          <button class="btn btn-primary w-full" onclick="createFriendsRoom()">Create Room</button>
          <div style="text-align:center;color:var(--txt3);font-size:.8rem;">— or join existing —</div>
          <div style="display:flex;gap:8px;">
            <input class="input" id="joinCodeInput" placeholder="Enter 6-digit room code" maxlength="6" style="text-align:center;letter-spacing:6px;font-size:1.1rem;font-family:var(--font-head);">
            <button class="btn btn-secondary" onclick="joinRoom()">Join</button>
          </div>
          <button class="btn btn-ghost w-full" onclick="showStep(1)">← Back</button>
        </div>
      </div>
    </div>
  </div>

  <div style="max-width:900px;margin:0 auto;">
    <div class="section-header"><h2>Features</h2></div>
    <div class="grid-3 gap-4">
      <div class="feature-card">
        <div class="feature-icon">⚡</div>
        <h3>Live Rated Matches</h3>
        <p>Real-time 1v1 battles. Earn or lose ELO rating. Climb the seasonal leaderboard.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">📅</div>
        <h3>Daily Challenge</h3>
        <p>A new puzzle every day. Build your streak. Discover how rare your picks are.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">🏆</div>
        <h3>500+ IPL Players</h3>
        <p>From legendary champions to today's stars. Fame-based difficulty keeps it fair.</p>
      </div>
    </div>
  </div>
</div>

<script>
let selectedMode = 'solo';
function selectMode(m){
  selectedMode = m;
  if(m === 'friends'){
    {% if not current_user.is_authenticated %}
      window.location = '/login/google';
      return;
    {% endif %}
    showStep('2friends');
  } else if(m === 'rated'){
    {% if not current_user.is_authenticated %}
      window.location = '/login/google';
      return;
    {% endif %}
    showStep(2);
  } else {
    showStep(2);
  }
}
function showStep(n){
  ['step1','step2','step2friends'].forEach(id => {
    const el = document.getElementById(id);
    if(el) el.style.display = 'none';
  });
  const target = n === '2friends' ? 'step2friends' : 'step' + n;
  const el = document.getElementById(target);
  if(el) el.style.display = '';
}
function startGame(){
  const ds   = document.getElementById('dataSource').value;
  const gs   = document.getElementById('gridSize').value;
  const diff = document.getElementById('difficulty').value;
  if(selectedMode === 'rated'){
    window.location = `/matchmaking?data_source=${ds}&grid_size=${gs}&difficulty=${diff}`;
  } else {
    window.location = `/play?mode=${selectedMode}&data_source=${ds}&grid_size=${gs}&difficulty=${diff}`;
  }
}
async function createFriendsRoom(){
  const ds = document.getElementById('friendsDataSource').value;
  const btn = event.target;
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const r = await fetch('/api/create_room',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data_source:ds})});
    const d = await r.json();
    if(d.code) window.location = `/room/${d.code}`;
    else { toast('Failed to create room','error'); btn.disabled=false; btn.textContent='Create Room'; }
  } catch(e){ toast('Network error','error'); btn.disabled=false; btn.textContent='Create Room'; }
}
function joinRoom(){
  const code = document.getElementById('joinCodeInput').value.trim();
  if(code.length === 6) window.location = `/room/${code}`;
  else toast('Please enter a valid 6-digit code','warn');
}
document.getElementById('joinCodeInput') && document.getElementById('joinCodeInput').addEventListener('keydown', e => {
  if(e.key === 'Enter') joinRoom();
});
</script>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  GAME BODY — Full implementation with rating display fix
# ═══════════════════════════════════════════════════════════════════════════════

GAME_BODY = """
<style>
.filled-by-name{font-size:.68rem;font-weight:700;color:var(--green);margin-top:3px;display:block;line-height:1.2;}
.wc-filled-name{font-size:.68rem;font-weight:700;color:var(--pur);margin-top:3px;display:block;line-height:1.2;}
.cell-skip-used{opacity:.5;cursor:not-allowed!important;pointer-events:none;}
</style>

<div class="container" style="padding-top:22px;padding-bottom:88px;max-width:900px;">

  <!-- Header bar -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:10px;">
    <div style="display:flex;align-items:center;gap:10px;">
      <a href="/" class="btn btn-ghost btn-sm" id="quitBtn">← Quit</a>
      <span class="badge" style="color:var(--acc);border-color:rgba(245,166,35,.3);background:var(--acc-dim);">
        {{ mode_label }}
      </span>
      <span class="badge" style="color:var(--txt3);border-color:var(--bdr);">
        {{ difficulty|capitalize }} · {{ data_source|upper }}
      </span>
    </div>
    <div style="display:flex;align-items:center;gap:14px;">
      <div style="text-align:right;">
        <div class="label" style="margin-bottom:2px;">Score</div>
        <div style="font-family:var(--font-head);font-size:1.5rem;font-weight:800;color:var(--acc);line-height:1;" id="scoreNum">0</div>
      </div>
      {% if game_mode in ['rated','friends'] and opponent %}
      <div class="opp-bar">
        <div class="label" style="margin-bottom:2px;">{{ opponent }}</div>
        <div class="opp-score-num" id="oppScore">0</div>
      </div>
      {% endif %}
      <div style="text-align:right;">
        <div class="label" style="margin-bottom:2px;">Time</div>
        <div style="font-family:var(--font-head);font-size:1.1rem;font-weight:700;color:var(--txt2);line-height:1;" id="timerDisplay">0:00</div>
      </div>
    </div>
  </div>

  <!-- Timer bar -->
  <div class="timer-wrap mb-4" style="height:4px;">
    <div class="timer-bar" id="timerBar" style="width:100%;background:var(--acc);"></div>
  </div>

  <!-- Player card -->
  <div class="player-card mb-4" id="playerCard">
    <div class="player-hint mb-2" id="playerIdx">Player 1 of {{ total_players }}</div>
    <div class="player-name" id="playerName">Loading…</div>
    <div style="display:flex;align-items:center;justify-content:center;gap:6px;margin-top:8px;flex-wrap:wrap;" id="playerMeta"></div>
  </div>

  <!-- Controls -->
  <div style="display:flex;gap:8px;justify-content:center;margin-bottom:18px;flex-wrap:wrap;">
    <button class="btn btn-secondary btn-sm" id="skipBtn" onclick="skipPlayer()">
      ⏭ Skip <span id="skipsLeft" style="opacity:.6;font-size:.75rem;">(3 left)</span>
    </button>
    <button class="btn btn-secondary btn-sm" id="wcBtn" onclick="useWildcard()">
      ✦ Wildcard <span style="opacity:.6;font-size:.75rem;">(1 use)</span>
    </button>
    <button class="btn btn-secondary btn-sm" id="solutionBtn" onclick="toggleSolutions()">
      💡 Hints
    </button>
  </div>

  <!-- Solutions panel -->
  <div id="solutionsPanel" style="display:none;margin-bottom:14px;">
    <div class="card-sm">
      <div class="label mb-2">Valid Players for Each Cell</div>
      <div id="solutionsContent" style="font-size:.75rem;color:var(--txt2);line-height:1.9;"></div>
    </div>
  </div>

  <!-- Grid -->
  <div class="bingo-grid size-{{ grid_size }}" id="bingoGrid">
    {% for cell in grid %}
    <div class="cell
      {% if cell.type == 'nation' %}nation-cell
      {% elif cell.type == 'trophy' %}trophy-cell
      {% elif cell.type == 'combo' %}combo-cell{% endif %}"
      data-idx="{{ loop.index0 }}"
      data-type="{{ cell.type }}"
      data-value="{{ cell.value }}"
      onclick="handleCellClick({{ loop.index0 }})">
      {% if cell.type == 'team' %}
        {% if cell.logo %}
          <img class="cell-logo" src="/public/{{ cell.logo }}" alt="{{ cell.value }}"
            onerror="this.style.display='none';this.nextElementSibling.style.display='block';">
          <span class="cell-label" style="display:none;">{{ cell.value }}</span>
        {% else %}
          <span class="cell-label">{{ cell.value }}</span>
        {% endif %}
        <span class="cell-label">{{ cell.value }}</span>
      {% elif cell.type == 'nation' %}
        {% if use_nation_flags and cell.value in FLAG_MAP %}
          <span style="font-size:2rem;">{{ FLAG_MAP[cell.value] }}</span>
        {% endif %}
        <span style="font-size:.82rem;font-weight:700;">{{ cell.value }}</span>
      {% elif cell.type == 'trophy' %}
        <span style="font-size:1.4rem;">🏆</span>
        <span class="cell-label">{{ cell.value }}</span>
      {% elif cell.type == 'combo' %}
        <span style="font-size:1rem;">🔗</span>
        <span style="font-size:.7rem;font-weight:700;line-height:1.4;padding:0 4px;">{{ cell.value }}</span>
      {% endif %}
    </div>
    {% endfor %}
  </div>

  <!-- Progress -->
  <div style="margin-top:20px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <span style="font-size:.75rem;color:var(--txt3);">Progress</span>
      <span style="font-size:.75rem;color:var(--txt3);" id="progressText">0 / {{ grid_size * grid_size }}</span>
    </div>
    <div class="progress-wrap" style="height:6px;">
      <div class="progress-bar" id="progressBar" style="width:0%;background:var(--green);"></div>
    </div>
  </div>
</div>

<!-- Results Modal -->
<div class="modal-overlay" id="resultsModal" style="display:none;">
  <div class="modal">
    <div style="text-align:center;margin-bottom:20px;">
      <div style="font-size:2.5rem;margin-bottom:8px;" id="resultsEmoji">🏏</div>
      <h2 style="font-family:var(--font-head);font-size:1.6rem;font-weight:800;" id="resultsTitle">Game Over!</h2>
      <p style="color:var(--txt2);font-size:.88rem;margin-top:4px;" id="resultsSubtitle"></p>
    </div>

    <!-- Score -->
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px;">
      <div class="stat-card">
        <div class="stat-value" id="finalScore">0</div>
        <div class="stat-label">Score</div>
      </div>
      <div class="stat-card">
        <div class="stat-value" id="finalAccuracy">0%</div>
        <div class="stat-label">Accuracy</div>
      </div>
      <div class="stat-card">
        <div class="stat-value" id="finalTime">0:00</div>
        <div class="stat-label">Time</div>
      </div>
    </div>

    <!-- ── RATING RESULT BOX (solo/rated modes) ── -->
    <div id="rating-result"></div>

    <!-- XP gain -->
    <div id="xpResultBox" style="display:none;margin-top:12px;">
      <div class="card-sm" style="text-align:center;background:rgba(155,114,247,.08);border-color:rgba(155,114,247,.25);">
        <div style="font-size:.72rem;font-weight:700;color:var(--pur);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">XP Earned</div>
        <div style="font-family:var(--font-head);font-size:1.6rem;font-weight:800;color:var(--pur);" id="xpGainNum">+0</div>
        <div style="margin-top:6px;">
          <div class="xp-bar-wrap" style="height:5px;">
            <div class="xp-bar" id="xpProgressBar" style="width:0%;"></div>
          </div>
          <div style="font-size:.72rem;color:var(--txt3);margin-top:4px;" id="xpLevelText"></div>
        </div>
      </div>
    </div>

    <!-- Streak -->
    <div id="streakResultBox" style="display:none;margin-top:10px;">
      <div class="card-sm" style="text-align:center;background:rgba(245,166,35,.08);border-color:rgba(245,166,35,.25);">
        <div style="font-size:.72rem;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">Daily Streak</div>
        <div style="font-family:var(--font-head);font-size:1.4rem;font-weight:800;color:var(--acc);" id="streakNum">🔥 0</div>
      </div>
    </div>

    <!-- Share row -->
    <div id="shareRow" style="margin-top:14px;display:none;">
      <div class="share-grid-wrap" id="shareEmojis" style="font-size:1.1rem;letter-spacing:1px;"></div>
      <div class="share-btn-row">
        <button class="btn btn-secondary btn-sm" onclick="copyShare()">📋 Copy Result</button>
        <button class="btn btn-secondary btn-sm" onclick="shareToTwitter()">𝕏 Share</button>
      </div>
    </div>

    <div style="display:flex;gap:8px;margin-top:20px;">
      <a href="/" class="btn btn-secondary w-full">🏠 Home</a>
      <button class="btn btn-primary w-full" onclick="playAgain()">↺ Play Again</button>
    </div>
  </div>
</div>

<!-- Level-up overlay (injected by JS) -->
<div id="levelupContainer"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script>
// ── Game Data from server ───────────────────────────────────────────────────
const PLAYERS      = {{ players_json | safe }};
const SOLUTIONS    = {{ solutions_json | safe }};
const GRID_DATA    = {{ grid_json | safe }};
const GRID_SIZE    = {{ grid_size }};
const TOTAL_CELLS  = GRID_SIZE * GRID_SIZE;
const GAME_MODE    = "{{ game_mode }}";
const DATA_SOURCE  = "{{ data_source }}";
const DIFFICULTY   = "{{ difficulty }}";
const ROOM_CODE    = {{ ('"' + room_code + '"') if room_code else 'null' }};

// ── State ───────────────────────────────────────────────────────────────────
let currentIdx   = 0;
let score        = 0;
let correctCount = 0;
let wrongCount   = 0;
let skipsLeft    = 3;
let wcUsed       = false;
let wcActive     = false;
let wcPlayer     = null;
let startTime    = Date.now();
let timerInterval= null;
let cellsFilled  = 0;
let filledBy     = {};   // cellIdx -> playerName
let gridState    = Array(TOTAL_CELLS).fill(null);
let gameEnded    = false;
let solutionsVisible = false;

// ── Timer ───────────────────────────────────────────────────────────────────
function startTimer(){
  timerInterval = setInterval(()=>{
    const secs = Math.floor((Date.now()-startTime)/1000);
    const m = Math.floor(secs/60), s = secs%60;
    document.getElementById('timerDisplay').textContent = m+':'+(s<10?'0':'')+s;
  }, 500);
}

// ── Render current player ────────────────────────────────────────────────────
function renderPlayer(){
  if(currentIdx >= PLAYERS.length){ endGame('completed'); return; }
  const p = PLAYERS[currentIdx];
  document.getElementById('playerName').textContent = p.name || p.id;
  document.getElementById('playerIdx').textContent  = `Player ${currentIdx+1} of ${PLAYERS.length}`;
  const meta = document.getElementById('playerMeta');
  meta.innerHTML = '';
  const nation = p.nation || p.country || '';
  if(nation){
    const s = document.createElement('span');
    s.className='badge'; s.style.cssText='color:var(--blue);border-color:rgba(79,142,247,.3)';
    s.textContent='🌍 '+nation; meta.appendChild(s);
  }
  if(p.iplTeams && p.iplTeams.length){
    p.iplTeams.slice(0,3).forEach(t=>{
      const s=document.createElement('span');
      s.className='badge'; s.style.cssText='color:var(--txt2);border-color:var(--bdr)';
      s.textContent=t; meta.appendChild(s);
    });
  } else if(p.team){
    const s=document.createElement('span');
    s.className='badge'; s.style.cssText='color:var(--txt2);border-color:var(--bdr)';
    s.textContent=p.team; meta.appendChild(s);
  }
  if(wcActive){
    document.getElementById('playerCard').style.border='2px solid var(--acc)';
  } else {
    document.getElementById('playerCard').style.border='1px solid var(--bdr2)';
  }
  updateProgress();
}

// ── Cell click ───────────────────────────────────────────────────────────────
async function handleCellClick(idx){
  if(gameEnded) return;
  const cell = document.querySelector(`.cell[data-idx="${idx}"]`);
  if(!cell || cell.classList.contains('filled') || cell.classList.contains('wc-filled')) return;

  if(wcActive){
    // wildcard — fill all hinted cells
    if(cell.classList.contains('wildcard-hint')){
      await applyWildcard(idx);
    }
    return;
  }

  const p = PLAYERS[currentIdx];
  if(!p) return;

  try {
    const resp = await fetch('/api/validate_move',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({player_id: p.id, cell_idx: idx})
    });
    const data = await resp.json();

    if(data.correct){
      markCellFilled(idx, p.name || p.id, false);
      const pts = calcPoints();
      score += pts;
      correctCount++;
      filledBy[idx] = p.id;
      updateScore();
      toast('✓ Correct! +'+pts, 'success');
      if(ROOM_CODE && socket) socket.emit('player_move',{room:ROOM_CODE, score, cell_idx:idx});
      advancePlayer();
    } else {
      cell.classList.add('wrong');
      wrongCount++;
      score = Math.max(0, score - 5);
      updateScore();
      setTimeout(()=>cell.classList.remove('wrong'), 500);
      toast('✗ Wrong player', 'error');
      if(data.reason !== 'already_filled') advancePlayer();
    }
  } catch(e){ toast('Network error','error'); }
}

function markCellFilled(idx, pname, isWc){
  const cell = document.querySelector(`.cell[data-idx="${idx}"]`);
  if(!cell) return;
  cell.classList.add(isWc ? 'wc-filled' : 'filled');
  cell.classList.remove('wildcard-hint');
  const nameEl = document.createElement('span');
  nameEl.className = isWc ? 'wc-filled-name' : 'filled-by-name';
  nameEl.textContent = pname;
  cell.appendChild(nameEl);
  gridState[idx] = pname;
  cellsFilled++;
  updateProgress();
  if(cellsFilled >= TOTAL_CELLS) setTimeout(()=>endGame('completed'), 600);
}

function calcPoints(){
  const elapsed = (Date.now()-startTime)/1000;
  const base = DIFFICULTY === 'hard' ? 120 : DIFFICULTY === 'easy' ? 60 : 90;
  const timeBonus = Math.max(0, Math.floor((300 - elapsed) / 10));
  return base + timeBonus;
}

function updateScore(){
  document.getElementById('scoreNum').textContent = score;
}

function updateProgress(){
  const pct = Math.round(cellsFilled / TOTAL_CELLS * 100);
  document.getElementById('progressBar').style.width = pct+'%';
  document.getElementById('progressText').textContent = cellsFilled+' / '+TOTAL_CELLS;
}

// ── Skip ─────────────────────────────────────────────────────────────────────
function skipPlayer(){
  if(skipsLeft <= 0 || gameEnded) return;
  skipsLeft--;
  document.getElementById('skipsLeft').textContent = `(${skipsLeft} left)`;
  if(skipsLeft === 0) document.getElementById('skipBtn').disabled = true;
  advancePlayer();
  toast('Skipped', 'info');
}

// ── Wildcard ─────────────────────────────────────────────────────────────────
async function useWildcard(){
  if(wcUsed || gameEnded) return;
  const p = PLAYERS[currentIdx];
  if(!p) return;
  try {
    const r = await fetch('/api/wildcard_hint',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({player_id: p.id, data_source: DATA_SOURCE})
    });
    const d = await r.json();
    if(!d.matching_cells || d.matching_cells.length === 0){
      toast('No matching cells for this player', 'warn'); return;
    }
    wcUsed   = true;
    wcActive = true;
    wcPlayer = p;
    document.getElementById('wcBtn').disabled = true;
    d.matching_cells.forEach(i=>{
      const cell = document.querySelector(`.cell[data-idx="${i}"]`);
      if(cell && !cell.classList.contains('filled') && !cell.classList.contains('wc-filled'))
        cell.classList.add('wildcard-hint');
    });
    document.getElementById('playerCard').style.border='2px solid var(--acc)';
    toast('Click a highlighted cell to place '+p.name, 'info');
  } catch(e){ toast('Wildcard failed','error'); }
}

async function applyWildcard(idx){
  if(!wcPlayer) return;
  // Remove all wildcard hints
  document.querySelectorAll('.cell.wildcard-hint').forEach(c=>{
    c.classList.remove('wildcard-hint');
  });
  markCellFilled(idx, wcPlayer.name || wcPlayer.id, true);
  filledBy[idx] = wcPlayer.id + '_wc';
  wcActive = false;
  wcPlayer = null;
  document.getElementById('playerCard').style.border='1px solid var(--bdr2)';
  advancePlayer();
  toast('✦ Wildcard placed!', 'info');
}

// ── Advance player ────────────────────────────────────────────────────────────
function advancePlayer(){
  currentIdx++;
  if(currentIdx >= PLAYERS.length){ endGame('completed'); return; }
  renderPlayer();
}

// ── Solutions panel ───────────────────────────────────────────────────────────
function toggleSolutions(){
  solutionsVisible = !solutionsVisible;
  const p = document.getElementById('solutionsPanel');
  p.style.display = solutionsVisible ? '' : 'none';
  if(solutionsVisible && !p.dataset.built){
    const c = document.getElementById('solutionsContent');
    let html = '';
    GRID_DATA.forEach((cell, i)=>{
      const sols = SOLUTIONS[String(i)] || [];
      html += `<div style="margin-bottom:8px;"><strong style="color:var(--txt);">${cell.value}:</strong> ${sols.slice(0,8).join(', ')||'—'}</div>`;
    });
    c.innerHTML = html;
    p.dataset.built = '1';
  }
}

// ── Quit handler ─────────────────────────────────────────────────────────────
document.getElementById('quitBtn').addEventListener('click', async(e)=>{
  e.preventDefault();
  if(gameEnded){ window.location='/'; return; }
  if(confirm('Quit this game? Your progress will be lost.')){
    gameEnded = true;
    clearInterval(timerInterval);
    await submitEndGame('quit');
    window.location = '/';
  }
});

// ── End game ──────────────────────────────────────────────────────────────────
async function endGame(reason){
  if(gameEnded) return;
  gameEnded = true;
  clearInterval(timerInterval);
  const elapsed  = Math.floor((Date.now()-startTime)/1000);
  const total    = correctCount + wrongCount;
  const accuracy = total > 0 ? Math.round(correctCount / total * 100) : 100;

  document.getElementById('finalScore').textContent    = score;
  document.getElementById('finalAccuracy').textContent = accuracy+'%';
  const m = Math.floor(elapsed/60), s = elapsed%60;
  document.getElementById('finalTime').textContent = m+':'+(s<10?'0':'')+s;

  if(reason === 'completed'){
    document.getElementById('resultsEmoji').textContent  = accuracy >= 80 ? '🏆' : '🏏';
    document.getElementById('resultsTitle').textContent  = accuracy >= 80 ? 'Excellent!' : 'Good Game!';
    document.getElementById('resultsSubtitle').textContent = `${correctCount} correct · ${wrongCount} wrong · ${elapsed}s`;
  } else {
    document.getElementById('resultsEmoji').textContent  = '⏸';
    document.getElementById('resultsTitle').textContent  = 'Game Over';
    document.getElementById('resultsSubtitle').textContent = 'Better luck next time!';
  }

  buildShareEmojis();
  document.getElementById('resultsModal').style.display = 'flex';

  // Submit to server
  await submitEndGame(reason, elapsed, accuracy);
}

async function submitEndGame(reason, elapsed=0, accuracy=0){
  try {
    const payload = {
      mode:        GAME_MODE,
      data_source: DATA_SOURCE,
      score:       score,
      elapsed:     elapsed,
      accuracy:    accuracy,
      difficulty:  DIFFICULTY,
      grid_size:   GRID_SIZE,
      room_code:   ROOM_CODE,
      filled_by:   filledBy,
      reason:      reason
    };
    const resp = await fetch('/api/end_game',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    handleEndGameResponse(data, reason);
  } catch(e){ console.error('end_game failed', e); }
}

// ── Handle server response & display rating ───────────────────────────────────
function handleEndGameResponse(data, reason){
  if(reason === 'quit') return;

  // ── Rating update (solo / rated / friends) ──────────────────────────────
  const ratingBox = document.getElementById('rating-result');
  if(data.new_rating !== undefined && ratingBox){
    const delta     = data.rating_change || 0;
    const newRating = Math.round(data.new_rating);
    const sign      = delta >= 0 ? '+' : '';
    const animCls   = delta >= 0 ? 'rating-anim-up' : 'rating-anim-down';
    const deltaCol  = delta >= 0 ? 'var(--green)' : 'var(--red)';
    const parNote   = data.par_score
      ? `<div style="font-size:.72rem;color:var(--txt3);margin-top:4px;">Par score: ${data.par_score}</div>`
      : '';
    const rankNote  = data.new_rank
      ? `<div style="font-size:.78rem;color:var(--txt2);margin-top:6px;">Rank #${data.new_rank}</div>`
      : '';
    const modeLabel = GAME_MODE === 'solo' ? 'Solo Rating' : 'Rating';

    ratingBox.innerHTML = `
      <div class="rating-result-box">
        <div class="label" style="margin-bottom:6px;">${modeLabel} Updated</div>
        <div class="new-rating-num ${animCls}">${newRating}</div>
        <div class="rating-delta ${delta>=0?'pos':'neg'}">${sign}${delta.toFixed(1)}</div>
        ${parNote}
        ${rankNote}
      </div>`;
  }

  // ── XP ──────────────────────────────────────────────────────────────────
  if(data.xp){
    const xd = data.xp;
    const box = document.getElementById('xpResultBox');
    box.style.display = '';
    document.getElementById('xpGainNum').textContent = '+' + xd.gained;
    const toNext  = xd.to_next  || 0;
    const rng     = xd.range    || 1;
    const pct     = rng > 0 ? Math.round((rng - toNext) / rng * 100) : 100;
    document.getElementById('xpProgressBar').style.width = pct + '%';
    document.getElementById('xpLevelText').textContent   =
      `Level ${xd.level} · ${xd.name} · ${toNext} XP to next`;
    toast(`+${xd.gained} XP earned!`, 'info');
    if(xd.leveled_up) showLevelUp(xd.level, xd.name);
  }

  // ── Streak ──────────────────────────────────────────────────────────────
  if(data.streak && data.streak.is_new){
    const sd  = data.streak;
    const box = document.getElementById('streakResultBox');
    box.style.display = '';
    document.getElementById('streakNum').textContent = '🔥 ' + sd.current;
    if(sd.broken){
      toast('Streak reset — new streak: 1 day 🔥', 'warn');
    } else {
      toast('🔥 '+sd.current+'-day streak!', 'success');
    }
  }

  // ── Multiplayer result ───────────────────────────────────────────────────
  if(data.winner !== undefined){
    document.getElementById('resultsEmoji').textContent  = data.winner ? '🏆' : '😞';
    document.getElementById('resultsTitle').textContent  = data.winner ? 'You Win!' : 'You Lose';
  }
}

// ── Level-up overlay ──────────────────────────────────────────────────────────
function showLevelUp(level, name){
  const c = document.getElementById('levelupContainer');
  c.innerHTML = `
    <div class="levelup-overlay" id="levelupOverlay">
      <div class="levelup-card" onclick="document.getElementById('levelupOverlay').remove()">
        <div style="font-size:2.5rem;margin-bottom:12px;">🎉</div>
        <div style="font-family:var(--font-head);font-size:.75rem;font-weight:700;
                    color:var(--pur);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;">
          Level Up!
        </div>
        <div style="font-family:var(--font-head);font-size:2rem;font-weight:800;
                    color:var(--txt);letter-spacing:-.5px;">Level ${level}</div>
        <div style="color:var(--pur);font-weight:600;margin-top:4px;">${name}</div>
        <div style="margin-top:18px;">
          <button class="btn btn-secondary btn-sm"
            onclick="document.getElementById('levelupOverlay').remove()">
            Continue
          </button>
        </div>
      </div>
    </div>`;
  setTimeout(()=>{
    const el = document.getElementById('levelupOverlay');
    if(el) el.remove();
  }, 5000);
}

// ── Share ─────────────────────────────────────────────────────────────────────
function buildShareEmojis(){
  const row = document.getElementById('shareRow');
  const box = document.getElementById('shareEmojis');
  if(!row || !box) return;
  let lines = [];
  for(let r=0;r<GRID_SIZE;r++){
    let line='';
    for(let c=0;c<GRID_SIZE;c++){
      const i = r*GRID_SIZE+c;
      if(filledBy[i]){
        line += (String(filledBy[i]).endsWith('_wc')) ? '🟣' : '🟩';
      } else {
        line += '⬜';
      }
    }
    lines.push(line);
  }
  box.textContent = lines.join('\n');
  row.style.display = '';
}

function copyShare(){
  const emoji = document.getElementById('shareEmojis').textContent;
  const txt   = `Cricket Bingo — Score: ${score}\n${emoji}\nPlay at cricketbingo.com`;
  navigator.clipboard.writeText(txt).then(()=>toast('Copied!','success'));
}

function shareToTwitter(){
  const emoji = document.getElementById('shareEmojis').textContent;
  const txt   = encodeURIComponent(`Cricket Bingo — Score: ${score}\n${emoji}\n🏏 Play at cricketbingo.com`);
  window.open('https://twitter.com/intent/tweet?text='+txt,'_blank');
}

function playAgain(){
  window.location = `/play?mode=${GAME_MODE}&data_source=${DATA_SOURCE}&grid_size=${GRID_SIZE}&difficulty=${DIFFICULTY}`;
}

// ── Socket.IO (multiplayer) ───────────────────────────────────────────────────
let socket = null;
if(ROOM_CODE){
  socket = io();
  socket.emit('join_room', {room: ROOM_CODE});
  socket.on('opponent_move', d => {
    const el = document.getElementById('oppScore');
    if(el){
      el.textContent = d.score || 0;
      el.classList.add('pulse');
      setTimeout(()=>el.classList.remove('pulse'), 600);
    }
  });
  socket.on('game_result', d => {
    if(d.rating_change !== undefined){
      const ratingBox = document.getElementById('rating-result');
      if(ratingBox && ratingBox.innerHTML === ''){
        // opponent finished first — show our delta
        const delta = -(d.rating_change);
        const sign  = delta >= 0 ? '+' : '';
        ratingBox.innerHTML = `<div class="rating-result-box">
          <div class="label" style="margin-bottom:4px;">Match Result</div>
          <div style="font-size:1rem;font-weight:700;color:${delta>=0?'var(--green)':'var(--red)'}">
            ${sign}${delta.toFixed(1)} rating
          </div></div>`;
      }
    }
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────
renderPlayer();
startTimer();
document.getElementById('skipsLeft').textContent = `(${skipsLeft} left)`;
</script>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  MATCHMAKING BODY
# ═══════════════════════════════════════════════════════════════════════════════

MATCHMAKING_BODY = """
<div class="container page">
  <div class="mm-card">
    <div class="mm-dots"><span></span><span></span><span></span></div>
    <h2 class="title mb-2">Finding Opponent…</h2>
    <p class="subhead mb-6">Matching you with a player of similar rating</p>
    <div style="font-size:.8rem;color:var(--txt3);margin-bottom:28px;" id="mmStatus">
      Looking for players in your rating range
    </div>
    <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">
      <div class="badge" style="color:var(--acc);border-color:rgba(245,166,35,.3);">{{ data_source|upper }}</div>
      <div class="badge" style="color:var(--txt2);border-color:var(--bdr);">{{ grid_size }}×{{ grid_size }}</div>
      <div class="badge" style="color:var(--txt2);border-color:var(--bdr);">{{ difficulty|capitalize }}</div>
    </div>
    <div style="margin-top:24px;">
      <span id="waitTime" style="font-size:1.8rem;font-family:var(--font-head);font-weight:800;color:var(--txt2);">0</span>
      <span style="font-size:.8rem;color:var(--txt3);margin-left:4px;">seconds</span>
    </div>
    <button class="btn btn-outline btn-sm mt-6" onclick="cancelMatchmaking()">Cancel</button>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script>
const socket = io();
let waitSecs = 0;
const wt = setInterval(()=>{
  waitSecs++;
  document.getElementById('waitTime').textContent = waitSecs;
  if(waitSecs > 10) document.getElementById('mmStatus').textContent = 'Still searching — expanding rating range…';
  if(waitSecs > 30) document.getElementById('mmStatus').textContent = 'Taking longer than usual…';
}, 1000);

socket.emit('join_matchmaking',{
  data_source: '{{ data_source }}',
  grid_size:   {{ grid_size }},
  difficulty:  '{{ difficulty }}'
});
socket.on('match_found', d => {
  clearInterval(wt);
  document.querySelector('.mm-dots').innerHTML = '<span style="background:var(--green)"></span>';
  document.querySelector('.title').textContent = 'Match Found!';
  setTimeout(()=>{ window.location = `/play?room_code=${d.room_code}&mode=rated`; }, 800);
});
socket.on('matchmaking_status', d => {
  document.getElementById('mmStatus').textContent = d.message || '';
});
function cancelMatchmaking(){
  clearInterval(wt);
  socket.emit('leave_matchmaking');
  window.location = '/';
}
</script>
"""

# ═══════════════════════════════════════════════════════════════════════════════
#  ROOM BODY (Friends lobby)
# ═══════════════════════════════════════════════════════════════════════════════

ROOM_BODY = """
<div class="container page">
  <div style="max-width:480px;margin:0 auto;">
    <div class="card" style="text-align:center;">
      <div style="font-size:.72rem;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.1em;margin-bottom:12px;">Room Code</div>
      <div class="room-code-display" onclick="copyCode()" title="Click to copy">{{ room_code }}</div>
      <p style="font-size:.8rem;color:var(--txt3);margin-top:10px;">Share this code with your friend</p>

      <div id="playersList" style="margin-top:22px;display:flex;flex-direction:column;gap:8px;"></div>
      <div id="waitMsg" style="margin-top:16px;color:var(--txt2);font-size:.85rem;display:flex;align-items:center;justify-content:center;gap:8px;">
        <div class="spinner" style="width:18px;height:18px;border-width:2px;"></div>
        Waiting for opponent to join…
      </div>

      {% if is_host %}
      <div id="hostSettings" style="margin-top:22px;display:none;">
        <hr>
        <div class="label mt-3">Settings</div>
        <div style="display:flex;flex-direction:column;gap:10px;text-align:left;margin-top:10px;">
          <div class="input-group">
            <label class="label">Grid Size</label>
            <select class="input" id="roomGrid"><option value="3">3×3</option><option value="4">4×4</option></select>
          </div>
          <div class="input-group">
            <label class="label">Difficulty</label>
            <select class="input" id="roomDiff">
              <option value="easy">Easy</option>
              <option value="normal" selected>Normal</option>
              <option value="hard">Hard</option>
            </select>
          </div>
        </div>
        <button class="btn btn-primary w-full mt-4" id="startBtn" onclick="startRoomGame()">
          ▶ Start Game
        </button>
      </div>
      {% endif %}
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script>
const socket = io();
const IS_HOST = {{ 'true' if is_host else 'false' }};
const ROOM    = '{{ room_code }}';
const DS      = '{{ data_source }}';

socket.emit('join_room', {room: ROOM});

socket.on('room_update', d => {
  const list = document.getElementById('playersList');
  list.innerHTML = d.players.map((n,i)=>`
    <div style="display:flex;align-items:center;gap:10px;background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-lg);padding:12px 16px;">
      <span style="width:26px;height:26px;border-radius:50%;background:var(--acc-dim);color:var(--acc);font-weight:700;font-size:.8rem;display:flex;align-items:center;justify-content:center;">${i+1}</span>
      <span style="font-weight:600;color:var(--txt);">${n}</span>
      ${i===0?'<span class="badge" style="margin-left:auto;color:var(--acc);border-color:rgba(245,166,35,.3);">Host</span>':''}
    </div>`).join('');

  if(d.players.length >= 2){
    document.getElementById('waitMsg').style.display = 'none';
    if(IS_HOST){
      document.getElementById('hostSettings').style.display = '';
    }
  }
});

socket.on('game_start', d => {
  window.location = `/play?room_code=${d.room_code}&mode=friends`;
});

function startRoomGame(){
  const gs   = document.getElementById('roomGrid').value;
  const diff = document.getElementById('roomDiff').value;
  socket.emit('start_room_game',{room:ROOM, data_source:DS, grid_size:parseInt(gs), difficulty:diff});
}

function copyCode(){
  navigator.clipboard.writeText(ROOM).then(()=>toast('Room code copied!','success'));
}
</script>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  LEADERBOARD BODY
# ═══════════════════════════════════════════════════════════════════════════════

LEADERBOARD_BODY = """
<div class="container page">
  <div class="section-header mb-6"><h2>Leaderboard</h2></div>
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:24px;">
    <div>
      <h1 class="title">Season Rankings</h1>
      <p class="subhead">{{ season.name }} · Ends {{ season.end_date }}</p>
    </div>
    <div class="tab-bar" style="margin-bottom:0;">
      <button class="tab-btn {{ 'active' if mode=='mp' else '' }}" onclick="window.location='/leaderboard?mode=mp'">⚡ Multiplayer</button>
      <button class="tab-btn {{ 'active' if mode=='solo' else '' }}" onclick="window.location='/leaderboard?mode=solo'">🎯 Solo</button>
    </div>
  </div>

  {% if not rows %}
  <div class="card" style="text-align:center;padding:60px 20px;">
    <div style="font-size:2rem;margin-bottom:12px;">🏏</div>
    <p class="subhead">No games played yet this season. Be the first!</p>
    <a href="/" class="btn btn-primary mt-4">Play Now</a>
  </div>
  {% else %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th style="width:48px;">#</th>
          <th>Player</th>
          <th>Rating</th>
          <th class="hide-sm">Tier</th>
          <th class="hide-sm">W</th>
          <th class="hide-sm">L</th>
          <th class="hide-sm">Win %</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rows %}
        <tr style="{{ 'background:var(--acc-dim);' if current_user.is_authenticated and r.user_id == current_user.id else '' }}">
          <td>
            {% if loop.index == 1 %}<span style="font-size:1.1rem;">🥇</span>
            {% elif loop.index == 2 %}<span style="font-size:1.1rem;">🥈</span>
            {% elif loop.index == 3 %}<span style="font-size:1.1rem;">🥉</span>
            {% else %}<span style="color:var(--txt3);">{{ loop.index }}</span>{% endif %}
          </td>
          <td>
            <a href="/profile/{{ r.user_id }}" style="color:var(--txt);font-weight:600;text-decoration:none;">
              {{ r.name }}
              {% if current_user.is_authenticated and r.user_id == current_user.id %}
              <span class="badge" style="color:var(--acc);border-color:rgba(245,166,35,.3);margin-left:6px;font-size:.62rem;">You</span>
              {% endif %}
            </a>
          </td>
          <td>
            <span style="font-family:var(--font-head);font-weight:700;color:var(--txt);">{{ r.rating | round | int }}</span>
          </td>
          <td class="hide-sm">
            <span class="badge" style="color:{{ r.tier_color }};border-color:{{ r.tier_color }}40;">
              {{ r.tier_icon }} {{ r.tier }}
            </span>
          </td>
          <td class="hide-sm" style="color:var(--green);">{{ r.wins }}</td>
          <td class="hide-sm" style="color:var(--red);">{{ r.losses }}</td>
          <td class="hide-sm">
            <div style="display:flex;align-items:center;gap:6px;">
              <div class="progress-wrap" style="width:48px;height:4px;">
                <div class="progress-bar" style="width:{{ r.win_rate }}%;background:var(--green);"></div>
              </div>
              <span style="font-size:.78rem;color:var(--txt2);">{{ r.win_rate }}%</span>
            </div>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>
"""

# ═══════════════════════════════════════════════════════════════════════════════
#  PROFILE BODY
# ═══════════════════════════════════════════════════════════════════════════════

PROFILE_BODY = """
<div class="container page">
  <div style="max-width:740px;margin:0 auto;">
    <!-- Header -->
    <div class="card mb-4">
      <div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;">
        <img src="{{ profile_user.avatar or '' }}"
          style="width:72px;height:72px;border-radius:50%;object-fit:cover;border:3px solid var(--acc);"
          onerror="this.style.display='none'" alt="{{ profile_user.name }}">
        <div style="flex:1;min-width:0;">
          <h1 style="font-family:var(--font-head);font-size:1.5rem;font-weight:800;margin-bottom:4px;">
            {{ profile_user.name }}
          </h1>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="badge" style="color:{{ tier_color }};border-color:{{ tier_color }}40;">
              {{ tier_icon }} {{ tier }}
            </span>
            <span class="level-badge">Lv {{ xp_data.level }} · {{ xp_data.name }}</span>
            {% if streak_data.current > 0 %}
            <span class="streak-badge">🔥 {{ streak_data.current }}-day streak</span>
            {% endif %}
          </div>
          <!-- XP bar -->
          <div style="margin-top:10px;max-width:280px;">
            <div style="display:flex;justify-content:space-between;font-size:.68rem;color:var(--txt3);margin-bottom:3px;">
              <span>{{ xp_data.total }} XP</span>
              <span>Level {{ xp_data.level }}</span>
            </div>
            <div class="xp-bar-wrap" style="height:5px;">
              <div class="xp-bar" style="width:{{ xp_data.pct }}%;"></div>
            </div>
          </div>
        </div>
        {% if current_user.is_authenticated and current_user.id == profile_user.id %}
        <a href="/logout" class="btn btn-outline btn-sm">Sign Out</a>
        {% endif %}
      </div>
    </div>

    <!-- Rating cards -->
    <div class="grid-2 gap-3 mb-4">
      <div class="stat-card card-accent">
        <div class="label">MP Rating</div>
        <div class="stat-value">{{ rating | round | int }}</div>
      </div>
      <div class="stat-card">
        <div class="label">Solo Rating</div>
        <div class="stat-value">{{ solo_rating | round | int }}</div>
      </div>
    </div>

    <!-- Stats -->
    <div class="grid-4 gap-3 mb-6">
      <div class="stat-card">
        <div class="stat-value">{{ stats.total_games }}</div>
        <div class="stat-label">Games</div>
      </div>
      <div class="stat-card">
        <div class="stat-value text-green">{{ stats.wins }}</div>
        <div class="stat-label">Wins</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.win_rate }}%</div>
        <div class="stat-label">Win Rate</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.best_streak }}</div>
        <div class="stat-label">Best Streak</div>
      </div>
    </div>

    <!-- Match history -->
    <div class="section-header"><h2>Recent Matches</h2></div>
    {% if not matches %}
    <div class="card" style="text-align:center;padding:40px;">
      <p class="subhead">No matches yet. <a href="/" style="color:var(--acc);">Play now!</a></p>
    </div>
    {% else %}
    <div style="display:flex;flex-direction:column;gap:8px;">
      {% for m in matches %}
      <div class="card-sm" style="display:flex;align-items:center;gap:14px;border-left:3px solid
        {% if m.won == true %}var(--green){% elif m.won == false %}var(--red){% else %}var(--bdr){% endif %};">
        <div style="width:32px;text-align:center;flex-shrink:0;">
          {% if m.won == true %}
            <span style="font-size:1.1rem;">✅</span>
          {% elif m.won == false %}
            <span style="font-size:1.1rem;">❌</span>
          {% else %}
            <span style="font-size:1.1rem;">🎯</span>
          {% endif %}
        </div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:.85rem;font-weight:600;color:var(--txt);">
            {% if m.opponent %}vs {{ m.opponent }}{% else %}Solo · {{ m.mode|capitalize }}{% endif %}
          </div>
          <div style="font-size:.72rem;color:var(--txt3);">
            {{ m.difficulty|capitalize }} · {{ m.played_at[:16] if m.played_at else '' }}
          </div>
        </div>
        <div style="text-align:right;flex-shrink:0;">
          <div style="font-family:var(--font-head);font-weight:800;color:var(--acc);">{{ m.score | round | int }}</div>
          {% if m.rating_change %}
          <div style="font-size:.75rem;font-weight:700;color:{{ 'var(--green)' if m.rating_change >= 0 else 'var(--red)' }};">
            {{ '+' if m.rating_change >= 0 else '' }}{{ m.rating_change | round(1) }}
          </div>
          {% endif %}
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
</div>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  DAILY BODY
# ═══════════════════════════════════════════════════════════════════════════════

DAILY_BODY = """
<div class="container page">
  <div style="max-width:740px;margin:0 auto;">
    <div class="section-header"><h2>Daily Challenge</h2></div>
    <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:14px;margin-bottom:28px;">
      <div>
        <h1 class="title">Today's Puzzle</h1>
        <p class="subhead">{{ today }} · Same grid for everyone</p>
      </div>
      {% if already_played %}
        <span class="badge" style="color:var(--green);border-color:rgba(45,211,111,.3);padding:8px 16px;">✅ Completed Today</span>
      {% else %}
        <a href="/play?mode=daily" class="btn btn-primary btn-lg">Play Daily →</a>
      {% endif %}
    </div>

    <!-- Streaks info -->
    {% if current_user.is_authenticated %}
    <div class="card mb-6" style="background:var(--acc-dim);border-color:rgba(245,166,35,.25);">
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
        <div style="flex:1;">
          <div class="label mb-1">Your Streak</div>
          <div style="font-family:var(--font-head);font-size:1.6rem;font-weight:800;color:var(--acc);" id="streakDisplay">
            🔥 Loading…
          </div>
        </div>
        <div style="text-align:right;">
          <div class="label mb-1">Best</div>
          <div style="font-family:var(--font-head);font-size:1.2rem;font-weight:700;color:var(--txt2);" id="bestStreakDisplay">—</div>
        </div>
        <div style="text-align:right;">
          <div class="label mb-1">Freezes</div>
          <div style="font-family:var(--font-head);font-size:1.2rem;font-weight:700;color:var(--blue);" id="freezeDisplay">—</div>
        </div>
      </div>
    </div>
    {% endif %}

    <!-- Leaderboard -->
    <div class="section-header"><h2>Today's Top Scores</h2></div>
    {% if not rows %}
    <div class="card" style="text-align:center;padding:40px;">
      <div style="font-size:2rem;margin-bottom:10px;">📅</div>
      <p class="subhead">No one has played today yet. Be the first!</p>
      <a href="/play?mode=daily" class="btn btn-primary mt-4">Play Now</a>
    </div>
    {% else %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:48px;">#</th>
            <th>Player</th>
            <th>Score</th>
            <th class="hide-sm">Accuracy</th>
            <th class="hide-sm">Time</th>
          </tr>
        </thead>
        <tbody>
          {% for r in rows %}
          <tr style="{{ 'background:var(--acc-dim);' if current_user.is_authenticated and r.user_id == current_user.id else '' }}">
            <td>
              {% if loop.index == 1 %}🥇{% elif loop.index == 2 %}🥈{% elif loop.index == 3 %}🥉
              {% else %}<span style="color:var(--txt3);">{{ loop.index }}</span>{% endif %}
            </td>
            <td>
              <a href="/profile/{{ r.user_id }}" style="color:var(--txt);font-weight:600;text-decoration:none;">
                {{ r.name }}
              </a>
            </td>
            <td><span style="font-family:var(--font-head);font-weight:700;color:var(--acc);">{{ r.score | round | int }}</span></td>
            <td class="hide-sm">{{ r.accuracy | round | int }}%</td>
            <td class="hide-sm">
              {% set secs = r.completion_time | int %}
              {{ (secs // 60)|string + ':' + '%02d' % (secs % 60) }}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}
  </div>
</div>
<script>
{% if current_user.is_authenticated %}
fetch('/api/streak').then(r=>r.json()).then(d=>{
  if(d.streak){
    document.getElementById('streakDisplay').textContent = '🔥 ' + d.streak.current + ' days';
    document.getElementById('bestStreakDisplay').textContent = d.streak.best + ' days';
    document.getElementById('freezeDisplay').textContent = d.streak.freeze + ' ❄️';
  }
}).catch(()=>{});
{% endif %}
</script>
"""

# ═══════════════════════════════════════════════════════════════════════════════
#  ABOUT BODY
# ═══════════════════════════════════════════════════════════════════════════════

ABOUT_BODY = """
<div class="container page">
  <div class="container-sm" style="padding:0;">
    <div class="section-header"><h2>About</h2></div>
    <h1 class="title mb-3">About Cricket Bingo</h1>
    <p style="color:var(--txt2);line-height:1.9;margin-bottom:20px;">
      Cricket Bingo is a fan-made browser game that challenges you to match IPL cricket players
      to their teams, nations, and trophies. It was built for cricket fans who love trivia and
      want a fun, fast-paced way to test their knowledge.
    </p>
    <p style="color:var(--txt2);line-height:1.9;margin-bottom:28px;">
      The game features a fame-based difficulty system — on Easy mode you'll see the most famous
      players, while Hard mode includes more obscure names that only true fans will know.
    </p>

    <div class="section-header"><h2>Features</h2></div>
    <div class="grid-2 gap-4 mb-6">
      <div class="card">
        <div style="font-size:1.4rem;margin-bottom:8px;">⚡</div>
        <h3 class="heading mb-2">Rated Multiplayer</h3>
        <p style="font-size:.83rem;color:var(--txt2);">Compete 1v1 against players of your skill level. Win to gain ELO rating. Lose and you drop. Climb the seasonal leaderboard.</p>
      </div>
      <div class="card">
        <div style="font-size:1.4rem;margin-bottom:8px;">📅</div>
        <h3 class="heading mb-2">Daily Challenge</h3>
        <p style="font-size:.83rem;color:var(--txt2);">One new puzzle per day, shared by all players. Build your streak. Earn streak freezes every 7 days.</p>
      </div>
      <div class="card">
        <div style="font-size:1.4rem;margin-bottom:8px;">👥</div>
        <h3 class="heading mb-2">Friends Mode</h3>
        <p style="font-size:.83rem;color:var(--txt2);">Create a private room and share the 6-digit code with a friend. Play head-to-head in real time.</p>
      </div>
      <div class="card">
        <div style="font-size:1.4rem;margin-bottom:8px;">🏏</div>
        <h3 class="heading mb-2">500+ Players</h3>
        <p style="font-size:.83rem;color:var(--txt2);">From MS Dhoni and Virat Kohli to rare uncapped players. Covering all IPL seasons.</p>
      </div>
    </div>

    <div class="section-header"><h2>How to Play</h2></div>
    <div style="display:flex;flex-direction:column;gap:12px;margin-bottom:28px;">
      {% for step, desc in [
        ('A player name appears', 'You are shown a cricket player\'s name one at a time.'),
        ('Click the right cell', 'Each cell shows a team, nation, or trophy. Click the cell that matches the player.'),
        ('Score points', 'Correct picks earn points. Wrong picks deduct a few. Faster answers earn bonus points.'),
        ('Use power-ups', 'Skip a player (3 per game) or use a Wildcard to highlight all valid cells for 1 player.'),
        ('Fill the grid', 'Try to fill all cells before running out of players!')
      ] %}
      <div style="display:flex;gap:14px;align-items:flex-start;">
        <div style="width:28px;height:28px;border-radius:50%;background:var(--acc-dim);border:1px solid rgba(245,166,35,.3);color:var(--acc);font-weight:800;font-size:.8rem;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px;">{{ loop.index }}</div>
        <div>
          <div style="font-weight:700;color:var(--txt);font-size:.9rem;">{{ step }}</div>
          <div style="color:var(--txt2);font-size:.83rem;margin-top:2px;">{{ desc }}</div>
        </div>
      </div>
      {% endfor %}
    </div>

    <div class="card" style="background:var(--acc-dim);border-color:rgba(245,166,35,.25);text-align:center;padding:32px;">
      <p style="font-size:.83rem;color:var(--txt3);margin-bottom:14px;">Fan-made · Not affiliated with BCCI or IPL</p>
      <a href="/" class="btn btn-primary btn-lg">Start Playing →</a>
    </div>
  </div>
</div>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTACT BODY
# ═══════════════════════════════════════════════════════════════════════════════

CONTACT_BODY = """
<div class="container page">
  <div class="container-xs" style="padding:0;">
    <div class="section-header"><h2>Contact</h2></div>
    <h1 class="title mb-2">Get in Touch</h1>
    <p class="subhead mb-6">Questions, bug reports, or suggestions? We'd love to hear from you.</p>

    <div class="card">
      <div style="display:flex;flex-direction:column;gap:14px;">
        <div class="input-group">
          <label class="label">Your Name</label>
          <input class="input" id="cName" placeholder="e.g. Rohit Sharma" maxlength="100">
        </div>
        <div class="input-group">
          <label class="label">Email Address</label>
          <input class="input" id="cEmail" type="email" placeholder="you@example.com" maxlength="200">
        </div>
        <div class="input-group">
          <label class="label">Subject</label>
          <select class="input" id="cSubject">
            <option value="">Select a subject…</option>
            <option value="Bug Report">🐛 Bug Report</option>
            <option value="Feature Request">💡 Feature Request</option>
            <option value="Player Data Issue">🏏 Player Data Issue</option>
            <option value="Account Issue">👤 Account Issue</option>
            <option value="General Feedback">💬 General Feedback</option>
            <option value="Other">📌 Other</option>
          </select>
        </div>
        <div class="input-group">
          <label class="label">Message</label>
          <textarea class="input" id="cMessage" rows="5" placeholder="Describe your issue or suggestion…" maxlength="2000" style="resize:vertical;"></textarea>
        </div>
        <button class="btn btn-primary w-full btn-lg" onclick="submitContact()" id="contactBtn">
          Send Message
        </button>
        <div id="contactFeedback" style="display:none;text-align:center;padding:12px;border-radius:var(--r-md);"></div>
      </div>
    </div>

    <div style="margin-top:24px;text-align:center;">
      <p style="font-size:.83rem;color:var(--txt3);">
        Or email us directly at
        <a href="mailto:tehm8111@gmail.com" style="color:var(--acc);">tehm8111@gmail.com</a>
      </p>
    </div>
  </div>
</div>
<script>
async function submitContact(){
  const btn = document.getElementById('contactBtn');
  const fb  = document.getElementById('contactFeedback');
  const name    = document.getElementById('cName').value.trim();
  const email   = document.getElementById('cEmail').value.trim();
  const subject = document.getElementById('cSubject').value;
  const message = document.getElementById('cMessage').value.trim();

  if(name.length < 2){ toast('Name must be at least 2 characters','warn'); return; }
  if(!email.includes('@')){ toast('Please enter a valid email','warn'); return; }
  if(!subject){ toast('Please select a subject','warn'); return; }
  if(message.length < 10){ toast('Message must be at least 10 characters','warn'); return; }

  btn.disabled = true; btn.textContent = 'Sending…';

  try {
    const r = await fetch('/api/contact',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, email, subject, message})
    });
    const d = await r.json();
    if(d.success){
      fb.style.display = '';
      fb.style.cssText += 'background:rgba(45,211,111,.1);border:1px solid rgba(45,211,111,.3);color:var(--green);';
      fb.textContent   = '✅ Message sent! We\'ll get back to you soon.';
      document.getElementById('cName').value = '';
      document.getElementById('cEmail').value = '';
      document.getElementById('cSubject').value = '';
      document.getElementById('cMessage').value = '';
      btn.textContent = 'Sent!';
    } else {
      fb.style.display = '';
      fb.style.cssText += 'background:rgba(240,82,79,.1);border:1px solid rgba(240,82,79,.3);color:var(--red);';
      fb.textContent = '❌ ' + (d.error || 'Failed to send. Please email us directly.');
      btn.disabled = false; btn.textContent = 'Send Message';
    }
  } catch(e){
    toast('Network error. Please try again.','error');
    btn.disabled = false; btn.textContent = 'Send Message';
  }
}
</script>
"""

# ═══════════════════════════════════════════════════════════════════════════════
#  PRIVACY BODY
# ═══════════════════════════════════════════════════════════════════════════════

PRIVACY_BODY = """
<div class="container page">
  <div class="container-sm" style="padding:0;">
    <div class="section-header"><h2>Legal</h2></div>
    <h1 class="title mb-2">Privacy Policy</h1>
    <p class="subhead mb-6">Last updated: January 2025</p>
    <div style="display:flex;flex-direction:column;gap:20px;">
      {% for title, body in sections %}
      <div class="card">
        <h2 style="font-family:var(--font-head);font-size:1rem;font-weight:700;margin-bottom:10px;color:var(--txt);">{{ title }}</h2>
        <div style="font-size:.875rem;color:var(--txt2);line-height:1.85;">{{ body | safe }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
</div>
"""

# ═══════════════════════════════════════════════════════════════════════════════
#  TERMS BODY
# ═══════════════════════════════════════════════════════════════════════════════

TERMS_BODY = """
<div class="container page">
  <div class="container-sm" style="padding:0;">
    <div class="section-header"><h2>Legal</h2></div>
    <h1 class="title mb-2">Terms &amp; Conditions</h1>
    <p class="subhead mb-6">Last updated: January 2025</p>
    <div style="display:flex;flex-direction:column;gap:20px;">
      {% for title, body in sections %}
      <div class="card">
        <h2 style="font-family:var(--font-head);font-size:1rem;font-weight:700;margin-bottom:10px;color:var(--txt);">{{ title }}</h2>
        <div style="font-size:.875rem;color:var(--txt2);line-height:1.85;">{{ body | safe }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
</div>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  page() helper — FIX #1: single render, kwargs forwarded
# ═══════════════════════════════════════════════════════════════════════════════

def _get_current_user_safe():
    try:
        from flask_login import current_user as _cu
        return _cu
    except Exception:
        return None

def page(body, title="Cricket Bingo", extra_head="", **kwargs):
    nav = NAV_HTML()
    streak_current  = 0
    user_level      = 1
    user_level_name = "Rookie"
    try:
        from flask_login import current_user as _cu
        if _cu.is_authenticated:
            sd = get_streak_data(_cu.id)
            streak_current = sd.get("current", 0)
            xd = get_xp_data(_cu.id)
            user_level      = xd.get("level", 1)
            user_level_name = xd.get("name", "Rookie")
    except Exception:
        pass
    pwa_meta = """
<link rel="manifest" href="/public/manifest.json">
<meta name="theme-color" content="#0A0C12">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
"""
    context = dict(kwargs)
    context.setdefault("streak_current", streak_current)
    context.setdefault("user_level",      user_level)
    context.setdefault("user_level_name", user_level_name)
    context.setdefault("current_user",    _get_current_user_safe())

    return render_template_string(
        f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title} — Cricket Bingo</title>
{SEO_META}
{GOOGLE_ANALYTICS}
{pwa_meta}
{CSS}
{extra_head}
<script>
(function(){{
  const t=localStorage.getItem('cb-theme')||'dark';
  document.documentElement.setAttribute('data-theme',t);
}})();
</script>
</head>
<body>
{nav}
{body}
{FOOTER_HTML}
{GLOBAL_SCRIPTS}
</body>
</html>""",
        **context
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return page(HOME_BODY, "Home")

@app.route("/about")
def about():
    return page(ABOUT_BODY, "About Us")

@app.route("/contact")
def contact():
    return page(CONTACT_BODY, "Contact Us")

@app.route("/privacy")
def privacy():
    sections = [
        ("1. Information We Collect",
         "When you sign in with Google, we collect your <strong style='color:var(--txt)'>name</strong>, "
         "<strong style='color:var(--txt)'>email address</strong>, and <strong style='color:var(--txt)'>profile picture</strong>. "
         "We also collect gameplay data such as scores, match results, accuracy statistics, and time taken."),
        ("2. How We Use Your Information",
         "<ul style='padding-left:20px;line-height:2.2;'>"
         "<li>To create and maintain your Cricket Bingo account</li>"
         "<li>To display your name on leaderboards and profiles</li>"
         "<li>To calculate and track your ELO rating</li>"
         "<li>To enable multiplayer matchmaking</li>"
         "<li>To improve the game and fix bugs</li></ul>"),
        ("3. Google Analytics",
         "We use <strong style='color:var(--txt)'>Google Analytics</strong> (GA4) to understand how visitors use the site."),
        ("4. Cookies",
         "We use session cookies to keep you logged in. Google Analytics uses cookies for usage tracking."),
        ("5. Data Sharing",
         "We do <strong style='color:var(--txt)'>not sell</strong> your personal data."),
        ("6. Data Deletion",
         "To request deletion of your account and data, email "
         "<a href='mailto:tehm8111@gmail.com' style='color:var(--acc);'>tehm8111@gmail.com</a>."),
        ("7. Children's Privacy",
         "Cricket Bingo is not directed at children under 13."),
        ("8. Contact",
         "For privacy questions: <a href='mailto:tehm8111@gmail.com' style='color:var(--acc);'>tehm8111@gmail.com</a>"),
    ]
    return page(PRIVACY_BODY, "Privacy Policy", sections=sections)

@app.route("/terms")
def terms():
    sections = [
        ("1. Acceptance", "By using Cricket Bingo, you agree to these Terms."),
        ("2. Acceptable Use",
         "<ul style='padding-left:20px;line-height:2.2;'>"
         "<li>Do not use bots or automated scripts</li>"
         "<li>Do not attempt to manipulate scores or ratings</li>"
         "<li>Do not harass other players</li>"
         "<li>Do not attempt unauthorised access to the system</li></ul>"),
        ("3. Intellectual Property",
         "Cricket Bingo is an independent fan-made game, not affiliated with BCCI or any IPL franchise."),
        ("4. Account Responsibility",
         "You are responsible for the security of your Google account."),
        ("5. Disclaimer",
         'Cricket Bingo is provided "as is" without warranties.'),
        ("6. Contact",
         "Questions? Email <a href='mailto:tehm8111@gmail.com' style='color:var(--acc);'>tehm8111@gmail.com</a>"),
    ]
    return page(TERMS_BODY, "Terms & Conditions", sections=sections)

@app.route("/oauth_callback")
def oauth_callback():
    if not google.authorized: return redirect(url_for("google.login"))
    try:
        resp = google.get("/oauth2/v2/userinfo")
        if not resp.ok: return redirect("/")
        info = resp.json(); gid = info["id"]; email = info.get("email", "")
        name = info.get("name", email.split("@")[0]); avatar = info.get("picture", "")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE google_id=?", (gid,)).fetchone():
            db.execute("UPDATE users SET email=?,name=?,avatar=? WHERE google_id=?",
                       (email, name, avatar, gid))
        else:
            db.execute("INSERT INTO users(google_id,email,name,avatar) VALUES(?,?,?,?)",
                       (gid, email, name, avatar))
        db.commit()
        u = User(db.execute("SELECT * FROM users WHERE google_id=?", (gid,)).fetchone())
        login_user(u)
        s = get_current_season()
        if s: ensure_season_rating(u.id, s["id"])
    except Exception as e:
        log.error(f"OAuth error: {e}")
    return redirect("/")

@app.route("/logout")
@login_required
def logout():
    logout_user(); session.clear(); return redirect("/")

@app.route("/play")
def play():
    game_mode = request.args.get("mode", "solo")
    if game_mode in ("rated", "friends") and not current_user.is_authenticated:
        return redirect(url_for("google.login"))

    ds         = request.args.get("data_source", "overall")
    grid_size  = int(request.args.get("grid_size", 3))
    difficulty = request.args.get("difficulty", "normal")
    room_code  = request.args.get("room_code", None)

    if game_mode == "daily":
        state = get_or_create_daily()
        if state:
            ds         = state.get("data_source", "overall")
            grid_size  = state.get("grid_size", 3)
            difficulty = state.get("difficulty", "normal")
    elif room_code:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if not row: return redirect("/")
        state     = json.loads(row["game_state"])
        game_mode = row["mode"]
        ds        = state.get("data_source", "overall")
        grid_size = state.get("grid_size", 3)
        difficulty= state.get("difficulty", "normal")
    else:
        state = create_game_state(ds, grid_size, difficulty)

    if not state or not state.get("players"):
        log.error(f"Game state creation failed for ds={ds}")
        return (
            f"<div style='font-family:sans-serif;padding:60px;text-align:center;'>"
            f"<h2 style='color:#EF4444;'>⚠ No player data found for '{ds}'</h2>"
            f"<p>Ensure <code>overall.json</code> / <code>ipl26.json</code> exist in project root.</p>"
            f"<a href='/' style='color:#22C55E;'>← Back to Home</a></div>",
            500,
        )

    n = grid_size * grid_size
    if len(state.get("grid_state", [])) != n:
        state["grid_state"] = [None] * n

    session["game_state"] = {
        "grid":        state["grid"],
        "grid_state":  [None] * n,
        "room_code":   room_code,
        "mode":        game_mode,
        "data_source": ds,
    }

    mode_labels = {
        "solo":    "Solo Practice",
        "rated":   "⚡ Rated",
        "friends": "👥 Friends",
        "daily":   "📅 Daily"
    }

    grid = state["grid"]
    for cell in grid:
        cell["logo"] = TEAM_LOGOS.get(cell["value"], "") if cell["type"] == "team" else ""

    nation_cells     = [c for c in grid if c["type"] == "nation"]
    use_nation_flags = all(c["value"] in FLAG_MAP for c in nation_cells)

    players_json   = json.dumps(state["players"],           default=str, ensure_ascii=False).replace("</", r"<\/")
    solutions_json = json.dumps(state.get("solutions", {}), ensure_ascii=False).replace("</", r"<\/")
    grid_for_js    = [{"type": c["type"], "value": c["value"]} for c in grid]
    grid_json      = json.dumps(grid_for_js, ensure_ascii=False).replace("</", r"<\/")

    opponent = None
    if room_code and current_user.is_authenticated:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if row:
            oid = row["player2_id"] if row["player1_id"] == current_user.id else row["player1_id"]
            if oid:
                ou = query_db("SELECT name FROM users WHERE id=?", (oid,), one=True)
                if ou: opponent = ou["name"]

    return page(
        GAME_BODY, "Play",
        grid             = grid,
        grid_json        = grid_json,
        players_json     = players_json,
        solutions_json   = solutions_json,
        grid_size        = grid_size,
        total_players    = len(state["players"]),
        game_mode        = game_mode,
        mode_label       = mode_labels.get(game_mode, game_mode),
        data_source      = ds,
        difficulty       = difficulty,
        room_code        = room_code,
        opponent         = opponent,
        use_nation_flags = use_nation_flags,
        FLAG_MAP         = FLAG_MAP,
    )

@app.route("/matchmaking")
@login_required
def matchmaking():
    ds         = request.args.get("data_source", "overall")
    grid_size  = int(request.args.get("grid_size", 3))
    difficulty = request.args.get("difficulty", "normal")
    return page(MATCHMAKING_BODY, "Finding Match",
                data_source=ds, grid_size=grid_size, difficulty=difficulty)

@app.route("/room/<room_code>")
@login_required
def room(room_code):
    row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
    if not row: return redirect("/")
    is_host = row["player1_id"] == current_user.id
    state   = json.loads(row["game_state"])
    ds      = state.get("data_source", "overall")
    if row["status"] == "active":
        return redirect(f"/play?room_code={room_code}&mode={row['mode']}")
    if not is_host and not row["player2_id"]:
        query_db("UPDATE active_games SET player2_id=? WHERE room_code=? AND player2_id IS NULL",
                 (current_user.id, room_code), commit=True)
    return page(ROOM_BODY, f"Room {room_code}",
                room_code=room_code, is_host=is_host, data_source=ds)

@app.route("/leaderboard")
def leaderboard():
    season = get_current_season()
    mode   = request.args.get("mode", "mp")
    if mode not in ("mp", "solo"):
        mode = "mp"
    rating_col = "rating" if mode == "mp" else "solo_rating"

    if not season:
        return page(LEADERBOARD_BODY, "Leaderboard",
                    season={"name": "No Season", "end_date": "—"}, rows=[], mode=mode)

    raw = query_db(
        f"""SELECT sr.user_id,sr.{rating_col} as rating,sr.wins,sr.losses,sr.total_games,u.name
            FROM season_ratings sr JOIN users u ON u.id=sr.user_id
            WHERE sr.season_id=? ORDER BY sr.{rating_col} DESC LIMIT 100""",
        (season["id"],))
    rows = []
    for r in raw:
        t, tc, ti = rating_tier(r["rating"])
        wr = round(r["wins"] / r["total_games"] * 100) if r["total_games"] > 0 else 0
        rows.append({
            "user_id":    r["user_id"],
            "name":       r["name"],
            "rating":     r["rating"],
            "wins":       r["wins"],
            "losses":     r["losses"],
            "tier":       t,
            "tier_color": tc,
            "tier_icon":  ti,
            "win_rate":   wr
        })
    return page(LEADERBOARD_BODY, "Leaderboard", season=season, rows=rows, mode=mode)

@app.route("/profile/<int:user_id>")
def profile(user_id):
    ur = query_db("SELECT * FROM users WHERE id=?", (user_id,), one=True)
    if not ur: return "User not found", 404
    season = get_current_season()
    rating = 1200.0; solo_rating = 1200.0
    tier, tier_color, tier_icon = "Beginner", "#9CA3AF", "🟤"; sr = None
    if season:
        sr = query_db("SELECT * FROM season_ratings WHERE user_id=? AND season_id=?",
                      (user_id, season["id"]), one=True)
        if sr:
            rating      = sr["rating"]
            solo_rating = sr["solo_rating"] if "solo_rating" in sr.keys() else 1200.0
            tier, tier_color, tier_icon = rating_tier(rating)
    stats = {
        "total_games":  sr["total_games"] if sr else 0,
        "solo_games":   sr["solo_games"]  if sr and "solo_games" in sr.keys() else 0,
        "wins":         sr["wins"]        if sr else 0,
        "losses":       sr["losses"]      if sr else 0,
        "win_rate":     round(sr["wins"] / sr["total_games"] * 100) if sr and sr["total_games"] > 0 else 0,
        "best_streak":  sr["best_streak"] if sr else 0,
        "avg_accuracy": round(sr["accuracy_sum"] / sr["total_games"]) if sr and sr["total_games"] > 0 else 0,
        "avg_time":     round(sr["time_sum"]      / sr["total_games"]) if sr and sr["total_games"] > 0 else 0,
    }
    raw = query_db("""SELECT m.*,u1.name as p1name,u2.name as p2name FROM matches m
        LEFT JOIN users u1 ON u1.id=m.player1_id
        LEFT JOIN users u2 ON u2.id=m.player2_id
        WHERE m.player1_id=? OR m.player2_id=?
        ORDER BY m.played_at DESC LIMIT 20""", (user_id, user_id))
    matches = []
    for m in raw:
        is_solo = m["mode"] == "solo" or m["player2_id"] is None
        ip1     = m["player1_id"] == user_id
        score   = m["player1_score"] if ip1 else m["player2_score"]
        if is_solo:
            opp = None
            won = m["winner_id"] is not None
            rc  = m["rating_change"]
        else:
            opp = m["p2name"] if ip1 else m["p1name"]
            won = m["winner_id"] == user_id if m["winner_id"] is not None else None
            rc  = m["rating_change"] if won is True else (-m["rating_change"] if won is False else 0)
        matches.append({
            "won":           won,
            "score":         score,
            "opponent":      opp,
            "rating_change": rc,
            "mode":          m["mode"],
            "difficulty":    m["difficulty"] or "normal",
            "played_at":     m["played_at"]
        })
    xp_d = get_xp_data(user_id)
    _to_next, _range = xp_next_level(xp_d["total"])
    xp_d["pct"] = max(0, int((_range - _to_next) / _range * 100)) if _range > 0 else 100
    return page(
        PROFILE_BODY, ur["name"],
        profile_user = ur,
        tier         = tier,
        tier_color   = tier_color,
        tier_icon    = tier_icon,
        rating       = rating,
        solo_rating  = solo_rating,
        stats        = stats,
        matches      = matches,
        xp_data      = xp_d,
        streak_data  = get_streak_data(user_id)
    )

@app.route("/daily")
def daily():
    today = date.today().isoformat()
    raw = query_db("""SELECT dr.user_id,dr.score,dr.completion_time,dr.accuracy,u.name
        FROM daily_results dr JOIN users u ON u.id=dr.user_id
        WHERE dr.challenge_date=? ORDER BY dr.score DESC,dr.completion_time ASC LIMIT 50""", (today,))
    played = False
    if current_user.is_authenticated:
        played = query_db("SELECT id FROM daily_results WHERE user_id=? AND challenge_date=?",
                          (current_user.id, today), one=True) is not None
    return page(DAILY_BODY, "Daily Challenge",
                today=today, rows=[dict(r) for r in raw], already_played=played)

# ═══════════════════════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/contact", methods=["POST"])
def api_contact():
    data    = request.get_json(force=True)
    name    = str(data.get("name",    "")).strip()[:100]
    email   = str(data.get("email",   "")).strip()[:200]
    subject = str(data.get("subject", "")).strip()[:200]
    message = str(data.get("message", "")).strip()[:2000]
    if len(name) < 2:
        return jsonify({"success": False, "error": "Name must be at least 2 characters"})
    if "@" not in email or "." not in email:
        return jsonify({"success": False, "error": "Invalid email address"})
    if not subject:
        return jsonify({"success": False, "error": "Please select a subject"})
    if len(message) < 10:
        return jsonify({"success": False, "error": "Message must be at least 10 characters"})

    # FIX #8: increment BEFORE returning
    contact_count = int(session.get("cb_contact_count", 0))
    if contact_count >= 3:
        return jsonify({"success": False, "error": "Too many submissions. Please email us directly."})
    session["cb_contact_count"] = contact_count + 1

    html_body = (
        f"<html><body style='font-family:sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;'>"
        f"<h2 style='color:#22C55E;'>New Cricket Bingo Contact Submission</h2>"
        f"<p><strong>From:</strong> {name} &lt;{email}&gt;</p>"
        f"<p><strong>Subject:</strong> {subject}</p>"
        f"<h3>Message:</h3>"
        f"<div style='background:#f9f9f9;padding:20px;border-radius:10px;white-space:pre-wrap;'>{message}</div>"
        f"</body></html>"
    )
    success, err = send_email(CONTACT_EMAIL, f"[Cricket Bingo] {subject} — from {name}", html_body)
    if success:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Email service unavailable. Please email us directly."})

@app.route("/api/create_room", methods=["POST"])
@login_required
def api_create_room():
    data = request.get_json(force=True)
    ds   = data.get("data_source", "overall")
    code = gen_room_code()
    init = {"data_source": ds, "grid_size": 3, "difficulty": "normal",
            "grid": [], "players": []}
    query_db("INSERT INTO active_games(room_code,player1_id,game_state,mode) VALUES(?,?,?,?)",
             (code, current_user.id, json.dumps(init), "friends"), commit=True)
    return jsonify({"code": code})

@app.route("/api/streak")
@login_required
def api_streak():
    sd = get_streak_data(current_user.id)
    xd = get_xp_data(current_user.id)
    to_next, range_val = xp_next_level(xd["total"])
    return jsonify({"streak": sd, "xp": xd, "to_next": to_next, "range": range_val})

@app.route("/api/validate_move", methods=["POST"])
def api_validate_move():
    data = request.get_json(force=True)
    pid  = data.get("player_id")
    cidx = data.get("cell_idx")

    gi = session.get("game_state")
    if not gi:
        log.warning("validate_move: no session found")
        return jsonify({"correct": False, "error": "no_session"})

    grid   = gi.get("grid", [])
    gstate = gi.get("grid_state") or [None] * len(grid)
    ds     = gi.get("data_source", "overall")

    if cidx is None or not isinstance(cidx, int) or cidx >= len(grid):
        return jsonify({"correct": False})
    if gstate[cidx] is not None:
        return jsonify({"correct": False, "reason": "already_filled"})

    pool   = get_pool(ds)
    player = next((p for p in pool if str(p.get("id")) == str(pid)), None)
    if not player and isinstance(pid, str) and pid.startswith("player_"):
        try:
            idx    = int(pid.split("_", 1)[1])
            player = pool[idx] if 0 <= idx < len(pool) else None
        except (ValueError, IndexError):
            pass

    if not player:
        log.warning(f"validate_move: player not found id={pid} ds={ds}")
        return jsonify({"correct": False, "reason": "player_not_found"})

    correct = player_matches_cell(player, grid[cidx], ds)
    if correct:
        if len(gstate) != len(grid):
            gstate = [None] * len(grid)
        gstate[cidx] = str(pid)
        gi["grid_state"] = gstate
        session["game_state"] = gi
        session.modified = True

    return jsonify({"correct": correct})

@app.route("/api/wildcard_hint", methods=["POST"])
def api_wildcard_hint():
    data = request.get_json(force=True)
    pid  = data.get("player_id")
    ds   = data.get("data_source", "overall")

    gi = session.get("game_state")
    if not gi:
        return jsonify({"matching_cells": []})

    grid   = gi.get("grid", [])
    gstate = gi.get("grid_state") or [None] * len(grid)
    ds     = gi.get("data_source", ds)

    pool   = get_pool(ds)
    player = next((p for p in pool if str(p.get("id")) == str(pid)), None)
    if not player and isinstance(pid, str) and pid.startswith("player_"):
        try:
            idx    = int(pid.split("_", 1)[1])
            player = pool[idx] if 0 <= idx < len(pool) else None
        except (ValueError, IndexError):
            pass

    if not player:
        return jsonify({"matching_cells": []})

    cells = [i for i, c in enumerate(grid)
             if gstate[i] is None and player_matches_cell(player, c, ds)]

    player_name = player.get("name", str(pid))
    if len(gstate) != len(grid):
        gstate = [None] * len(grid)
    for i in cells:
        gstate[i] = player_name + "_wc"
    gi["grid_state"] = gstate
    session["game_state"] = gi
    session.modified = True

    return jsonify({"matching_cells": cells})

@app.route("/api/end_game", methods=["POST"])
def api_end_game():
    data       = request.get_json(force=True)
    gmode      = data.get("mode", "solo")
    ds         = data.get("data_source", "overall")
    score      = float(data.get("score", 0))
    elapsed    = float(data.get("elapsed", 0))
    accuracy   = float(data.get("accuracy", 0))
    difficulty = data.get("difficulty", "normal")
    grid_size  = int(data.get("grid_size", 3))
    room_code  = data.get("room_code")
    filled_by  = data.get("filled_by", {})
    result     = {"rating_change": 0}
    season     = get_current_season()

    is_auth   = current_user.is_authenticated
    quit_game = data.get("reason") == "quit"

    # ── Daily rarity tracking ─────────────────────────────────────────────────
    today = date.today().isoformat()
    if gmode == "daily" and filled_by:
        try:
            track_cell_picks_for_daily(today, filled_by)
        except Exception as e:
            log.error(f"Rarity track failed: {e}")

    if gmode == "daily":
        try:
            n_cells = grid_size * grid_size
            rarity  = get_rarity_for_cells(today, n_cells)
            result["rarity"] = rarity
        except Exception as e:
            log.error(f"Rarity fetch failed: {e}")

        if is_auth:
            try:
                query_db(
                    "INSERT OR IGNORE INTO daily_results"
                    "(user_id,challenge_date,score,completion_time,accuracy) VALUES(?,?,?,?,?)",
                    (current_user.id, today, score, elapsed, accuracy), commit=True)
            except Exception as e:
                log.error(f"Daily result insert failed: {e}")

    elif gmode == "solo" and season and is_auth:
        ensure_season_rating(current_user.id, season["id"])
        old_solo = get_user_rating(current_user.id, season["id"], "solo_rating")
        k        = DIFFICULTY_K.get(difficulty, 24)
        par      = calc_par(difficulty, grid_size, old_solo)

        if quit_game:
            act = 0.0
        else:
            lo, hi  = par * 0.25, par * 1.5
            raw_act = (score - lo) / max(hi - lo, 1)
            act     = max(0.0, min(1.0, raw_act))

        new_solo   = elo_update(old_solo, 0.5, act, k=k)
        delta_solo = round(new_solo - old_solo, 1)

        try:
            query_db("""UPDATE season_ratings
                SET solo_rating  = ?,
                    solo_games   = solo_games + 1,
                    total_games  = total_games + 1,
                    accuracy_sum = accuracy_sum + ?,
                    time_sum     = time_sum + ?
                WHERE user_id = ? AND season_id = ?""",
                (new_solo, accuracy, elapsed, current_user.id, season["id"]), commit=True)
            query_db("""INSERT INTO matches(
                player1_id, player2_id, winner_id,
                player1_score, player1_time, player1_accuracy,
                rating_change, mode, data_source, grid_size, difficulty, season_id)
                VALUES(?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (current_user.id,
                 current_user.id if not quit_game else None,
                 score, elapsed, accuracy,
                 delta_solo, "solo", ds, grid_size, difficulty, season["id"]), commit=True)
        except Exception as e:
            log.error(f"Solo DB write failed uid={current_user.id}: {e}", exc_info=True)

        result.update({
            "rating_change": delta_solo,
            "new_rating":    new_solo,
            "par_score":     round(par),
            "difficulty":    difficulty,
            "quit":          quit_game
        })
        new_rank = get_user_rank(current_user.id, season["id"], "solo_rating")
        if new_rank: result["new_rank"] = new_rank
        log.info(f"SOLO {'QUIT' if quit_game else 'END'} uid={current_user.id} "
                 f"diff={difficulty} k={k} score={score:.0f} par={par:.0f} "
                 f"act={act:.2f} Δ={delta_solo:+.1f} ({old_solo:.0f}→{new_solo:.0f})")

    elif gmode in ("rated", "friends") and room_code and season and is_auth:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if row and row["status"] != "finished":
            gs_data  = json.loads(row["game_state"])
            mp_diff  = gs_data.get("difficulty", difficulty)
            mp_gs    = gs_data.get("grid_size", grid_size)
            k_mp     = DIFFICULTY_K.get(mp_diff, 24)
            results  = gs_data.get("results", {})
            results[str(current_user.id)] = {
                "score": score, "elapsed": elapsed, "accuracy": accuracy,
                "quit": quit_game}
            gs_data["results"] = results

            if len(results) >= 2:
                p1, p2 = row["player1_id"], row["player2_id"]
                r1 = results.get(str(p1), {"score": 0, "elapsed": 9999})
                r2 = results.get(str(p2), {"score": 0, "elapsed": 9999})

                q1 = results.get(str(p1), {}).get("quit", False)
                q2 = results.get(str(p2), {}).get("quit", False)
                if q1 and not q2:
                    winner = p2
                elif q2 and not q1:
                    winner = p1
                elif r1["score"] > r2["score"]:
                    winner = p1
                elif r2["score"] > r1["score"]:
                    winner = p2
                else:
                    winner = p1 if r1.get("elapsed", 9999) <= r2.get("elapsed", 9999) else p2

                rat1  = get_user_rating(p1, season["id"])
                rat2  = get_user_rating(p2, season["id"])
                exp1  = elo_expected(rat1, rat2)
                act1  = 1.0 if winner == p1 else 0.0
                new1  = elo_update(rat1, exp1,       act1,   k=k_mp)
                new2  = elo_update(rat2, 1.0 - exp1, 1-act1, k=k_mp)
                delta = round(new1 - rat1, 1)

                ensure_season_rating(p1, season["id"])
                ensure_season_rating(p2, season["id"])
                for uid, nr, w, rd in [
                    (p1, new1, 1 if winner == p1 else 0, r1),
                    (p2, new2, 1 if winner == p2 else 0, r2),
                ]:
                    query_db("""UPDATE season_ratings
                        SET rating=?, wins=wins+?, losses=losses+?,
                            total_games=total_games+1,
                            accuracy_sum=accuracy_sum+?, time_sum=time_sum+?,
                            win_streak=CASE WHEN ?=1 THEN win_streak+1 ELSE 0 END,
                            best_streak=MAX(best_streak,
                                CASE WHEN ?=1 THEN win_streak+1 ELSE best_streak END)
                        WHERE user_id=? AND season_id=?""",
                        (nr, w, 1-w,
                         rd.get("accuracy", 0), rd.get("elapsed", 0),
                         w, w, uid, season["id"]), commit=True)

                query_db("""INSERT INTO matches(
                    player1_id, player2_id, winner_id,
                    player1_score, player2_score, player1_time, player2_time,
                    player1_accuracy, player2_accuracy,
                    rating_change, mode, data_source, grid_size, difficulty, season_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p1, p2, winner,
                     r1["score"], r2["score"],
                     r1.get("elapsed", 0), r2.get("elapsed", 0),
                     r1.get("accuracy", 0), r2.get("accuracy", 0),
                     abs(delta), gmode, ds, mp_gs, mp_diff, season["id"]), commit=True)

                query_db("UPDATE active_games SET status='finished',game_state=? WHERE room_code=?",
                         (json.dumps(gs_data), room_code), commit=True)

                my_delta = delta if current_user.id == p1 else -delta
                my_new_r = new1  if current_user.id == p1 else new2
                result.update({
                    "rating_change": my_delta,
                    "new_rating":    my_new_r,
                    "winner":        winner == current_user.id,
                    "difficulty":    mp_diff
                })
                new_rank = get_user_rank(current_user.id, season["id"])
                if new_rank: result["new_rank"] = new_rank

                socketio.emit("game_result",
                    {"rating_change": -my_delta, "winner": winner != current_user.id},
                    room=room_code)
                log.info(f"MP result p1={p1} p2={p2} winner={winner} diff={mp_diff} k={k_mp} "
                         f"scores={r1['score']:.0f}/{r2['score']:.0f} Δ={delta:+.1f}")
            else:
                query_db("UPDATE active_games SET game_state=? WHERE room_code=?",
                         (json.dumps(gs_data), room_code), commit=True)

    session.pop("game_state", None)

    # FIX #9: daily mode gets streak + XP
    if is_auth and not quit_game and gmode == "daily":
        try:
            streak_data      = update_streak(current_user.id)
            result["streak"] = streak_data
        except Exception as e:
            log.error(f"Streak update failed: {e}")

    # XP for all non-quit authenticated sessions with a positive score
    if is_auth and not quit_game and score > 0:
        try:
            streak_cur = result.get("streak", {}).get("current", 0) if result.get("streak") else 0
            xp_gain    = calc_xp_gain(score, accuracy, streak_cur, difficulty)
            xp_data    = update_xp(current_user.id, xp_gain)
            to_next, range_val   = xp_next_level(xp_data["total"])
            xp_data["to_next"]   = to_next
            xp_data["range"]     = range_val
            result["xp"]         = xp_data
        except Exception as e:
            log.error(f"XP update failed: {e}")

    return jsonify(result)

# ═══════════════════════════════════════════════════════════════════════════════
#  SOCKET.IO HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

@socketio.on("join_room")
def on_join(data):
    rm = data.get("room")
    if not rm: return
    join_room(rm)
    row = query_db("SELECT * FROM active_games WHERE room_code=?", (rm,), one=True)
    if row:
        players = []
        for uid in [row["player1_id"], row["player2_id"]]:
            if uid:
                u = query_db("SELECT name FROM users WHERE id=?", (uid,), one=True)
                if u: players.append(u["name"])
        emit("room_update", {"players": players}, to=rm)

@socketio.on("player_move")
def on_move(data):
    rm = data.get("room")
    if rm: emit("opponent_move", data, to=rm, include_self=False)

@socketio.on("join_matchmaking")
def on_queue(data):
    if not current_user.is_authenticated: return
    ds   = data.get("data_source", "overall")
    gs   = data.get("grid_size", 3)
    diff = data.get("difficulty", "normal")
    s    = get_current_season()
    rat  = get_user_rating(current_user.id, s["id"]) if s else 1200.0
    query_db(
        "INSERT OR REPLACE INTO matchmaking_queue(user_id,rating,data_source,grid_size,difficulty) VALUES(?,?,?,?,?)",
        (current_user.id, rat, ds, gs, diff), commit=True)
    cands = query_db(
        """SELECT * FROM matchmaking_queue WHERE user_id!=? AND data_source=?
           AND grid_size=? AND difficulty=? AND ABS(rating-?)<=300
           ORDER BY ABS(rating-?) ASC LIMIT 1""",
        (current_user.id, ds, gs, diff, rat, rat))
    if cands:
        opp = cands[0]
        query_db("DELETE FROM matchmaking_queue WHERE user_id IN (?,?)",
                 (current_user.id, opp["user_id"]), commit=True)
        code  = gen_room_code()
        state = create_game_state(ds, gs, diff)
        query_db(
            "INSERT INTO active_games(room_code,player1_id,player2_id,game_state,mode,status) VALUES(?,?,?,?,?,?)",
            (code, opp["user_id"], current_user.id,
             json.dumps(state, default=str), "rated", "active"), commit=True)
        emit("match_found", {"room_code": code})
        emit("match_found", {"room_code": code}, to=f"queue_{opp['user_id']}")
    else:
        join_room(f"queue_{current_user.id}")
        emit("matchmaking_status", {"message": "Searching for opponent with similar rating…"})

@socketio.on("leave_matchmaking")
def on_leave_q():
    if current_user.is_authenticated:
        query_db("DELETE FROM matchmaking_queue WHERE user_id=?",
                 (current_user.id,), commit=True)

@socketio.on("start_room_game")
def on_start(data):
    rm   = data.get("room")
    ds   = data.get("data_source", "overall")
    gs   = data.get("grid_size", 3)
    diff = data.get("difficulty", "normal")
    row  = query_db("SELECT * FROM active_games WHERE room_code=?", (rm,), one=True)
    if not row or row["player1_id"] != current_user.id: return
    state = create_game_state(ds, gs, diff)
    query_db("UPDATE active_games SET game_state=?,status='active' WHERE room_code=?",
             (json.dumps(state, default=str), rm), commit=True)
    emit("game_start", {"room_code": rm}, to=rm)

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print("""
╔══════════════════════════════════════════════════════════╗
║   🏏  Cricket Bingo v7 — Full Production Build           ║
╠══════════════════════════════════════════════════════════╣
║  • All HTML body templates included                      ║
║  • Rating display on game-end (solo + rated + friends)   ║
║  • XP level-up overlay                                   ║
║  • Streak toasts                                         ║
║  • Share grid emoji                                      ║
║  • page(**kwargs) single render                          ║
║  • Local RNG (no global mutation)                        ║
║  • SQL column whitelist                                  ║
║  • Contact rate-limit fix                                ║
║  • Daily XP + streak fix                                 ║
╚══════════════════════════════════════════════════════════╝
""")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
