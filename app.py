# MUST BE ABSOLUTELY FIRST — patch before any import touches ssl/socket
from gevent import monkey; monkey.patch_all()

"""
Cricket Bingo — v6 (Player Pool & Fame-Based Selection)
Changes from v5.1:
  - 25 players are selected FIRST (before grid categories are derived)
    → This guarantees every cell always has a valid solution
  - Fame-based difficulty distribution:
      Easy   → 75% high-famous + 25% medium-famous
      Normal → 50% high-famous + 50% medium-famous
      Hard   → 30% high + 60% medium + 10% low-famous
  - New "Player Type" selector on the Settings step:
      All Players / Indian Players Only / International Players Only
  - player_type param flows through: UI → /play route → create_game_state
"""

import os, json, random, string, hashlib, time, smtplib, logging
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

DATABASE = "cricket_bingo.db"
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
        return all(p in teams or p == nation or p in trophies for p in parts)
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  FAME-BASED PLAYER SELECTION
#  Selects exactly `n` players from pool according to difficulty distribution.
#  player_type filters the base pool before fame sampling.
# ══════════════════════════════════════════════════════════════════════════════

# Unified difficulty config: controls BOTH fame distribution AND grid category types.
# easy   → Teams only   + 75% high / 25% medium
# normal → Teams+Nations + 50% high / 50% medium
# hard   → Teams+Nations+Combos + 30% high / 60% medium / 10% low
DIFFICULTY_CONFIG = {
    "easy":   {"high": 0.75, "medium": 0.25, "low": 0.00, "grid": "easy"},
    "normal": {"high": 0.50, "medium": 0.50, "low": 0.00, "grid": "normal"},
    "hard":   {"high": 0.30, "medium": 0.60, "low": 0.10, "grid": "hard"},
}
# Keep alias so existing references to FAME_DISTRIBUTION still work
FAME_DISTRIBUTION = {k: v for k, v in DIFFICULTY_CONFIG.items()}

def select_players_by_fame(pool, difficulty, n=25, player_type=None):
    """
    Returns exactly `n` players from `pool`.
    Fame distribution is driven by `difficulty` (easy/normal/hard) via DIFFICULTY_CONFIG.
    `player_type` is unused but kept for backwards compatibility.
    """
    filtered = list(pool)

    # Bucket by fame
    high_f   = [p for p in filtered if p.get("famous") == "high"]
    medium_f = [p for p in filtered if p.get("famous") == "medium"]
    low_f    = [p for p in filtered if p.get("famous") == "low"]

    dist = DIFFICULTY_CONFIG.get(difficulty, DIFFICULTY_CONFIG["normal"])
    n_high   = round(n * dist["high"])
    n_medium = round(n * dist["medium"])
    n_low    = n - n_high - n_medium   # absorbs rounding remainder

    # Shuffle each bucket
    random.shuffle(high_f)
    random.shuffle(medium_f)
    random.shuffle(low_f)

    selected = []
    selected += high_f[:n_high]
    selected += medium_f[:n_medium]
    selected += low_f[:max(0, n_low)]

    # 3. Fill any shortfall (bucket too small) from remaining pool
    if len(selected) < n:
        used_ids = {id(p) for p in selected}
        rest = [p for p in filtered if id(p) not in used_ids]
        random.shuffle(rest)
        selected += rest[: n - len(selected)]

    # 4. Final shuffle so order isn't predictable
    random.shuffle(selected)
    log.info(
        f"select_players_by_fame: player_type={player_type} "
        f"→ {len(selected)} players "
        f"(high={sum(1 for p in selected if p.get('famous')=='high')}, "
        f"medium={sum(1 for p in selected if p.get('famous')=='medium')}, "
        f"low={sum(1 for p in selected if p.get('famous')=='low')})"
    )
    return selected[:n]


# ══════════════════════════════════════════════════════════════════════════════
#  GRID BUILDER — derives categories from the pre-selected player pool
#  Every cell is guaranteed to have ≥1 matching player in that 25-player pool.
# ══════════════════════════════════════════════════════════════════════════════

def build_grid_validated(size, ds, difficulty, pool):
    """Build a grid where EVERY cell has at least one valid player from pool."""
    n = size * size

    # Build category pools exclusively from the given player pool
    valid_teams    = list({t for p in pool for t in p.get("iplTeams", [])} if ds == "overall"
                          else {p["team"] for p in pool if p.get("team")})
    valid_nations  = list({p["nation"] for p in pool if p.get("nation")})
    valid_trophies = list({t for p in pool for t in p.get("trophies", [])} if ds == "overall" else [])

    def has_player(cell):
        return any(player_matches_cell(p, cell, ds) for p in pool)

    def get_valid_category(cell_type, seen, max_tries=50):
        if cell_type == "team" and valid_teams:
            candidates = [t for t in valid_teams if t not in seen]
            random.shuffle(candidates)
            for v in candidates:
                cell = {"type": "team", "value": v}
                if has_player(cell): return cell
        elif cell_type == "nation" and valid_nations:
            candidates = [n for n in valid_nations if n not in seen]
            random.shuffle(candidates)
            for v in candidates:
                cell = {"type": "nation", "value": v}
                if has_player(cell): return cell
        elif cell_type == "trophy" and valid_trophies:
            candidates = [t for t in valid_trophies if t not in seen]
            random.shuffle(candidates)
            for v in candidates:
                cell = {"type": "trophy", "value": v}
                if has_player(cell): return cell
        elif cell_type == "combo":
            for _ in range(max_tries):
                p = random.choice(pool)
                teams_p    = p.get("iplTeams", []) if ds == "overall" else [p.get("team", "")]
                nation_p   = p.get("nation", "")
                trophies_p = p.get("trophies", []) if ds == "overall" else []
                combos = []
                if teams_p and nation_p:   combos.append(f"{random.choice(teams_p)} + {nation_p}")
                if teams_p and trophies_p: combos.append(f"{random.choice(teams_p)} + {random.choice(trophies_p)}")
                if nation_p and trophies_p:combos.append(f"{nation_p} + {random.choice(trophies_p)}")
                for combo_v in combos:
                    if combo_v not in seen:
                        cell = {"type": "combo", "value": combo_v}
                        if has_player(cell): return cell
        return None

    if difficulty == "easy":   type_pool = ["team"] * n
    elif difficulty == "hard": type_pool = ["team"] * (n//3) + ["nation"] * (n//3) + ["combo"] * (n - 2*(n//3))
    else:                      type_pool = ["team"] * (n//2) + ["nation"] * (n - n//2)
    random.shuffle(type_pool)

    cells, seen = [], set()
    for ct in type_pool:
        cell = get_valid_category(ct, seen)
        if cell is None:
            cell = get_valid_category("team", seen)
        if cell is None and valid_teams:
            for t in valid_teams:
                c = {"type": "team", "value": t}
                if has_player(c): cell = c; break
        if cell:
            seen.add(cell["value"]); cells.append(cell)

    # Pad if short
    while len(cells) < n and valid_teams:
        for t in valid_teams:
            c = {"type": "team", "value": t}
            if has_player(c) and t not in seen:
                seen.add(t); cells.append(c)
                if len(cells) >= n: break

    return cells[:n]


def create_game_state(ds, grid_size, difficulty, seed=None, player_type=None):
    """
    Build a complete game state.
    Step 1 — select 25 players via fame distribution.
    Step 2 — derive grid categories exclusively from those 25 players
              (guarantees every cell has ≥1 solution).
    """
    if seed is not None: random.seed(seed)

    full_pool = list(get_pool(ds))
    if not full_pool:
        log.error(f"No players found for data source: {ds}"); return None

    # ── Step 1: fame-based player selection ──────────────────────────────────
    selected_players = select_players_by_fame(full_pool, difficulty, n=25)
    if not selected_players:
        log.error("Player selection returned empty list"); return None

    # ── Step 2: build grid from the selected 25 players ──────────────────────
    grid = build_grid_validated(grid_size, ds, difficulty, selected_players)
    if not grid:
        log.error("Grid build failed"); return None

    # ── Step 3: pre-compute solutions (only from the 25 selected) ────────────
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
        "players":            selected_players,   # exactly 25
        "current_player_idx": 0,
        "grid_state":         [None] * (grid_size * grid_size),
        "skips_used":         0,
        "wildcard_used":      False,
        "correct":            0,
        "wrong":              0,
        "started_at":         time.time(),
        "seed":               see