# MUST BE ABSOLUTELY FIRST — patch before any import touches ssl/socket
from gevent import monkey; monkey.patch_all()

"""
Cricket Bingo — v4 (UI/UX Overhaul + Dark/Light Mode + Bug Fixes)
Changes:
  - Dark/Light mode toggle (default: dark) with localStorage persistence
  - Complete UI redesign: Sora font, refined spacing, glassmorphism cards
  - Fixed: session game_state grid_state sync bug
  - Fixed: player_not_found fallback for player index lookup
  - Fixed: contact rate limit session key collision
  - Fixed: wildcard_hint gstate None guard
  - Fixed: daily game uses correct ds/grid_size/difficulty from state
  - New: animated background orbs, gradient borders, smooth transitions
  - New: micro-interactions on cells, buttons, nav
  - New: better mobile nav with slide animation
  - New: accessible focus states and ARIA labels
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
        log.warning("SMTP not configured")
        return False, "Email service not configured"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = to_addr
        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo(); server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_addr, msg.as_string())
        return True, ""
    except Exception as e:
        log.error(f"Email send failed: {e}")
        return False, str(e)

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
    db = get_db()
    cur = db.execute(sql, args)
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
            rating REAL DEFAULT 1200, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            total_games INTEGER DEFAULT 0, accuracy_sum REAL DEFAULT 0, time_sum REAL DEFAULT 0,
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
        """)
        db.commit()
        _ensure_season()

def _ensure_season():
    db = get_db()
    today = date.today().isoformat()
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
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        for i, p in enumerate(data):
            if "id" not in p or not p["id"]:
                p["id"] = f"player_{i}"
            if "name" not in p:
                p["name"] = f"Player {i+1}"
        return data
    except Exception as e:
        log.error(f"Failed to load {fp}: {e}")
        return []

OVERALL_DATA = load_json("overall.json")
IPL26_DATA   = load_json("ipl26.json")
log.info(f"Loaded {len(OVERALL_DATA)} overall players, {len(IPL26_DATA)} ipl26 players")

def get_pool(ds):
    return OVERALL_DATA if ds == "overall" else IPL26_DATA

def gen_cell(pool, ds, difficulty, cell_type):
    if not pool: return {"type": "team", "value": "Unknown"}
    if cell_type == "combo" and difficulty == "hard":
        p = random.choice(pool)
        if ds == "overall" and p.get("iplTeams"):
            t = random.choice(p["iplTeams"])
            combos = [f"{t} + {p['nation']}"]
            if p.get("trophies"):
                tr = random.choice(p["trophies"])
                combos += [f"{t} + {tr}", f"{p['nation']} + {tr}"]
            return {"type": "combo", "value": random.choice(combos)}
        return {"type": "combo", "value": f"{p.get('team','?')} + {p.get('nation','?')}"}
    if cell_type == "team":
        teams = list({t for p in pool for t in p.get("iplTeams", [])} if ds == "overall" else {p["team"] for p in pool if p.get("team")})
        if teams: return {"type": "team", "value": random.choice(teams)}
    if cell_type == "nation":
        nations = list({p["nation"] for p in pool if p.get("nation")})
        if nations: return {"type": "nation", "value": random.choice(nations)}
    if cell_type == "trophy" and ds == "overall":
        trophies = list({t for p in pool for t in p.get("trophies", [])})
        if trophies: return {"type": "trophy", "value": random.choice(trophies)}
    nations = list({p["nation"] for p in pool if p.get("nation")})
    return {"type": "nation", "value": random.choice(nations) if nations else "India"}

def build_grid(size, ds, difficulty):
    pool = get_pool(ds)
    if not pool: return []
    n = size * size
    if difficulty == "easy":   types = ["team"] * n
    elif difficulty == "hard": types = ["team"] * (n // 3) + ["nation"] * (n // 3) + ["combo"] * (n - 2 * (n // 3))
    else:                      types = ["team"] * (n // 2) + ["nation"] * (n - n // 2)
    random.shuffle(types)
    cells, seen = [], set()
    for t in types:
        for _ in range(20):
            cell = gen_cell(pool, ds, difficulty, t)
            if cell["value"] not in seen:
                seen.add(cell["value"]); cells.append(cell); break
        else:
            cells.append(gen_cell(pool, ds, difficulty, t))
    return cells

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

def create_game_state(ds, grid_size, difficulty, seed=None):
    if seed is not None: random.seed(seed)
    pool = list(get_pool(ds))
    if not pool:
        log.error(f"No players found for data source: {ds}")
        return None
    random.shuffle(pool)
    n = grid_size * grid_size
    selected = pool[:min(len(pool), n * 3)]
    grid = build_grid(grid_size, ds, difficulty)
    state = {
        "data_source": ds, "grid_size": grid_size, "difficulty": difficulty,
        "grid": grid, "players": selected,
        "current_player_idx": 0, "grid_state": [None] * n,
        "skips_used": 0, "wildcard_used": False, "correct": 0, "wrong": 0,
        "started_at": time.time(), "seed": seed or random.randint(0, 9999999),
    }
    return state

def elo_expected(a, b): return 1 / (1 + 10 ** ((b - a) / 400))
def elo_update(r, exp, act, k=32): return r + k * (act - exp)

def get_user_rating(uid, sid):
    row = query_db("SELECT rating FROM season_ratings WHERE user_id=? AND season_id=?", (uid, sid), one=True)
    return row["rating"] if row else 1200.0

def ensure_season_rating(uid, sid):
    query_db("INSERT OR IGNORE INTO season_ratings(user_id,season_id,rating) VALUES(?,?,1200)",
             (uid, sid), commit=True)

def rating_tier(r):
    if r < 1000:   return ("Beginner", "#9CA3AF", "🟤")
    elif r < 1200: return ("Amateur",  "#60A5FA", "🔵")
    elif r < 1400: return ("Pro",      "#34D399", "🟢")
    elif r < 1600: return ("Elite",    "#FBBF24", "🟡")
    else:          return ("Legend",   "#F87171", "🔴")

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
    return "".join(random.choices(string.digits, k=6))

# ═══════════════════════════════════════════════════════════════════════════════
#  DESIGN SYSTEM — Cricket Bingo v4 (Dark + Light Mode)
# ═══════════════════════════════════════════════════════════════════════════════

ADSENSE = """<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9904803540658016" crossorigin="anonymous"></script>"""

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
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');

/* ══════════════════════════════════
   DARK MODE TOKENS (default)
══════════════════════════════════ */
:root {
  --bg:         #050A12;
  --bg2:        #080E18;
  --sur:        #0D1525;
  --sur2:       #121C30;
  --sur3:       #19243D;
  --sur4:       #1F2C47;
  --bdr:        rgba(255,255,255,.06);
  --bdr2:       rgba(255,255,255,.11);
  --bdr3:       rgba(255,255,255,.18);
  --acc:        #22C55E;
  --acc2:       #16A34A;
  --acc3:       #4ADE80;
  --acc-glow:   rgba(34,197,94,.22);
  --acc-subtle: rgba(34,197,94,.08);
  --blue:       #3B82F6;
  --amber:      #F59E0B;
  --red:        #EF4444;
  --pur:        #A855F7;
  --cyn:        #22D3EE;
  --txt:        #EDF2FF;
  --txt2:       #8899BB;
  --txt3:       #3D4F6E;
  --txt4:       #273348;
  --font:       'Sora', sans-serif;
  --mono:       'Space Mono', monospace;
  --r-sm:       6px;
  --r-md:       10px;
  --r-lg:       16px;
  --r-xl:       22px;
  --r-2xl:      30px;
  --shadow-sm:  0 2px 8px rgba(0,0,0,.35);
  --shadow:     0 6px 28px rgba(0,0,0,.5);
  --shadow-lg:  0 16px 56px rgba(0,0,0,.65);
  --shadow-glow:0 0 40px rgba(34,197,94,.12);
  --grd-acc:    linear-gradient(135deg,#22C55E,#16A34A);
  --grd-hero:   linear-gradient(135deg,#22C55E 0%,#06B6D4 50%,#818CF8 100%);
  --grd-warm:   linear-gradient(135deg,#F59E0B,#EF4444);
  --grd-cool:   linear-gradient(135deg,#3B82F6,#8B5CF6);
  --orb1:       rgba(34,197,94,.05);
  --orb2:       rgba(59,130,246,.04);
  --orb3:       rgba(168,85,247,.03);
  --nav-bg:     rgba(5,10,18,.88);
  --card-bg:    rgba(13,21,37,.9);
  --glass:      rgba(13,21,37,.7);
  --theme-icon: '🌙';
}

/* ══════════════════════════════════
   LIGHT MODE TOKENS
══════════════════════════════════ */
[data-theme="light"] {
  --bg:         #F0F4FF;
  --bg2:        #E8EDF8;
  --sur:        #FFFFFF;
  --sur2:       #F5F7FF;
  --sur3:       #EBEEf8;
  --sur4:       #E0E5F2;
  --bdr:        rgba(0,0,0,.07);
  --bdr2:       rgba(0,0,0,.12);
  --bdr3:       rgba(0,0,0,.2);
  --acc:        #16A34A;
  --acc2:       #15803D;
  --acc3:       #22C55E;
  --acc-glow:   rgba(22,163,74,.2);
  --acc-subtle: rgba(22,163,74,.08);
  --blue:       #2563EB;
  --amber:      #D97706;
  --red:        #DC2626;
  --pur:        #9333EA;
  --cyn:        #0891B2;
  --txt:        #0F1B35;
  --txt2:       #3D5280;
  --txt3:       #8899BB;
  --txt4:       #BCC8E0;
  --bdr:        rgba(0,0,0,.07);
  --orb1:       rgba(34,197,94,.07);
  --orb2:       rgba(59,130,246,.06);
  --orb3:       rgba(168,85,247,.05);
  --nav-bg:     rgba(240,244,255,.92);
  --card-bg:    rgba(255,255,255,.95);
  --glass:      rgba(255,255,255,.75);
  --shadow-sm:  0 2px 8px rgba(0,0,0,.08);
  --shadow:     0 6px 28px rgba(0,0,0,.12);
  --shadow-lg:  0 16px 56px rgba(0,0,0,.16);
  --shadow-glow:0 0 40px rgba(22,163,74,.08);
  --theme-icon: '☀️';
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; font-size: 16px; }

body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--txt);
  min-height: 100vh;
  overflow-x: hidden;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  transition: background 0.35s ease, color 0.35s ease;
}

/* ── Atmospheric Orbs ── */
.bg-orbs {
  position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden;
}
.orb {
  position: absolute; border-radius: 50%; filter: blur(80px);
  animation: orb-drift 20s ease-in-out infinite;
}
.orb-1 { width: 600px; height: 600px; background: var(--orb1); top: -200px; left: -200px; animation-delay: 0s; }
.orb-2 { width: 500px; height: 500px; background: var(--orb2); bottom: -150px; right: -150px; animation-delay: -7s; }
.orb-3 { width: 400px; height: 400px; background: var(--orb3); top: 50%; left: 60%; animation-delay: -14s; }
@keyframes orb-drift {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(40px, -30px) scale(1.05); }
  66% { transform: translate(-25px, 20px) scale(0.97); }
}
* { position: relative; z-index: 1; }

/* ── TYPOGRAPHY ── */
h1,h2,h3,h4 { font-family: var(--font); line-height: 1.2; font-weight: 800; }
.display { font-size: clamp(2.2rem, 6vw, 4rem); font-weight: 800; letter-spacing: -2px; }
.title   { font-size: clamp(1.4rem, 3vw, 2rem); font-weight: 700; letter-spacing: -.5px; }
.heading { font-size: 1.1rem; font-weight: 700; }
.subhead { font-size: .95rem; font-weight: 400; color: var(--txt2); }
.mono    { font-family: var(--mono); }
.label   { display: block; font-size: .7rem; font-weight: 700; color: var(--txt3); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 7px; }

/* ── GRADIENT TEXT ── */
.grad-green { background: var(--grd-acc);  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.grad-hero  { background: var(--grd-hero); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.grad-warm  { background: var(--grd-warm); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.grad-cool  { background: var(--grd-cool); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }

/* ── LAYOUT ── */
.container    { max-width: 1100px; margin: 0 auto; padding: 0 24px; }
.container-sm { max-width: 680px;  margin: 0 auto; padding: 0 24px; }
.container-xs { max-width: 480px;  margin: 0 auto; padding: 0 24px; }
.page         { padding: 48px 0 100px; }
.section      { margin-bottom: 40px; }

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid-3 { display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; }
.grid-4 { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; }
.flex   { display: flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.justify-between { justify-content: space-between; }
.justify-center  { justify-content: center; }
.flex-wrap { flex-wrap: wrap; }
.gap-2 { gap: 8px; } .gap-3 { gap: 12px; } .gap-4 { gap: 16px; } .gap-6 { gap: 24px; }
.w-full { width: 100%; } .text-center { text-align: center; }
.mt-2{margin-top:8px;} .mt-3{margin-top:12px;} .mt-4{margin-top:16px;} .mt-6{margin-top:24px;} .mt-8{margin-top:32px;}
.mb-2{margin-bottom:8px;} .mb-3{margin-bottom:12px;} .mb-4{margin-bottom:16px;} .mb-6{margin-bottom:24px;} .mb-8{margin-bottom:32px;}

/* ── COLORS ── */
.text-muted   { color: var(--txt2); }
.text-subtle  { color: var(--txt3); }
.text-green   { color: var(--acc); }
.text-red     { color: var(--red); }
.text-amber   { color: var(--amber); }
.text-blue    { color: var(--blue); }
.text-pur     { color: var(--pur); }

/* ── NAVBAR ── */
.nav {
  position: sticky; top: 0; z-index: 500;
  background: var(--nav-bg);
  backdrop-filter: blur(24px) saturate(1.8);
  -webkit-backdrop-filter: blur(24px) saturate(1.8);
  border-bottom: 1px solid var(--bdr);
  height: 64px;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px;
  transition: background 0.35s ease;
}
.nav-logo {
  display: flex; align-items: center; gap: 10px;
  text-decoration: none; font-weight: 800; font-size: 1.05rem; color: var(--txt);
  letter-spacing: -.3px;
}
.nav-logo-icon {
  width: 36px; height: 36px;
  background: var(--grd-acc);
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 1.1rem; flex-shrink: 0;
  box-shadow: 0 4px 12px var(--acc-glow);
  transition: transform 0.2s, box-shadow 0.2s;
}
.nav-logo:hover .nav-logo-icon { transform: rotate(-5deg) scale(1.05); box-shadow: 0 6px 20px var(--acc-glow); }

.nav-links { display: flex; align-items: center; gap: 2px; }
.nav-link {
  color: var(--txt2); font-size: .85rem; font-weight: 500;
  padding: 7px 13px; border-radius: var(--r-md);
  text-decoration: none; transition: all .18s; white-space: nowrap;
}
.nav-link:hover { color: var(--txt); background: var(--sur2); }
.nav-link.active { color: var(--acc); }

.nav-actions { display: flex; align-items: center; gap: 10px; }
.nav-burger { display: none; flex-direction: column; gap: 5px; cursor: pointer; padding: 8px; border-radius: var(--r-sm); transition: background 0.2s; }
.nav-burger:hover { background: var(--sur2); }
.nav-burger span { width: 22px; height: 2px; background: var(--txt2); border-radius: 2px; transition: .3s; display: block; }

/* Theme Toggle */
.theme-toggle {
  width: 38px; height: 38px;
  background: var(--sur2);
  border: 1.5px solid var(--bdr2);
  border-radius: var(--r-md);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 1rem;
  transition: all 0.2s; flex-shrink: 0;
  color: var(--txt2);
}
.theme-toggle:hover { background: var(--sur3); color: var(--txt); transform: scale(1.05); }

/* Mobile menu */
.mobile-menu {
  display: none; position: fixed; top: 64px; left: 0; right: 0;
  background: var(--bg2);
  border-bottom: 1px solid var(--bdr);
  padding: 12px 16px 16px; flex-direction: column; gap: 3px;
  z-index: 499;
  transform: translateY(-8px); opacity: 0;
  transition: transform 0.25s ease, opacity 0.25s ease;
  pointer-events: none;
}
.mobile-menu.open { display: flex; transform: translateY(0); opacity: 1; pointer-events: all; }
.mobile-menu .nav-link { padding: 12px 16px; font-size: .9rem; border-radius: var(--r-lg); }
.mobile-menu .nav-link:hover { background: var(--sur); }

/* ── BUTTONS ── */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  padding: 10px 20px; border-radius: var(--r-lg);
  font-family: var(--font); font-size: .875rem; font-weight: 600;
  cursor: pointer; border: none; transition: all .2s;
  text-decoration: none; white-space: nowrap; line-height: 1;
}
.btn:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px; }
.btn:disabled { opacity: .38; cursor: not-allowed; pointer-events: none; }

.btn-primary {
  background: var(--grd-acc); color: #fff;
  box-shadow: 0 4px 18px var(--acc-glow);
}
.btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 28px var(--acc-glow); filter: brightness(1.07); }
.btn-primary:active { transform: translateY(0); }

.btn-secondary {
  background: var(--sur2); color: var(--txt);
  border: 1.5px solid var(--bdr2);
}
.btn-secondary:hover { background: var(--sur3); border-color: var(--bdr3); transform: translateY(-1px); }

.btn-outline {
  background: transparent; color: var(--txt2);
  border: 1.5px solid var(--bdr2);
}
.btn-outline:hover { color: var(--txt); border-color: var(--acc); background: var(--acc-subtle); }

.btn-danger  { background: var(--red); color: #fff; }
.btn-danger:hover { filter: brightness(1.1); transform: translateY(-1px); }
.btn-ghost { background: transparent; color: var(--txt2); border: none; }
.btn-ghost:hover { color: var(--txt); background: var(--sur2); }

.btn-google {
  background: var(--sur); color: var(--txt); font-weight: 600;
  box-shadow: var(--shadow-sm);
  border: 1.5px solid var(--bdr2);
}
.btn-google:hover { transform: translateY(-2px); box-shadow: var(--shadow); border-color: var(--bdr3); }

.btn-lg  { padding: 14px 28px; font-size: 1rem; border-radius: var(--r-xl); }
.btn-sm  { padding: 7px 14px; font-size: .8rem; border-radius: var(--r-md); }
.btn-xs  { padding: 4px 10px; font-size: .72rem; border-radius: var(--r-sm); }

/* ── CARDS ── */
.card {
  background: var(--card-bg);
  border: 1px solid var(--bdr);
  border-radius: var(--r-xl);
  padding: 24px;
  backdrop-filter: blur(12px);
  transition: background 0.3s, border-color 0.3s;
}
.card-sm {
  background: var(--card-bg);
  border: 1px solid var(--bdr);
  border-radius: var(--r-lg);
  padding: 18px;
  backdrop-filter: blur(12px);
}
.card-hover { transition: border-color .2s, transform .25s, box-shadow .25s; }
.card-hover:hover {
  border-color: rgba(34,197,94,.25);
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}
.card-accent {
  border-color: rgba(34,197,94,.3);
  background: linear-gradient(135deg, rgba(34,197,94,.05), var(--card-bg));
}
.card-glow { box-shadow: var(--shadow-glow); border-color: rgba(34,197,94,.2); }

/* Gradient border card */
.card-gradient-border {
  background: var(--card-bg);
  border-radius: var(--r-xl);
  padding: 1px;
  background-clip: padding-box;
  position: relative;
}
.card-gradient-border::before {
  content: '';
  position: absolute; inset: 0;
  border-radius: var(--r-xl);
  padding: 1px;
  background: linear-gradient(135deg, rgba(34,197,94,.4), rgba(59,130,246,.2), rgba(168,85,247,.1));
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
.card-gradient-border .card-inner {
  background: var(--card-bg);
  border-radius: calc(var(--r-xl) - 1px);
  padding: 24px;
}

/* ── INPUTS ── */
.input {
  background: var(--sur2);
  border: 1.5px solid var(--bdr);
  border-radius: var(--r-md);
  padding: 11px 15px;
  color: var(--txt); font-size: .9rem; font-family: var(--font);
  width: 100%; outline: none;
  transition: border-color .2s, box-shadow .2s, background 0.3s;
}
.input:focus { border-color: var(--acc); box-shadow: 0 0 0 3px rgba(34,197,94,.12); }
.input::placeholder { color: var(--txt3); }
select.input option { background: var(--sur); color: var(--txt); }
.input-group { display: flex; flex-direction: column; }

/* ── TABLE ── */
.table-wrap { overflow-x: auto; border-radius: var(--r-xl); border: 1px solid var(--bdr); }
table  { width: 100%; border-collapse: collapse; }
th { padding: 12px 18px; text-align: left; font-size: .7rem; font-weight: 700; color: var(--txt3); text-transform: uppercase; letter-spacing: .07em; background: var(--sur2); border-bottom: 1px solid var(--bdr); }
td { padding: 13px 18px; font-size: .875rem; border-bottom: 1px solid var(--bdr); transition: background 0.15s; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--acc-subtle); }

/* ── STAT CARD ── */
.stat-card {
  background: var(--sur);
  border: 1px solid var(--bdr);
  border-radius: var(--r-lg);
  padding: 20px 16px;
  text-align: center;
  transition: border-color 0.2s, transform 0.2s;
}
.stat-card:hover { border-color: var(--bdr2); transform: translateY(-2px); }
.stat-value { font-size: 2rem; font-weight: 800; line-height: 1; letter-spacing: -1.5px; }
.stat-label { font-size: .7rem; font-weight: 600; color: var(--txt3); text-transform: uppercase; letter-spacing: .06em; margin-top: 6px; }

/* ── BADGE ── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 999px;
  font-size: .7rem; font-weight: 700;
  border: 1px solid currentColor; white-space: nowrap;
}

/* ── PROGRESS & TIMER ── */
.progress-wrap { background: var(--sur3); border-radius: 999px; overflow: hidden; }
.progress-bar  { height: 100%; border-radius: 999px; transition: width .4s ease; }
.timer-wrap { background: var(--sur3); border-radius: 999px; height: 5px; overflow: hidden; }
.timer-bar  { height: 100%; border-radius: 999px; transition: width .95s linear, background .4s; }

/* ── BINGO GRID ── */
.bingo-grid { display: grid; gap: 10px; margin: 0 auto; width: 100%; }
.bingo-grid.size-3 { grid-template-columns: repeat(3,1fr); max-width: 520px; }
.bingo-grid.size-4 { grid-template-columns: repeat(4,1fr); max-width: 640px; }

.cell {
  background: var(--sur2);
  border: 2px solid var(--bdr);
  border-radius: var(--r-lg);
  padding: 12px 8px;
  text-align: center;
  cursor: pointer;
  transition: all .2s cubic-bezier(.34,1.56,.64,1);
  min-height: 90px;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 6px;
  user-select: none;
  overflow: hidden;
}
.cell-logo {
  width: 48px; height: 48px; object-fit: contain;
  border-radius: 6px; transition: transform .2s;
}
.cell-label {
  font-size: .65rem; font-weight: 700; color: var(--txt2);
  line-height: 1.2; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis;
}
.cell.nation-cell { font-size: .82rem; font-weight: 700; color: var(--txt); }
.cell.trophy-cell { font-size: .72rem; font-weight: 700; color: var(--amber); }
.cell.combo-cell  { font-size: .62rem; font-weight: 700; color: var(--pur); line-height: 1.4; }

.cell:hover:not(.filled):not(.cell-disabled) {
  border-color: var(--acc);
  background: var(--acc-subtle);
  transform: scale(1.06) translateY(-2px);
  box-shadow: 0 8px 24px var(--acc-glow);
}
.cell:hover .cell-logo { transform: scale(1.1); }
.cell.filled {
  background: linear-gradient(135deg, rgba(34,197,94,.12), rgba(34,197,94,.06));
  border-color: var(--acc);
  cursor: default;
  animation: cell-pop .35s cubic-bezier(.34,1.56,.64,1);
}
.cell.filled .cell-logo { filter: drop-shadow(0 0 8px var(--acc-glow)); }
.cell.wrong  { animation: cell-shake .4s ease; border-color: var(--red); background: rgba(239,68,68,.1); }
.cell.hint   { border-color: var(--amber); background: rgba(245,158,11,.08); box-shadow: 0 0 16px rgba(245,158,11,.15); }

.cell-fill-name {
  position: absolute; inset: 0;
  background: rgba(34,197,94,.15);
  display: flex; align-items: center; justify-content: center;
  border-radius: 14px;
  font-size: .58rem; font-weight: 700; color: var(--acc);
  padding: 4px; text-align: center; line-height: 1.2;
  pointer-events: none;
}

@keyframes cell-pop   { 0%{transform:scale(1.18);}60%{transform:scale(.97);}100%{transform:scale(1);} }
@keyframes cell-shake { 0%,100%{transform:translateX(0);}20%{transform:translateX(-9px);}60%{transform:translateX(7px);} }

/* ── PLAYER CARD ── */
.player-card {
  background: var(--card-bg);
  border: 2px solid rgba(34,197,94,.3);
  border-radius: var(--r-xl);
  padding: 22px 28px;
  text-align: center;
  overflow: hidden;
  box-shadow: 0 0 48px rgba(34,197,94,.07);
}
.player-card::before {
  content: '';
  position: absolute; inset: 0;
  background: radial-gradient(ellipse at 50% -15%, rgba(34,197,94,.1), transparent 65%);
  pointer-events: none;
}
.player-name {
  font-size: clamp(1.3rem, 3.5vw, 2rem);
  font-weight: 800; letter-spacing: -.8px;
  background: var(--grd-hero);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.player-hint { font-size: .8rem; color: var(--txt3); }

/* ── MODAL ── */
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,.75); backdrop-filter: blur(10px);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000; padding: 16px;
  animation: fade-in .2s ease;
}
.modal {
  background: var(--sur);
  border: 1px solid var(--bdr2);
  border-radius: var(--r-2xl);
  padding: 36px;
  max-width: 440px; width: 100%;
  box-shadow: var(--shadow-lg);
  animation: slide-up .3s cubic-bezier(.34,1.56,.64,1);
}

@keyframes fade-in  { from { opacity: 0; } to { opacity: 1; } }
@keyframes slide-up { from { transform: translateY(32px) scale(.97); opacity: 0; } to { transform: none; opacity: 1; } }

/* ── TOAST ── */
#toasts {
  position: fixed; bottom: 24px; right: 20px;
  z-index: 9999; display: flex; flex-direction: column; gap: 8px; pointer-events: none;
}
.toast {
  background: var(--sur);
  border: 1px solid var(--bdr2);
  border-radius: var(--r-lg);
  padding: 12px 16px;
  font-size: .83rem; font-weight: 500;
  max-width: 260px;
  display: flex; align-items: center; gap: 8px;
  box-shadow: var(--shadow);
  animation: toast-in .28s cubic-bezier(.34,1.56,.64,1);
}
.toast-success { border-left: 3px solid var(--acc); }
.toast-error   { border-left: 3px solid var(--red); }
.toast-info    { border-left: 3px solid var(--blue); }
.toast-warn    { border-left: 3px solid var(--amber); }
@keyframes toast-in { from { transform: translateX(20px) scale(.95); opacity: 0; } to { transform: none; opacity: 1; } }

/* ── SPINNER ── */
.spinner {
  width: 36px; height: 36px; border-radius: 50%;
  border: 3px solid var(--bdr2); border-top-color: var(--acc);
  animation: spin .7s linear infinite; margin: 0 auto;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── ROOM CODE ── */
.room-code-display {
  font-family: var(--mono);
  font-size: 2.4rem; font-weight: 700;
  letter-spacing: 14px; color: var(--acc);
  text-align: center; padding: 20px;
  background: var(--acc-subtle);
  border-radius: var(--r-xl);
  border: 2px dashed rgba(34,197,94,.4);
  cursor: pointer; transition: all .2s;
}
.room-code-display:hover { background: rgba(34,197,94,.12); transform: scale(1.01); }

/* ── AD SLOTS ── */
.ad-slot {
  background: var(--sur2);
  border: 1px solid var(--bdr);
  border-radius: var(--r-lg);
  min-height: 90px;
  display: flex; align-items: center; justify-content: center;
  margin: 16px 0; overflow: hidden;
}
.ad-rect { min-height: 250px; }

/* ── FOOTER ── */
.footer {
  background: var(--bg2);
  border-top: 1px solid var(--bdr);
  padding: 48px 28px 32px;
  margin-top: 80px;
}
.footer-grid {
  max-width: 1100px; margin: 0 auto;
  display: grid; grid-template-columns: 1.8fr 1fr 1fr 1fr; gap: 48px;
  margin-bottom: 36px;
}
.footer-brand p { font-size: .85rem; color: var(--txt2); line-height: 1.8; margin-top: 12px; }
.footer-col h4  { font-size: .8rem; font-weight: 700; color: var(--txt); margin-bottom: 16px; text-transform: uppercase; letter-spacing: .06em; }
.footer-col a   { display: block; color: var(--txt2); font-size: .85rem; text-decoration: none; margin-bottom: 10px; transition: color .15s, transform .15s; }
.footer-col a:hover { color: var(--acc); transform: translateX(3px); }
.footer-bottom  { max-width: 1100px; margin: 0 auto; padding-top: 24px; border-top: 1px solid var(--bdr); }
.footer-bottom p { font-size: .75rem; color: var(--txt3); }

/* ── HERO ── */
.hero-section { text-align: center; padding: 80px 0 64px; }
.hero-badge {
  display: inline-flex; align-items: center; gap: 7px;
  background: var(--acc-subtle); border: 1px solid rgba(34,197,94,.3);
  color: var(--acc); font-size: .78rem; font-weight: 600;
  padding: 6px 16px; border-radius: 999px; margin-bottom: 24px;
}
.hero-badge::before { content: ''; width: 6px; height: 6px; background: var(--acc); border-radius: 50%; animation: pulse-dot 1.5s ease infinite; }

/* ── FEATURE CARDS ── */
.feature-card {
  background: var(--sur);
  border: 1px solid var(--bdr);
  border-radius: var(--r-xl);
  padding: 32px 24px;
  text-align: center;
  transition: all .28s cubic-bezier(.34,1.56,.64,1);
  cursor: default;
}
.feature-card:hover {
  transform: translateY(-6px);
  border-color: rgba(34,197,94,.25);
  box-shadow: 0 20px 56px rgba(0,0,0,.4), var(--shadow-glow);
}
.feature-icon {
  width: 56px; height: 56px; border-radius: var(--r-lg);
  background: var(--acc-subtle); border: 1px solid rgba(34,197,94,.2);
  display: flex; align-items: center; justify-content: center;
  font-size: 1.6rem; margin: 0 auto 16px;
}
.feature-card h3 { font-size: .95rem; font-weight: 700; margin-bottom: 8px; }
.feature-card p  { font-size: .85rem; color: var(--txt2); line-height: 1.7; }

/* ── STEP CARDS ── */
.step-card {
  background: var(--card-bg);
  border: 1px solid var(--bdr);
  border-radius: var(--r-2xl);
  padding: 32px;
  max-width: 540px; margin: 0 auto;
  animation: fade-in .3s ease;
  box-shadow: var(--shadow);
}
.mode-btn {
  background: var(--sur2);
  border: 1.5px solid var(--bdr);
  border-radius: var(--r-xl);
  padding: 20px 16px;
  text-align: center;
  cursor: pointer;
  transition: all .22s cubic-bezier(.34,1.56,.64,1);
  font-family: var(--font);
}
.mode-btn:hover { border-color: rgba(34,197,94,.35); background: var(--acc-subtle); transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,.3); }
.mode-btn:active { transform: translateY(-1px); }
.mode-btn .mode-icon { font-size: 1.7rem; display: block; margin-bottom: 10px; }
.mode-btn .mode-title { font-size: .9rem; font-weight: 700; color: var(--txt); display: block; margin-bottom: 4px; }
.mode-btn .mode-sub   { font-size: .75rem; color: var(--txt2); display: block; }

/* ── MATCHMAKING ── */
.mm-card {
  max-width: 400px; margin: 80px auto;
  text-align: center;
  background: var(--card-bg);
  border: 1px solid var(--bdr);
  border-radius: var(--r-2xl);
  padding: 52px 40px;
  box-shadow: var(--shadow-lg);
}
.mm-dots { display: flex; justify-content: center; align-items: center; gap: 6px; margin-bottom: 28px; }
.mm-dots span {
  width: 9px; height: 9px;
  background: var(--acc); border-radius: 50%;
  animation: pulse-dot 1.4s ease infinite;
}
.mm-dots span:nth-child(2) { animation-delay: .22s; }
.mm-dots span:nth-child(3) { animation-delay: .44s; }

/* ── SCORE DISPLAY ── */
.score-display {
  font-size: 3.5rem; font-weight: 800; letter-spacing: -3px;
  background: var(--grd-acc);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  line-height: 1;
}

/* ── DIVIDERS ── */
hr { border: none; border-top: 1px solid var(--bdr); margin: 24px 0; }
.divider { height: 1px; background: var(--bdr); margin: 24px 0; }

/* ── MISC ── */
@keyframes pulse-dot { 0%,100%{opacity:.35;transform:scale(.9);} 50%{opacity:1;transform:scale(1.1);} }
.pulse { animation: pulse-dot 1.5s ease infinite; }

/* ── SCROLLBAR ── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--sur4); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--txt3); }

/* ── RESPONSIVE ── */
@media (max-width: 900px) {
  .footer-grid { grid-template-columns: 1fr 1fr; gap: 32px; }
}
@media (max-width: 768px) {
  .nav-links  { display: none; }
  .nav-burger { display: flex; }
  .grid-3 { grid-template-columns: 1fr 1fr; }
  .grid-4 { grid-template-columns: 1fr 1fr; }
  .footer-grid { grid-template-columns: 1fr; gap: 24px; }
  .hide-sm { display: none; }
  .hero-section { padding: 56px 0 44px; }
  .step-card { padding: 24px; }
}
@media (max-width: 480px) {
  .grid-2 { grid-template-columns: 1fr; }
  .cell { min-height: 76px; padding: 8px 5px; }
  .cell-logo { width: 38px; height: 38px; }
  .player-name { font-size: 1.2rem; }
  .room-code-display { font-size: 1.8rem; letter-spacing: 8px; }
  .container, .container-sm { padding: 0 16px; }
  .modal { padding: 24px 20px; }
}

/* ── LIGHT MODE OVERRIDES ── */
[data-theme="light"] .cell { background: var(--sur2); }
[data-theme="light"] .cell:hover:not(.filled):not(.cell-disabled) { background: var(--acc-subtle); }
[data-theme="light"] .player-card { box-shadow: 0 0 40px rgba(22,163,74,.06); }
[data-theme="light"] .feature-card:hover { box-shadow: 0 20px 56px rgba(0,0,0,.12), var(--shadow-glow); }
[data-theme="light"] body::after { display: none; }

/* ── TRANSITION for theme switch ── */
body, .nav, .card, .card-sm, .step-card, .modal, .mm-card, .toast, .cell, .input, .btn-secondary, .btn-outline, .footer, table, th, td { transition-property: background, border-color, color, box-shadow; transition-duration: 0.3s; transition-timing-function: ease; }
</style>
"""


# ── SHARED HTML COMPONENTS ─────────────────────────────────────────────────────

def NAV_HTML():
    return """
<nav class="nav" role="navigation" aria-label="Main navigation">
  <a class="nav-logo" href="/" aria-label="Cricket Bingo home">
    <div class="nav-logo-icon" aria-hidden="true">🏏</div>
    <span>Cricket Bingo</span>
  </a>
  <div class="nav-links" role="menubar">
    <a class="nav-link" href="/" role="menuitem">Play</a>
    <a class="nav-link" href="/leaderboard" role="menuitem">Leaderboard</a>
    <a class="nav-link" href="/daily" role="menuitem">Daily</a>
    <a class="nav-link" href="/about" role="menuitem">About</a>
    <a class="nav-link" href="/contact" role="menuitem">Contact</a>
  </div>
  <div class="nav-actions">
    {% if current_user.is_authenticated %}
      <a class="nav-link" href="/profile/{{ current_user.id }}" style="color:var(--txt);">
        <img src="{{ current_user.avatar or '' }}" style="width:26px;height:26px;border-radius:50%;object-fit:cover;border:2px solid var(--acc);vertical-align:middle;margin-right:6px;"
          onerror="this.style.display='none'" alt="">
        {{ current_user.name.split()[0] }}
      </a>
      <a href="/logout" class="btn btn-outline btn-sm">Sign Out</a>
    {% else %}
      <a href="/login/google" class="btn btn-primary btn-sm">Sign In</a>
    {% endif %}
    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle dark/light mode" aria-label="Toggle theme" id="themeBtn">☀️</button>
    <div class="nav-burger" onclick="toggleMenu()" aria-label="Toggle menu" aria-expanded="false" id="navBurger">
      <span></span><span></span><span></span>
    </div>
  </div>
</nav>
<div class="mobile-menu" id="mmenu" role="menu">
  <a class="nav-link" href="/" onclick="closeMenu()" role="menuitem">🏠 Home</a>
  <a class="nav-link" href="/leaderboard" onclick="closeMenu()" role="menuitem">🏆 Leaderboard</a>
  <a class="nav-link" href="/daily" onclick="closeMenu()" role="menuitem">📅 Daily Challenge</a>
  <a class="nav-link" href="/about" onclick="closeMenu()" role="menuitem">ℹ️ About</a>
  <a class="nav-link" href="/contact" onclick="closeMenu()" role="menuitem">✉️ Contact</a>
  <a class="nav-link" href="/privacy" onclick="closeMenu()" role="menuitem">🔒 Privacy</a>
  <a class="nav-link" href="/terms" onclick="closeMenu()" role="menuitem">📋 Terms</a>
  {% if current_user.is_authenticated %}
    <a class="nav-link" href="/profile/{{ current_user.id }}" onclick="closeMenu()" role="menuitem">👤 My Profile</a>
    <a class="nav-link" href="/logout" onclick="closeMenu()" role="menuitem">← Sign Out</a>
  {% else %}
    <a class="nav-link" href="/login/google" onclick="closeMenu()" role="menuitem">🔑 Sign In with Google</a>
  {% endif %}
</div>
"""

FOOTER_HTML = """
<footer class="footer" role="contentinfo">
  <div class="footer-grid">
    <div class="footer-brand">
      <a class="nav-logo" href="/" style="display:inline-flex;text-decoration:none;">
        <div class="nav-logo-icon" style="width:32px;height:32px;font-size:1rem;border-radius:8px;">🏏</div>
        <span style="font-weight:800;color:var(--txt);font-size:1rem;margin-left:8px;">Cricket Bingo</span>
      </a>
      <p>The ultimate IPL cricket quiz. Match legends to their teams, nations &amp; trophies.</p>
      <p style="margin-top:8px;font-size:.75rem;color:var(--txt3);">Fan-made · Not affiliated with BCCI or IPL</p>
    </div>
    <div class="footer-col">
      <h4>Play</h4>
      <a href="/">Home</a>
      <a href="/daily">Daily Challenge</a>
      <a href="/leaderboard">Leaderboard</a>
    </div>
    <div class="footer-col">
      <h4>Company</h4>
      <a href="/about">About Us</a>
      <a href="/contact">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy">Privacy Policy</a>
      <a href="/terms">Terms &amp; Conditions</a>
    </div>
  </div>
  <div class="footer-bottom flex justify-between items-center flex-wrap gap-4">
    <p>© 2025 Cricket Bingo · Fan-made IPL knowledge game</p>
    <button onclick="toggleTheme()" style="background:var(--sur2);border:1px solid var(--bdr2);color:var(--txt2);padding:6px 14px;border-radius:99px;font-size:.78rem;cursor:pointer;font-family:var(--font);">
      Toggle Theme
    </button>
  </div>
</footer>
"""

GLOBAL_SCRIPTS = """
<div id="toasts" role="status" aria-live="polite"></div>
<script>
/* ── THEME SYSTEM ── */
(function(){
  const saved = localStorage.getItem('cb-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcon(saved);
})();

function updateThemeIcon(theme){
  const btn = document.getElementById('themeBtn');
  if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
}
function toggleTheme(){
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('cb-theme', next);
  updateThemeIcon(next);
}

/* ── TOAST ── */
function toast(msg, type='info'){
  const d = document.createElement('div');
  d.className = 'toast toast-' + type;
  d.setAttribute('role', 'alert');
  d.textContent = msg;
  const container = document.getElementById('toasts');
  container.appendChild(d);
  setTimeout(() => {
    d.style.opacity = '0'; d.style.transform = 'translateX(20px)';
    d.style.transition = '.25s ease';
    setTimeout(() => d.remove(), 280);
  }, 2800);
}

/* ── NAV ── */
function toggleMenu(){
  const m = document.getElementById('mmenu');
  const b = document.getElementById('navBurger');
  const isOpen = m.classList.toggle('open');
  if (b) b.setAttribute('aria-expanded', isOpen);
}
function closeMenu(){
  const m = document.getElementById('mmenu');
  m.classList.remove('open');
  const b = document.getElementById('navBurger');
  if (b) b.setAttribute('aria-expanded', 'false');
}
document.addEventListener('click', e => {
  const m = document.getElementById('mmenu');
  if(m && !m.contains(e.target) && !e.target.closest('.nav-burger') && !e.target.closest('#navBurger')) m.classList.remove('open');
});

/* ── Keyboard nav ── */
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeMenu();
});
</script>
"""

def page(body, title="Cricket Bingo", extra_head=""):
    nav = NAV_HTML()
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title} — Cricket Bingo</title>
{SEO_META}
{GOOGLE_ANALYTICS}
{ADSENSE}
{CSS}
{extra_head}
<script>
// Apply saved theme immediately to prevent flash
(function(){{
  const t = localStorage.getItem('cb-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
}})();
</script>
</head>
<body>
<div class="bg-orbs" aria-hidden="true">
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>
  <div class="orb orb-3"></div>
</div>
{nav}
{body}
{FOOTER_HTML}
{GLOBAL_SCRIPTS}
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE BODIES
# ═══════════════════════════════════════════════════════════════════════════════

HOME_BODY = """
<div class="container page">

  <!-- TOP AD -->
  <div class="ad-slot mb-6">
    <ins class="adsbygoogle" style="display:block;width:100%;height:90px;"
      data-ad-client="ca-pub-9904803540658016" data-ad-slot="auto" data-ad-format="horizontal" data-full-width-responsive="true"></ins>
    <script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>
  </div>

  <!-- HERO -->
  <div class="hero-section">
    <span class="hero-badge">🏏 IPL Cricket Quiz Game</span>
    <h1 class="display grad-hero mb-4">Cricket Bingo</h1>
    <p class="subhead mb-8" style="max-width:520px;margin-left:auto;margin-right:auto;font-size:1.05rem;line-height:1.9;">
      Match cricket legends to their IPL teams, nations &amp; trophies.<br>
      Compete in rated matches or challenge your friends!
    </p>

    {% if not current_user.is_authenticated %}
      <div style="display:flex;flex-direction:column;align-items:center;gap:14px;">
        <a href="/login/google" class="btn btn-google btn-lg" style="gap:12px;padding:16px 32px;font-size:1rem;">
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57C21.36 18.09 22.56 15.27 22.56 12.25z" fill="#4285F4"/>
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
          </svg>
          Continue with Google
        </a>
        <p class="text-subtle" style="font-size:.82rem;">Free to play · No credit card needed</p>
      </div>

    {% else %}

      <!-- STEP 1: Pick Pool -->
      <div id="s1" class="step-card">
        <h2 class="heading mb-1">🎯 Start a Game</h2>
        <p class="text-muted mb-5" style="font-size:.875rem;">Choose your player pool to begin:</p>
        <div class="grid-2 gap-4">
          <button class="mode-btn" onclick="pickSrc('overall')" aria-label="Overall mode">
            <span class="mode-icon">🌍</span>
            <span class="mode-title">All-Time Overall</span>
            <span class="mode-sub">All IPL players 2008–2026</span>
          </button>
          <button class="mode-btn" onclick="pickSrc('ipl26')" aria-label="IPL 2026 mode">
            <span class="mode-icon">🏆</span>
            <span class="mode-title">IPL 2026 Edition</span>
            <span class="mode-sub">Current season squads</span>
          </button>
        </div>
      </div>

      <!-- STEP 2: Mode -->
      <div id="s2" class="step-card" style="display:none;">
        <div class="flex items-center gap-3 mb-5">
          <button onclick="back('s1','s2')" class="btn btn-ghost btn-sm">← Back</button>
          <h2 class="heading" id="s2-title"></h2>
        </div>
        <div class="grid-3 gap-3">
          <button class="mode-btn" onclick="pickMode('rated')">
            <span class="mode-icon">⚡</span>
            <span class="mode-title">Rated</span>
            <span class="mode-sub">ELO matchmaking</span>
          </button>
          <button class="mode-btn" onclick="pickMode('friends')">
            <span class="mode-icon">👥</span>
            <span class="mode-title">Friends</span>
            <span class="mode-sub">Room code</span>
          </button>
          <button class="mode-btn" onclick="pickMode('solo')">
            <span class="mode-icon">🎮</span>
            <span class="mode-title">Solo</span>
            <span class="mode-sub">Practice mode</span>
          </button>
        </div>
      </div>

      <!-- STEP 3: Rated -->
      <div id="s3-rated" class="step-card" style="display:none;">
        <div class="flex items-center gap-3 mb-5">
          <button onclick="back('s2','s3-rated')" class="btn btn-ghost btn-sm">← Back</button>
          <h2 class="heading">⚡ Rated Match</h2>
        </div>
        <div class="grid-2 gap-4 mb-5">
          <div class="input-group">
            <label class="label" for="gs-r">Grid Size</label>
            <select id="gs-r" class="input">
              <option value="3">3×3 Standard</option>
              <option value="4">4×4 Large</option>
            </select>
          </div>
          <div class="input-group">
            <label class="label" for="df-r">Difficulty</label>
            <select id="df-r" class="input">
              <option value="easy">Easy — Teams only</option>
              <option value="normal" selected>Normal — Teams &amp; Nations</option>
              <option value="hard">Hard — All + Combos</option>
            </select>
          </div>
        </div>
        <button class="btn btn-primary w-full btn-lg" onclick="goRated()">🔍 Find Opponent</button>
      </div>

      <!-- STEP 3: Friends -->
      <div id="s3-friends" class="step-card" style="display:none;">
        <div class="flex items-center gap-3 mb-5">
          <button onclick="back('s2','s3-friends')" class="btn btn-ghost btn-sm">← Back</button>
          <h2 class="heading">👥 Friends Room</h2>
        </div>
        <div class="grid-2 gap-4">
          <button class="mode-btn" onclick="createRoom()" style="min-height:110px;">
            <span class="mode-icon">➕</span>
            <span class="mode-title">Create Room</span>
            <span class="mode-sub">Host a game</span>
          </button>
          <div style="display:flex;flex-direction:column;gap:10px;justify-content:center;">
            <label class="label" for="jcode">Room Code</label>
            <input id="jcode" class="input" placeholder="123456" maxlength="6" inputmode="numeric"
              style="text-align:center;font-size:1.6rem;letter-spacing:10px;font-weight:700;font-family:var(--mono);"
              aria-label="Enter 6-digit room code"
              oninput="this.value=this.value.replace(/[^0-9]/g,'')">
            <button class="btn btn-outline w-full" onclick="joinRoom()">🚪 Join Room</button>
          </div>
        </div>
      </div>

      <!-- STEP 3: Solo -->
      <div id="s3-solo" class="step-card" style="display:none;">
        <div class="flex items-center gap-3 mb-5">
          <button onclick="back('s2','s3-solo')" class="btn btn-ghost btn-sm">← Back</button>
          <h2 class="heading">🎮 Solo Practice</h2>
        </div>
        <div class="grid-2 gap-4 mb-5">
          <div class="input-group">
            <label class="label" for="gs-s">Grid Size</label>
            <select id="gs-s" class="input">
              <option value="3">3×3</option><option value="4">4×4</option>
            </select>
          </div>
          <div class="input-group">
            <label class="label" for="df-s">Difficulty</label>
            <select id="df-s" class="input">
              <option value="easy">Easy</option>
              <option value="normal" selected>Normal</option>
              <option value="hard">Hard</option>
            </select>
          </div>
        </div>
        <button class="btn btn-primary w-full btn-lg" onclick="startSolo()">▶ Start Game</button>
      </div>

    {% endif %}
  </div>

  <!-- FEATURE CARDS -->
  <div class="grid-3 gap-4 mb-8 mt-4">
    <div class="feature-card">
      <div class="feature-icon">⚡</div>
      <h3>Rated Matches</h3>
      <p>ELO ranking system with 5 tiers from Beginner to Legend</p>
    </div>
    <div class="feature-card">
      <div class="feature-icon">📅</div>
      <h3>Daily Challenge</h3>
      <p>One shared board every day — compete for the fastest time globally</p>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🏟️</div>
      <h3>Franchise Logos</h3>
      <p>Identify all 10+ IPL franchises by their iconic badges</p>
    </div>
  </div>

  <!-- BOTTOM AD -->
  <div class="ad-slot ad-rect mb-8">
    <ins class="adsbygoogle" style="display:block;"
      data-ad-client="ca-pub-9904803540658016" data-ad-slot="auto" data-ad-format="rectangle" data-full-width-responsive="true"></ins>
    <script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>
  </div>
</div>

<script>
let selSrc = null;
function show(id){ const el=document.getElementById(id); if(el){el.style.display='';} }
function hide(id){ const el=document.getElementById(id); if(el){el.style.display='none';} }
function back(showId, hideId){ hide(hideId); show(showId); }

function pickSrc(s){
  selSrc = s;
  document.getElementById('s2-title').textContent = s === 'overall' ? '🌍 All-Time Overall' : '🏆 IPL 2026 Edition';
  hide('s1'); show('s2');
}
function pickMode(m){
  ['rated','friends','solo'].forEach(x => hide('s3-'+x));
  hide('s2'); show('s3-'+m);
}
function goRated(){
  const gs = document.getElementById('gs-r').value;
  const df = document.getElementById('df-r').value;
  window.location.href = `/matchmaking?data_source=${selSrc}&grid_size=${gs}&difficulty=${df}`;
}
function startSolo(){
  const gs = document.getElementById('gs-s').value;
  const df = document.getElementById('df-s').value;
  window.location.href = `/play?data_source=${selSrc}&grid_size=${gs}&difficulty=${df}&mode=solo`;
}
function createRoom(){
  const btn = event.currentTarget;
  btn.style.opacity = '0.6'; btn.querySelector('.mode-title').textContent = 'Creating…';
  fetch('/api/create_room',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data_source:selSrc})})
    .then(r=>r.json())
    .then(d=>{ if(d.code) window.location.href='/room/'+d.code; else { toast('Error creating room','error'); btn.style.opacity='1'; btn.querySelector('.mode-title').textContent='Create Room'; }});
}
function joinRoom(){
  const c = document.getElementById('jcode').value.trim();
  if(c.length === 6 && /^[0-9]+$/.test(c)) window.location.href = '/room/' + c;
  else toast('Enter a valid 6-digit code','warn');
}
// Allow Enter key on room code input
document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('jcode');
  if (inp) inp.addEventListener('keydown', e => { if(e.key==='Enter') joinRoom(); });
});
</script>
"""

GAME_BODY = """
<div class="container-sm page">

  <!-- STATS ROW -->
  <div class="grid-3 gap-3 mb-4">
    <div class="stat-card">
      <div class="stat-label">Score</div>
      <div class="stat-value text-green" id="sc">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Remaining</div>
      <div class="stat-value" id="pl">{{ total_players }}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Accuracy</div>
      <div class="stat-value text-blue" id="ac">—</div>
    </div>
  </div>

  <!-- TIMER -->
  <div class="timer-wrap mb-2" role="progressbar" aria-label="Time remaining" aria-valuenow="100">
    <div id="tb" class="timer-bar" style="width:100%;background:var(--acc);"></div>
  </div>
  <div class="flex justify-between mb-4" style="font-size:.8rem;color:var(--txt3);">
    <span id="tt" style="font-family:var(--mono);font-weight:700;color:var(--txt2);">30s</span>
    <span style="color:var(--txt3);">{{ mode_label }}</span>
  </div>

  <!-- PLAYER CARD -->
  <div class="player-card mb-4" id="pcard" aria-live="polite">
    <div id="ps" class="player-hint mb-2" style="font-size:.78rem;color:var(--txt3);">Loading game…</div>
    <div id="pn" class="player-name">
      <div class="spinner" style="width:28px;height:28px;margin:0 auto;"></div>
    </div>
    <div id="px" style="font-size:.75rem;color:var(--txt3);margin-top:8px;"></div>
  </div>

  <!-- BINGO GRID -->
  <div class="bingo-grid size-{{ grid_size }}" id="grid" role="grid" aria-label="Bingo grid">
    {% for cell in grid %}
    <div class="cell {{ cell.type }}-cell" id="c{{ loop.index0 }}"
      onclick="clickCell({{ loop.index0 }})"
      role="gridcell" tabindex="0"
      aria-label="{{ cell.value }}"
      onkeydown="if(event.key==='Enter'||event.key===' ')clickCell({{ loop.index0 }})">
      {% if cell.type == 'team' and cell.logo %}
        <img class="cell-logo" src="/public/{{ cell.logo }}" alt="{{ cell.value }}"
          onerror="this.style.display='none';this.nextElementSibling.style.display='block'">
        <span class="cell-label" style="display:none;">{{ cell.value }}</span>
      {% elif cell.type == 'nation' %}
        <span style="font-size:1.5rem;">
          {% set flag_map = {'India': '🇮🇳', 'Australia': '🇦🇺', 'England': '🏴󠁧󠁢󠁥󠁮󠁧󠁿', 'South Africa': '🇿🇦', 'New Zealand': '🇳🇿', 'Pakistan': '🇵🇰', 'West Indies': '🏴', 'Sri Lanka': '🇱🇰', 'Bangladesh': '🇧🇩', 'Afghanistan': '🇦🇫', 'Zimbabwe': '🇿🇼'} %}
          {{ flag_map.get(cell.value, '🌍') }}
        </span>
        <span class="cell-label" style="font-size:.78rem;color:var(--txt);font-weight:700;">{{ cell.value }}</span>
      {% elif cell.type == 'trophy' %}
        <span style="font-size:1.4rem;">🏆</span>
        <span class="cell-label" style="font-size:.72rem;color:var(--amber);font-weight:700;">{{ cell.value }}</span>
      {% else %}
        <span style="font-size:.8rem;">🔗</span>
        <span class="cell-label" style="font-size:.62rem;color:var(--pur);font-weight:700;line-height:1.3;">{{ cell.value }}</span>
      {% endif %}
    </div>
    {% endfor %}
  </div>

  <!-- ACTION BUTTONS -->
  <div class="flex gap-3 mt-5 justify-center flex-wrap">
    <button id="skip-btn" class="btn btn-secondary" onclick="doSkip()" title="Skip this player">
      ⏭ Skip <span id="skip-count" style="background:var(--sur3);padding:2px 8px;border-radius:99px;font-size:.75rem;margin-left:2px;">3</span>
    </button>
    <button id="wc-btn" class="btn btn-secondary" style="color:var(--amber);border-color:rgba(245,158,11,.3);" onclick="doWildcard()" title="Highlight matching cells">
      🃏 Wildcard
    </button>
    <button class="btn btn-ghost text-subtle btn-sm" onclick="quitGame()" style="font-size:.8rem;">🏳 Quit</button>
  </div>

  {% if opponent %}
  <div class="card mt-5" style="padding:16px;">
    <div class="flex justify-between items-center mb-2">
      <span style="font-size:.875rem;color:var(--txt2);">vs <strong style="color:var(--txt);">{{ opponent }}</strong></span>
      <span style="font-size:.875rem;font-weight:700;">Score: <strong id="os" class="text-green">0</strong></span>
    </div>
    <div class="progress-wrap" style="height:5px;">
      <div id="ob" class="progress-bar" style="width:0%;background:var(--red);"></div>
    </div>
  </div>
  {% endif %}

  <!-- MID-GAME AD -->
  <div class="ad-slot mt-5">
    <ins class="adsbygoogle" style="display:block;width:100%;height:90px;"
      data-ad-client="ca-pub-9904803540658016" data-ad-slot="auto" data-ad-format="horizontal" data-full-width-responsive="true"></ins>
    <script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>
  </div>
</div>

<!-- END MODAL -->
<div id="emod" class="modal-overlay" style="display:none;" role="dialog" aria-modal="true" aria-label="Game over">
  <div class="modal text-center">
    <div style="font-size:4rem;margin-bottom:12px;line-height:1;" id="ee" aria-hidden="true">🎯</div>
    <h2 class="title mb-2" id="et">Game Over</h2>
    <div class="score-display mt-3 mb-3" id="es">0</div>
    <p class="text-muted mb-2" id="ed" style="font-size:.875rem;"></p>
    <div id="er" style="font-size:1rem;font-weight:700;margin-bottom:24px;min-height:1.5em;"></div>
    <div class="grid-2 gap-3">
      <a href="/" class="btn btn-outline w-full">🏠 Home</a>
      <button class="btn btn-primary w-full" onclick="location.href='/'">🔄 Play Again</button>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const G = {
  room:    {{ room_code | tojson }},
  mode:    {{ game_mode | tojson }},
  ds:      {{ data_source | tojson }},
  gs:      {{ grid_size }},
  players: {{ players_json }},
  idx:     0,
  gstate:  new Array({{ grid_size * grid_size }}).fill(null),
  correct: 0, wrong: 0, skips: 3, wcUsed: false,
  t0:      Date.now(), tsec: 30, tleft: 30, tint: null,
  ended:   false, clickable: false
};

console.log('[CricketBingo] Players loaded:', G.players ? G.players.length : 'NONE');

if (!G.players || G.players.length === 0) {
  document.getElementById('pn').innerHTML = '<span style="color:var(--red);font-size:.95rem;">⚠ No players found. Check JSON files.</span>';
  document.getElementById('ps').textContent = 'Error: ' + G.ds + '.json may be missing or empty';
}

const sock = io();
if (G.room) {
  sock.emit('join_room', { room: G.room });
  sock.on('opponent_move', d => updOpp(d.score));
}

function calcScore() {
  const el  = (Date.now() - G.t0) / 1000;
  const a   = G.correct + G.wrong;
  const acc = a > 0 ? G.correct / a * 100 : 0;
  const filled = G.gstate.every(x => x !== null);
  return Math.max(0, Math.round(G.correct * 100 + acc * 2 + (filled ? 200 : 0) - Math.max(0, (el - G.gs * G.gs * 15) * 0.5)));
}
function refresh() {
  document.getElementById('pl').textContent = Math.max(0, G.players.length - G.idx);
  document.getElementById('sc').textContent = calcScore();
  const a = G.correct + G.wrong;
  document.getElementById('ac').textContent = a > 0 ? Math.round(G.correct / a * 100) + '%' : '—';
}

function showP() {
  if (!G.players || G.players.length === 0) return;
  if (G.idx >= G.players.length) { end('no_more_players'); return; }
  const p = G.players[G.idx];
  const name = p.name || p.player_name || ('Player ' + (G.idx + 1));
  document.getElementById('pn').textContent = name;
  document.getElementById('ps').textContent = 'Player ' + (G.idx + 1) + ' of ' + G.players.length;
  // Show nation/team hint
  const hints = [];
  if (p.nation) hints.push(p.nation);
  if (p.iplTeams && p.iplTeams.length > 0) hints.push(p.iplTeams.slice(-1)[0]);
  document.getElementById('px').textContent = '';
  refresh(); startTimer();
}

function startTimer() {
  clearInterval(G.tint);
  G.tleft = G.tsec; G.clickable = true;
  tickTimer();
  G.tint = setInterval(() => {
    G.tleft--;
    tickTimer();
    if (G.tleft <= 0) { clearInterval(G.tint); timeUp(); }
  }, 1000);
}
function tickTimer() {
  const pct = G.tleft / G.tsec * 100;
  const bar = document.getElementById('tb');
  bar.style.width = pct + '%';
  bar.style.background = pct > 50 ? 'var(--acc)' : pct > 25 ? 'var(--amber)' : 'var(--red)';
  document.getElementById('tt').textContent = G.tleft + 's';
}
function timeUp() {
  G.wrong++; G.idx++;
  toast('⏰ Time\'s up!', 'warn');
  setTimeout(showP, 300);
}

function clickCell(i) {
  if (!G.clickable || G.ended || G.gstate[i] !== null || G.idx >= G.players.length) return;
  G.clickable = false;
  clearInterval(G.tint);
  const p = G.players[G.idx];
  const pid = p.id || p.player_id || ('player_' + G.idx);

  fetch('/api/validate_move', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ player_id: pid, cell_idx: i, data_source: G.ds, room_code: G.room, mode: G.mode })
  })
  .then(r => r.json())
  .then(res => {
    const el = document.getElementById('c' + i);
    if (res.correct) {
      G.correct++;
      G.gstate[i] = p.name || p.player_name || 'Player';
      el.classList.add('filled');
      const tag = document.createElement('div');
      tag.className = 'cell-fill-name';
      tag.textContent = G.gstate[i];
      el.appendChild(tag);
      toast('✅ Correct!', 'success');
    } else {
      G.wrong++;
      el.classList.add('wrong');
      setTimeout(() => el.classList.remove('wrong'), 500);
      const reason = res.reason === 'player_not_found' ? '❓ Player not found' : '❌ Wrong!';
      toast(reason, 'error');
    }
    G.idx++;
    if (G.room) sock.emit('player_move', { room: G.room, score: calcScore() });
    refresh();
    if (G.gstate.every(x => x !== null)) { end('grid_complete'); return; }
    setTimeout(showP, 420);
  })
  .catch(() => { G.clickable = true; startTimer(); toast('Connection error', 'error'); });
}

function updOpp(s) {
  const e = document.getElementById('os'); if(e) e.textContent = s;
  const b = document.getElementById('ob'); if(b) b.style.width = Math.min(100, s/2000*100) + '%';
}

function doSkip() {
  if (G.skips <= 0 || G.ended) return;
  G.skips--; G.wrong++; G.idx++;
  clearInterval(G.tint);
  document.getElementById('skip-count').textContent = G.skips;
  if (G.skips === 0) document.getElementById('skip-btn').disabled = true;
  toast(`⏭ Skipped (${G.skips} left)`, 'info');
  setTimeout(showP, 200);
}
function doWildcard() {
  if (G.wcUsed || G.ended || G.idx >= G.players.length) return;
  G.wcUsed = true;
  const btn = document.getElementById('wc-btn');
  btn.disabled = true; btn.textContent = '🃏 Used';
  const p = G.players[G.idx];
  const pid = p.id || p.player_id || ('player_' + G.idx);
  fetch('/api/wildcard_hint', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ player_id: pid, data_source: G.ds, room_code: G.room })
  })
  .then(r => r.json())
  .then(d => {
    if (d.matching_cells && d.matching_cells.length > 0) {
      d.matching_cells.forEach(i => { if(G.gstate[i]===null) document.getElementById('c'+i).classList.add('hint'); });
      toast(`🃏 ${d.matching_cells.length} matching cell(s) highlighted!`, 'info');
    } else {
      toast('🃏 No matching cells found', 'warn');
    }
  });
}
function quitGame() {
  if (confirm('Quit this game? Progress will be lost.')) end('quit');
}

function end(reason) {
  if (G.ended) return;
  G.ended = true; clearInterval(G.tint);
  const elapsed = Math.round((Date.now() - G.t0) / 1000);
  const score   = calcScore();
  const a       = G.correct + G.wrong;
  const acc     = a > 0 ? Math.round(G.correct / a * 100) : 0;

  fetch('/api/end_game', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ room_code: G.room, mode: G.mode, data_source: G.ds,
      score, correct: G.correct, wrong: G.wrong, elapsed, accuracy: acc, reason })
  })
  .then(r => r.json())
  .then(d => {
    const done = G.gstate.every(x => x !== null);
    document.getElementById('ee').textContent = done ? '🏆' : (G.correct > G.wrong ? '🎯' : '😅');
    document.getElementById('et').textContent = done ? 'Grid Complete!' : (reason === 'quit' ? 'Game Quit' : 'Game Over');
    document.getElementById('es').textContent = score;
    document.getElementById('ed').textContent = `Accuracy: ${acc}%  ·  Time: ${elapsed}s  ·  ${G.correct}/${a} correct`;
    if (d.rating_change && d.rating_change !== 0) {
      const rc = Math.round(d.rating_change);
      document.getElementById('er').innerHTML = `<span style="color:${rc>0?'var(--acc)':'var(--red)'};font-size:1.1rem;">${rc>0?'+':''}${rc} Rating</span>`;
    }
    document.getElementById('emod').style.display = 'flex';
  });
}

document.addEventListener('DOMContentLoaded', () => {
  if (G.players && G.players.length > 0) showP();
  else {
    document.getElementById('pn').innerHTML = '<span style="color:var(--red);">No players loaded!</span>';
    document.getElementById('ps').textContent = 'Ensure overall.json / ipl26.json exist in project root';
  }
});
</script>
"""

MATCHMAKING_BODY = """
<div class="container page">
  <div class="mm-card">
    <div class="mm-dots"><span></span><span></span><span></span></div>
    <h2 class="title mb-3">Finding Opponent…</h2>
    <p class="text-muted mb-6" id="smsg" style="font-size:.9rem;">Searching for players with similar rating…</p>
    <div class="progress-wrap mb-4" style="height:5px;">
      <div id="sbar" class="progress-bar" style="width:0%;transition:width 30s linear;background:var(--grd-acc);"></div>
    </div>
    <p class="text-subtle mb-8" id="etxt" style="font-size:.8rem;font-family:var(--mono);">0s elapsed</p>
    <button class="btn btn-outline" onclick="cancel()">Cancel</button>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const sock = io();
const ds={{ data_source|tojson }}, gs={{ grid_size }}, diff={{ difficulty|tojson }};
let el=0;
sock.emit('join_matchmaking',{data_source:ds,grid_size:gs,difficulty:diff});
sock.on('match_found',d=>window.location.href='/room/'+d.room_code);
sock.on('matchmaking_status',d=>document.getElementById('smsg').textContent=d.message);
setTimeout(()=>document.getElementById('sbar').style.width='100%',100);
const t=setInterval(()=>{el++;document.getElementById('etxt').textContent=el+'s elapsed';},1000);
setTimeout(()=>{
  clearInterval(t);
  document.getElementById('smsg').textContent='No opponent found — starting solo game…';
  setTimeout(()=>window.location.href=`/play?data_source=${ds}&grid_size=${gs}&difficulty=${diff}&mode=solo`,1800);
},30000);
function cancel(){sock.emit('leave_matchmaking');window.location.href='/';}
</script>
"""

ROOM_BODY = """
<div class="container page">
  <div class="card card-glow" style="max-width:520px;margin:0 auto;text-align:center;padding:40px;">
    <div style="font-size:2.5rem;margin-bottom:12px;">👥</div>
    <h2 class="title mb-2">Friends Room</h2>
    <p class="text-muted mb-6" style="font-size:.875rem;">Share this code with your friend to start playing</p>

    <div class="room-code-display mb-2" id="rcdisp" title="Click to copy" role="button" tabindex="0"
      onkeydown="if(event.key==='Enter')copyCode()" onclick="copyCode()">{{ room_code }}</div>
    <p class="text-subtle mb-6" style="font-size:.76rem;">Click to copy · Code expires when game starts</p>

    <div id="plist" class="flex gap-3 justify-center mb-6 flex-wrap" aria-live="polite"></div>

    <div id="wmsg" class="text-muted pulse" style="font-size:.9rem;">⏳ Waiting for friend to join…</div>

    <div id="ssec" style="display:none;">
      {% if is_host %}
      <hr style="margin:24px 0;">
      <div class="grid-2 gap-3 mb-4">
        <div class="input-group">
          <label class="label" for="rgs">Grid Size</label>
          <select id="rgs" class="input"><option value="3">3×3</option><option value="4">4×4</option></select>
        </div>
        <div class="input-group">
          <label class="label" for="rdf">Difficulty</label>
          <select id="rdf" class="input"><option value="easy">Easy</option><option value="normal" selected>Normal</option><option value="hard">Hard</option></select>
        </div>
      </div>
      <button class="btn btn-primary w-full btn-lg" onclick="startR()">▶ Start Game</button>
      {% else %}
      <div style="padding:16px;background:var(--acc-subtle);border-radius:var(--r-lg);border:1px solid rgba(34,197,94,.2);">
        <p class="text-green" style="font-weight:700;">✅ Connected! Waiting for host to start…</p>
      </div>
      {% endif %}
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const sock=io(), room={{ room_code|tojson }}, isHost={{ 'true' if is_host else 'false' }}, ds={{ data_source|tojson }};
sock.emit('join_room',{room});
sock.on('room_update',d=>{
  document.getElementById('plist').innerHTML=d.players.map(p=>`<span class="badge" style="color:var(--acc);border-color:var(--acc);padding:8px 16px;font-size:.82rem;">👤 ${p}</span>`).join('');
  if(d.players.length>=2){ document.getElementById('wmsg').style.display='none'; document.getElementById('ssec').style.display=''; }
});
sock.on('game_start',d=>window.location.href='/play?room_code='+d.room_code+'&mode=friends');
function startR(){
  const gs=document.getElementById('rgs').value, df=document.getElementById('rdf').value;
  sock.emit('start_room_game',{room,data_source:ds,grid_size:parseInt(gs),difficulty:df});
}
function copyCode(){
  navigator.clipboard.writeText({{ room_code|tojson }})
    .then(()=>toast('Room code copied!','success'))
    .catch(()=>toast('Copy: ' + {{ room_code|tojson }},'info'));
}
</script>
"""

LEADERBOARD_BODY = """
<div class="container page">
  <div class="flex justify-between items-center mb-6 flex-wrap gap-4">
    <div>
      <h1 class="title grad-green">🏆 Leaderboard</h1>
      <p class="text-muted mt-2" style="font-size:.875rem;">{{ season.name }} · Ends {{ season.end_date }}</p>
    </div>
    <a href="/daily" class="btn btn-outline btn-sm">📅 Daily Challenge</a>
  </div>

  <div class="ad-slot mb-5">
    <ins class="adsbygoogle" style="display:block;width:100%;height:90px;"
      data-ad-client="ca-pub-9904803540658016" data-ad-slot="auto" data-ad-format="horizontal" data-full-width-responsive="true"></ins>
    <script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr><th style="width:52px;">#</th><th>Player</th><th>Tier</th><th>Rating</th><th>W / L</th><th class="hide-sm">Win %</th></tr>
      </thead>
      <tbody>
        {% for r in rows %}
        <tr>
          <td>
            {% if loop.index == 1 %}<span style="font-size:1.2rem;">🥇</span>
            {% elif loop.index == 2 %}<span style="font-size:1.2rem;">🥈</span>
            {% elif loop.index == 3 %}<span style="font-size:1.2rem;">🥉</span>
            {% else %}<span class="text-subtle" style="font-family:var(--mono);font-size:.85rem;">{{ loop.index }}</span>{% endif %}
          </td>
          <td>
            <a href="/profile/{{ r.user_id }}" style="font-weight:700;color:var(--txt);text-decoration:none;transition:color .15s;">{{ r.name }}</a>
            {% if loop.index <= 3 %} <span style="font-size:.8rem;">⭐</span>{% endif %}
          </td>
          <td><span class="badge" style="color:{{ r.tier_color }};">{{ r.tier_icon }} {{ r.tier }}</span></td>
          <td class="text-green" style="font-weight:700;font-family:var(--mono);">{{ r.rating|int }}</td>
          <td><span class="text-green" style="font-weight:600;">{{ r.wins }}</span> <span class="text-subtle">/</span> <span class="text-red" style="font-weight:600;">{{ r.losses }}</span></td>
          <td class="hide-sm text-muted" style="font-family:var(--mono);">{{ r.win_rate }}%</td>
        </tr>
        {% endfor %}
        {% if not rows %}
        <tr><td colspan="6" style="text-align:center;padding:64px;color:var(--txt3);">
          No ranked players yet — be the first! 🚀</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</div>
"""

PROFILE_BODY = """
<div class="container page">

  <!-- Profile Header -->
  <div class="card card-gradient-border mb-6">
    <div class="card-inner">
      <div class="flex items-center gap-5 flex-wrap">
        <img src="{{ profile_user.avatar or '' }}"
          style="width:80px;height:80px;border-radius:50%;border:3px solid rgba(34,197,94,.4);object-fit:cover;flex-shrink:0;box-shadow:0 0 24px var(--acc-glow);"
          onerror="this.src='https://ui-avatars.com/api/?name={{ profile_user.name|urlencode }}&background=22C55E&color=fff&size=80'"
          alt="{{ profile_user.name }}">
        <div>
          <h1 class="title">{{ profile_user.name }}</h1>
          <div class="flex items-center gap-2 mt-2 flex-wrap">
            <span class="badge" style="color:{{ tier_color }};font-size:.78rem;padding:4px 12px;">{{ tier_icon }} {{ tier }}</span>
            <span class="text-muted" style="font-size:.875rem;font-family:var(--mono);">{{ rating|int }} elo</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Stats Grid -->
  <div class="grid-3 gap-4 mb-6">
    <div class="stat-card"><div class="stat-label">Games Played</div><div class="stat-value text-green">{{ stats.total_games }}</div></div>
    <div class="stat-card"><div class="stat-label">W / L</div><div class="stat-value" style="font-size:1.6rem;letter-spacing:-1px;"><span class="text-green">{{ stats.wins }}</span> <span class="text-subtle">/</span> <span class="text-red">{{ stats.losses }}</span></div></div>
    <div class="stat-card"><div class="stat-label">Win Rate</div><div class="stat-value text-blue">{{ stats.win_rate }}<span style="font-size:1.2rem;">%</span></div></div>
    <div class="stat-card"><div class="stat-label">Avg Accuracy</div><div class="stat-value text-amber">{{ stats.avg_accuracy }}<span style="font-size:1.2rem;">%</span></div></div>
    <div class="stat-card"><div class="stat-label">Best Streak</div><div class="stat-value text-pur">{{ stats.best_streak }}</div></div>
    <div class="stat-card"><div class="stat-label">Avg Time</div><div class="stat-value text-muted" style="font-size:1.5rem;">{{ stats.avg_time }}<span style="font-size:.9rem;">s</span></div></div>
  </div>

  <!-- Recent Matches -->
  <div class="card">
    <h2 class="heading mb-4">Recent Matches</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Result</th><th>Score</th><th class="hide-sm">Opponent</th><th class="hide-sm">Δ Rating</th><th>Mode</th><th class="hide-sm">Date</th></tr></thead>
        <tbody>
          {% for m in matches %}
          <tr>
            <td>{% if m.won %}<span class="text-green" style="font-weight:800;font-size:.85rem;">WIN</span>
                {% elif m.won == False %}<span class="text-red" style="font-weight:800;font-size:.85rem;">LOSS</span>
                {% else %}<span class="text-subtle" style="font-size:.85rem;">—</span>{% endif %}</td>
            <td style="font-weight:700;font-family:var(--mono);">{{ m.score|int }}</td>
            <td class="hide-sm text-muted">{{ m.opponent or '—' }}</td>
            <td class="hide-sm" style="font-family:var(--mono);">
              {% if m.rating_change > 0 %}<span class="text-green">+{{ m.rating_change|int }}</span>
              {% elif m.rating_change < 0 %}<span class="text-red">{{ m.rating_change|int }}</span>
              {% else %}<span class="text-subtle">—</span>{% endif %}
            </td>
            <td><span class="badge text-subtle" style="font-size:.66rem;border-color:var(--bdr2);">{{ m.mode }}</span></td>
            <td class="hide-sm text-subtle" style="font-size:.78rem;font-family:var(--mono);">{{ m.played_at[:10] }}</td>
          </tr>
          {% endfor %}
          {% if not matches %}<tr><td colspan="6" style="text-align:center;padding:48px;color:var(--txt3);">No matches yet — play your first game!</td></tr>{% endif %}
        </tbody>
      </table>
    </div>
  </div>
</div>
"""

DAILY_BODY = """
<div class="container page">
  <div class="flex justify-between items-center mb-6 flex-wrap gap-3">
    <div>
      <h1 class="title grad-green">📅 Daily Challenge</h1>
      <p class="text-muted mt-2" style="font-size:.875rem;">{{ today }} · Same board for everyone worldwide</p>
    </div>
    {% if not already_played %}
      <a href="/play?mode=daily&data_source=overall&grid_size=3&difficulty=normal" class="btn btn-primary btn-lg">▶ Play Today's Board</a>
    {% else %}
      <span class="badge text-green" style="padding:10px 20px;font-size:.85rem;background:var(--acc-subtle);">✅ Completed Today</span>
    {% endif %}
  </div>

  <div class="card">
    <h2 class="heading mb-4">Today's Rankings</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Player</th><th>Score</th><th>Accuracy</th><th>Time</th></tr></thead>
        <tbody>
          {% for r in rows %}
          <tr>
            <td>{% if loop.index==1 %}<span style="font-size:1.2rem;">🥇</span>{% elif loop.index==2 %}<span style="font-size:1.2rem;">🥈</span>{% elif loop.index==3 %}<span style="font-size:1.2rem;">🥉</span>{% else %}<span class="text-subtle" style="font-family:var(--mono);font-size:.85rem;">{{ loop.index }}</span>{% endif %}</td>
            <td><a href="/profile/{{ r.user_id }}" style="font-weight:700;color:var(--txt);text-decoration:none;">{{ r.name }}</a></td>
            <td class="text-green" style="font-weight:700;font-family:var(--mono);">{{ r.score|int }}</td>
            <td style="font-family:var(--mono);">{{ r.accuracy|int }}%</td>
            <td class="text-muted" style="font-family:var(--mono);">{{ r.completion_time|int }}s</td>
          </tr>
          {% endfor %}
          {% if not rows %}<tr><td colspan="5" style="text-align:center;padding:56px;color:var(--txt3);">Be the first to play today! 🚀</td></tr>{% endif %}
        </tbody>
      </table>
    </div>
  </div>
</div>
"""

ABOUT_BODY = """
<div class="container-sm page">
  <h1 class="title grad-green mb-2">About Cricket Bingo</h1>
  <p class="text-muted mb-8" style="font-size:.95rem;">The IPL cricket quiz that tests your knowledge of teams, nations &amp; trophies.</p>

  <div class="card mb-4">
    <h2 class="heading mb-3">What is Cricket Bingo?</h2>
    <p style="line-height:1.9;color:var(--txt2);margin-bottom:12px;">
      Cricket Bingo is a free browser-based cricket quiz where you match famous cricketers to
      their IPL franchises, nationalities, and trophies on a bingo-style grid.
    </p>
    <p style="line-height:1.9;color:var(--txt2);">
      Players are shown one by one — tap the correct matching cell before the 30-second timer
      runs out. Score points for accuracy and speed. Compete online in rated matches or challenge friends.
    </p>
  </div>

  <div class="grid-2 gap-4 mb-4">
    <div class="card">
      <h3 class="heading mb-3">🎮 Game Modes</h3>
      <div style="color:var(--txt2);display:flex;flex-direction:column;gap:10px;margin-top:4px;">
        <div style="display:flex;align-items:center;gap:10px;padding:10px;background:var(--sur2);border-radius:var(--r-md);"><span style="font-size:1.1rem;">⚡</span><div><div style="font-weight:700;color:var(--txt);font-size:.9rem;">Rated Matches</div><div style="font-size:.78rem;">ELO competitive ranking</div></div></div>
        <div style="display:flex;align-items:center;gap:10px;padding:10px;background:var(--sur2);border-radius:var(--r-md);"><span style="font-size:1.1rem;">👥</span><div><div style="font-weight:700;color:var(--txt);font-size:.9rem;">Friends Rooms</div><div style="font-size:.78rem;">Play with a room code</div></div></div>
        <div style="display:flex;align-items:center;gap:10px;padding:10px;background:var(--sur2);border-radius:var(--r-md);"><span style="font-size:1.1rem;">🎯</span><div><div style="font-weight:700;color:var(--txt);font-size:.9rem;">Solo Practice</div><div style="font-size:.78rem;">Sharpen your cricket IQ</div></div></div>
        <div style="display:flex;align-items:center;gap:10px;padding:10px;background:var(--sur2);border-radius:var(--r-md);"><span style="font-size:1.1rem;">📅</span><div><div style="font-weight:700;color:var(--txt);font-size:.9rem;">Daily Challenge</div><div style="font-size:.78rem;">One shared global board</div></div></div>
      </div>
    </div>
    <div class="card">
      <h3 class="heading mb-3">📊 Rating Tiers</h3>
      <div style="display:flex;flex-direction:column;gap:8px;margin-top:4px;">
        {% for icon, name, range, color in [('🟤','Beginner','&lt; 1000','#9CA3AF'),('🔵','Amateur','1000–1199','#60A5FA'),('🟢','Pro','1200–1399','#34D399'),('🟡','Elite','1400–1599','#FBBF24'),('🔴','Legend','1600+','#F87171')] %}
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:var(--sur2);border-radius:var(--r-md);border-left:3px solid {{ color }};">
          <span style="font-weight:700;color:var(--txt);">{{ icon }} {{ name }}</span>
          <span style="font-family:var(--mono);font-size:.8rem;color:{{ color }};">{{ range|safe }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <div class="card">
    <h2 class="heading mb-3">📬 Get in Touch</h2>
    <p style="line-height:1.85;color:var(--txt2);">
      Found a bug or have a suggestion? Visit the
      <a href="/contact" style="color:var(--acc);text-decoration:none;font-weight:600;">Contact page</a> or email us at
      <a href="mailto:tehm8111@gmail.com" style="color:var(--acc);text-decoration:none;font-weight:600;">tehm8111@gmail.com</a>
    </p>
  </div>
</div>
"""

CONTACT_BODY = """
<div class="container-sm page">
  <h1 class="title grad-green mb-2">Contact Us</h1>
  <p class="text-muted mb-8" style="font-size:.95rem;">We read every message and aim to respond within 24 hours.</p>

  <div class="card mb-4" id="form-wrap">
    <div id="contact-form">
      <div class="grid-2 gap-4 mb-4">
        <div class="input-group">
          <label class="label" for="fname">Your Name *</label>
          <input type="text" id="fname" class="input" placeholder="Virat Kohli" maxlength="100" autocomplete="name">
          <span id="err-name" style="display:none;font-size:.76rem;color:var(--red);margin-top:5px;"></span>
        </div>
        <div class="input-group">
          <label class="label" for="femail">Email Address *</label>
          <input type="email" id="femail" class="input" placeholder="you@example.com" autocomplete="email">
          <span id="err-email" style="display:none;font-size:.76rem;color:var(--red);margin-top:5px;"></span>
        </div>
      </div>
      <div class="input-group mb-4">
        <label class="label" for="fsubject">Subject *</label>
        <select id="fsubject" class="input">
          <option value="">Select a topic…</option>
          <option>Bug Report</option>
          <option>Feature Request</option>
          <option>Player / Data Error</option>
          <option>General Feedback</option>
          <option>Partnership / Collaboration</option>
          <option>Other</option>
        </select>
      </div>
      <div class="input-group mb-4">
        <label class="label" for="fmsg">Message *</label>
        <textarea id="fmsg" class="input" placeholder="Tell us what's on your mind…"
          minlength="10" maxlength="2000"
          style="min-height:150px;resize:vertical;line-height:1.7;"></textarea>
        <div class="flex justify-between mt-1">
          <span style="font-size:.72rem;color:var(--txt3);">Minimum 10 characters</span>
          <span id="char-count" style="font-size:.72rem;color:var(--txt3);">0 / 2000</span>
        </div>
      </div>
      <div id="form-error" style="display:none;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);
        border-radius:var(--r-lg);padding:14px;margin-bottom:16px;font-size:.875rem;color:var(--red);"></div>
      <button id="fsub" class="btn btn-primary w-full btn-lg" onclick="submitContact()">
        📨 Send Message
      </button>
    </div>

    <div id="form-success" style="display:none;text-align:center;padding:24px 0;">
      <div style="width:64px;height:64px;background:var(--acc-subtle);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:2rem;margin:0 auto 16px;">✅</div>
      <h3 class="heading mb-2">Message Sent!</h3>
      <p class="text-muted">Thanks for reaching out — we'll reply to your email shortly.</p>
    </div>
  </div>

  <div class="card">
    <h3 class="heading mb-4">Other ways to reach us</h3>
    <div class="grid-2 gap-3">
      <div style="display:flex;align-items:center;gap:14px;padding:16px;background:var(--sur2);border-radius:var(--r-lg);">
        <div style="width:40px;height:40px;background:var(--acc-subtle);border-radius:var(--r-md);display:flex;align-items:center;justify-content:center;font-size:1.2rem;flex-shrink:0;">📧</div>
        <div>
          <div style="font-weight:700;font-size:.85rem;margin-bottom:3px;">Email Direct</div>
          <a href="mailto:tehm8111@gmail.com" style="color:var(--acc);font-size:.82rem;text-decoration:none;">tehm8111@gmail.com</a>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:14px;padding:16px;background:var(--sur2);border-radius:var(--r-lg);">
        <div style="width:40px;height:40px;background:rgba(59,130,246,.1);border-radius:var(--r-md);display:flex;align-items:center;justify-content:center;font-size:1.2rem;flex-shrink:0;">⏱️</div>
        <div>
          <div style="font-weight:700;font-size:.85rem;margin-bottom:3px;">Response Time</div>
          <div style="color:var(--txt2);font-size:.82rem;">Usually 24–48 hours</div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const msgArea = document.getElementById('fmsg');
if (msgArea) msgArea.addEventListener('input', function(){
  document.getElementById('char-count').textContent = this.value.length + ' / 2000';
});

function showErr(id,msg){ const el=document.getElementById(id); el.textContent=msg; el.style.display='block'; }
function hideErr(id){ const el=document.getElementById(id); if(el) el.style.display='none'; }

function submitContact(){
  const name=document.getElementById('fname').value.trim();
  const email=document.getElementById('femail').value.trim();
  const subject=document.getElementById('fsubject').value;
  const msg=document.getElementById('fmsg').value.trim();
  let valid=true;
  hideErr('err-name'); hideErr('err-email');
  document.getElementById('form-error').style.display='none';
  if(!name||name.length<2){ showErr('err-name','Please enter your name (min 2 chars)'); valid=false; }
  if(!email||!email.includes('@')||!email.includes('.')){ showErr('err-email','Please enter a valid email address'); valid=false; }
  if(!subject){ toast('Please select a subject','warn'); valid=false; }
  if(!msg||msg.length<10){ toast('Message must be at least 10 characters','warn'); valid=false; }
  if(!valid) return;
  const btn=document.getElementById('fsub');
  btn.disabled=true; btn.textContent='Sending…';
  fetch('/api/contact',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,email,subject,message:msg})
  })
  .then(r=>r.json())
  .then(d=>{
    if(d.success){
      document.getElementById('contact-form').style.display='none';
      document.getElementById('form-success').style.display='block';
    } else {
      document.getElementById('form-error').textContent=d.error||'Failed to send. Please email us directly.';
      document.getElementById('form-error').style.display='block';
      btn.disabled=false; btn.textContent='📨 Send Message';
    }
  })
  .catch(()=>{
    const body=encodeURIComponent(`Name: ${name}\nEmail: ${email}\n\n${msg}`);
    window.location.href=`mailto:tehm8111@gmail.com?subject=${encodeURIComponent('[Cricket Bingo] '+subject)}&body=${body}`;
  });
}
</script>
"""

PRIVACY_BODY = """
<div class="container-sm page">
  <h1 class="title grad-green mb-2">Privacy Policy</h1>
  <p class="text-muted mb-8">Last updated: June 2025</p>
  {% for title, content in sections %}
  <div class="card mb-4">
    <h2 class="heading mb-3">{{ title }}</h2>
    <div style="line-height:1.9;color:var(--txt2);font-size:.9rem;">{{ content | safe }}</div>
  </div>
  {% endfor %}
</div>
"""

TERMS_BODY = """
<div class="container-sm page">
  <h1 class="title grad-green mb-2">Terms &amp; Conditions</h1>
  <p class="text-muted mb-8">Last updated: June 2025</p>
  {% for title, content in sections %}
  <div class="card mb-4">
    <h2 class="heading mb-3">{{ title }}</h2>
    <div style="line-height:1.9;color:var(--txt2);font-size:.9rem;">{{ content | safe }}</div>
  </div>
  {% endfor %}
</div>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return render_template_string(page(HOME_BODY, "Home"))

@app.route("/about")
def about():
    return render_template_string(page(ABOUT_BODY, "About Us"))

@app.route("/contact")
def contact():
    return render_template_string(page(CONTACT_BODY, "Contact Us"))

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
        ("3. Google AdSense & Advertising",
         "Cricket Bingo uses <strong style='color:var(--txt)'>Google AdSense</strong> to display advertisements. "
         "Google may use cookies to serve personalised ads. You may opt out at "
         "<a href='https://www.google.com/settings/ads' target='_blank' style='color:var(--acc);'>Google Ad Settings</a>."),
        ("4. Google Analytics",
         "We use <strong style='color:var(--txt)'>Google Analytics</strong> (GA4) to understand how visitors use the site. "
         "This collects anonymised usage data. You may opt out via "
         "<a href='https://tools.google.com/dlpage/gaoptout' target='_blank' style='color:var(--acc);'>Google Analytics Opt-out</a>."),
        ("5. Cookies",
         "We use session cookies to keep you logged in. Google AdSense and Analytics use cookies for ad personalisation "
         "and usage tracking. You can control cookie settings through your browser."),
        ("6. Data Sharing",
         "We do <strong style='color:var(--txt)'>not sell</strong> your personal data. "
         "Data is only shared with Google for authentication, advertising, and analytics."),
        ("7. Data Deletion",
         "To request deletion of your account and data, email <a href='mailto:tehm8111@gmail.com' style='color:var(--acc);'>tehm8111@gmail.com</a>."),
        ("8. Children's Privacy",
         "Cricket Bingo is not directed at children under 13. We do not knowingly collect data from children under 13."),
        ("9. Contact",
         "For privacy questions: <a href='mailto:tehm8111@gmail.com' style='color:var(--acc);'>tehm8111@gmail.com</a>"),
    ]
    return render_template_string(page(PRIVACY_BODY, "Privacy Policy"), sections=sections)

@app.route("/terms")
def terms():
    sections = [
        ("1. Acceptance", "By using Cricket Bingo, you agree to these Terms. If you disagree, please do not use the service."),
        ("2. Acceptable Use",
         "<ul style='padding-left:20px;line-height:2.2;'>"
         "<li>Do not use bots or automated scripts</li>"
         "<li>Do not attempt to manipulate scores or ratings</li>"
         "<li>Do not harass other players</li>"
         "<li>Do not attempt unauthorised access to the system</li></ul>"),
        ("3. Intellectual Property",
         "Cricket Bingo is an independent fan-made game, not affiliated with BCCI or any IPL franchise. "
         "Team logos are used for identification purposes in an educational/entertainment context."),
        ("4. Account Responsibility",
         "You are responsible for the security of your Google account. We are not liable for loss from unauthorised access."),
        ("5. Disclaimer",
         "Cricket Bingo is provided \"as is\" without warranties. We do not guarantee uninterrupted or error-free service."),
        ("6. Advertising", "The site displays ads via Google AdSense. We are not responsible for third-party ad content."),
        ("7. Contact", "Questions? Email <a href='mailto:tehm8111@gmail.com' style='color:var(--acc);'>tehm8111@gmail.com</a>"),
    ]
    return render_template_string(page(TERMS_BODY, "Terms & Conditions"), sections=sections)

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
            db.execute("UPDATE users SET email=?,name=?,avatar=? WHERE google_id=?", (email, name, avatar, gid))
        else:
            db.execute("INSERT INTO users(google_id,email,name,avatar) VALUES(?,?,?,?)", (gid, email, name, avatar))
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
@login_required
def play():
    game_mode  = request.args.get("mode", "solo")
    ds         = request.args.get("data_source", "overall")
    grid_size  = int(request.args.get("grid_size", 3))
    difficulty = request.args.get("difficulty", "normal")
    room_code  = request.args.get("room_code", None)

    if game_mode == "daily":
        state = get_or_create_daily()
        if state:
            # Use state's own params for daily
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
    else:
        state = create_game_state(ds, grid_size, difficulty)

    if not state or not state.get("players"):
        log.error(f"Game state creation failed for ds={ds}")
        return (
            f"<div style='font-family:sans-serif;padding:60px;text-align:center;'>"
            f"<h2 style='color:#EF4444;margin-bottom:16px;'>⚠ No player data found for '{ds}'</h2>"
            f"<p>Ensure <code>overall.json</code> / <code>ipl26.json</code> exist in project root.</p>"
            f"<a href='/' style='color:#22C55E;'>← Back to Home</a></div>", 500
        )

    # Sync grid_state length
    n = grid_size * grid_size
    if len(state.get("grid_state", [])) != n:
        state["grid_state"] = [None] * n

    session["game_state"] = {"state": state, "room_code": room_code, "mode": game_mode, "data_source": ds}
    mode_labels = {"solo": "Solo Practice", "rated": "⚡ Rated", "friends": "👥 Friends", "daily": "📅 Daily"}

    grid = state["grid"]
    for cell in grid:
        cell["logo"] = TEAM_LOGOS.get(cell["value"], "") if cell["type"] == "team" else ""

    players_json = json.dumps(state["players"], default=str)

    opponent = None
    if room_code:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if row:
            oid = row["player2_id"] if row["player1_id"] == current_user.id else row["player1_id"]
            if oid:
                ou = query_db("SELECT name FROM users WHERE id=?", (oid,), one=True)
                if ou: opponent = ou["name"]

    return render_template_string(
        page(GAME_BODY, "Play"),
        grid          = grid,
        players_json  = players_json,
        grid_size     = grid_size,
        total_players = len(state["players"]),
        game_mode     = game_mode,
        mode_label    = mode_labels.get(game_mode, game_mode),
        data_source   = ds,
        room_code     = room_code,
        opponent      = opponent,
    )

@app.route("/matchmaking")
@login_required
def matchmaking():
    ds         = request.args.get("data_source", "overall")
    grid_size  = int(request.args.get("grid_size", 3))
    difficulty = request.args.get("difficulty", "normal")
    return render_template_string(
        page(MATCHMAKING_BODY, "Finding Match"),
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
    return render_template_string(
        page(ROOM_BODY, f"Room {room_code}"),
        room_code=room_code, is_host=is_host, data_source=ds)

@app.route("/leaderboard")
def leaderboard():
    season = get_current_season()
    if not season:
        return render_template_string(page(LEADERBOARD_BODY, "Leaderboard"),
            season={"name": "No Season", "end_date": "—"}, rows=[])
    raw = query_db("""SELECT sr.user_id,sr.rating,sr.wins,sr.losses,sr.total_games,u.name
        FROM season_ratings sr JOIN users u ON u.id=sr.user_id
        WHERE sr.season_id=? ORDER BY sr.rating DESC LIMIT 100""", (season["id"],))
    rows = []
    for r in raw:
        t, tc, ti = rating_tier(r["rating"])
        wr = round(r["wins"] / r["total_games"] * 100) if r["total_games"] > 0 else 0
        rows.append({"user_id": r["user_id"], "name": r["name"], "rating": r["rating"],
                     "wins": r["wins"], "losses": r["losses"], "tier": t, "tier_color": tc, "tier_icon": ti, "win_rate": wr})
    return render_template_string(page(LEADERBOARD_BODY, "Leaderboard"), season=season, rows=rows)

@app.route("/profile/<int:user_id>")
def profile(user_id):
    ur = query_db("SELECT * FROM users WHERE id=?", (user_id,), one=True)
    if not ur: return "User not found", 404
    season = get_current_season(); rating = 1200.0
    tier, tier_color, tier_icon = "Beginner", "#9CA3AF", "🟤"; sr = None
    if season:
        sr = query_db("SELECT * FROM season_ratings WHERE user_id=? AND season_id=?", (user_id, season["id"]), one=True)
        if sr: rating = sr["rating"]; tier, tier_color, tier_icon = rating_tier(rating)
    stats = {
        "total_games":  sr["total_games"] if sr else 0,
        "wins":         sr["wins"] if sr else 0,
        "losses":       sr["losses"] if sr else 0,
        "win_rate":     round(sr["wins"] / sr["total_games"] * 100) if sr and sr["total_games"] > 0 else 0,
        "best_streak":  sr["best_streak"] if sr else 0,
        "avg_accuracy": round(sr["accuracy_sum"] / sr["total_games"]) if sr and sr["total_games"] > 0 else 0,
        "avg_time":     round(sr["time_sum"] / sr["total_games"]) if sr and sr["total_games"] > 0 else 0,
    }
    raw = query_db("""SELECT m.*,u1.name as p1name,u2.name as p2name FROM matches m
        LEFT JOIN users u1 ON u1.id=m.player1_id LEFT JOIN users u2 ON u2.id=m.player2_id
        WHERE m.player1_id=? OR m.player2_id=? ORDER BY m.played_at DESC LIMIT 10""", (user_id, user_id))
    matches = []
    for m in raw:
        ip1   = m["player1_id"] == user_id
        score = m["player1_score"] if ip1 else m["player2_score"]
        opp   = m["p2name"] if ip1 else m["p1name"]
        won   = None
        if m["winner_id"] == user_id:       won = True
        elif m["winner_id"] is not None:    won = False
        rc = m["rating_change"] if ip1 else -m["rating_change"]
        matches.append({"won": won, "score": score, "opponent": opp, "rating_change": rc,
                        "mode": m["mode"], "played_at": m["played_at"]})
    return render_template_string(page(PROFILE_BODY, ur["name"]),
        profile_user=ur, tier=tier, tier_color=tier_color, tier_icon=tier_icon,
        rating=rating, stats=stats, matches=matches)

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
    return render_template_string(page(DAILY_BODY, "Daily Challenge"),
        today=today, rows=[dict(r) for r in raw], already_played=played)

# ── API ────────────────────────────────────────────────────────────────────────

@app.route("/api/contact", methods=["POST"])
def api_contact():
    data = request.get_json(force=True)
    name    = str(data.get("name", "")).strip()[:100]
    email   = str(data.get("email", "")).strip()[:200]
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

    # FIX: Use a unique session key to avoid collision
    contact_count = session.get("cb_contact_count", 0)
    if contact_count >= 3:
        return jsonify({"success": False, "error": "Too many submissions. Please email us directly."})
    session["cb_contact_count"] = contact_count + 1

    html_body = f"""
    <html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
      <h2 style="color:#22C55E;margin-bottom:20px;">New Cricket Bingo Contact Submission</h2>
      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        <tr><td style="padding:10px;font-weight:bold;background:#f5f5f5;border-radius:6px 0 0 6px;">From:</td>
            <td style="padding:10px;">{name} &lt;{email}&gt;</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f5f5f5;">Subject:</td>
            <td style="padding:10px;">{subject}</td></tr>
      </table>
      <h3>Message:</h3>
      <div style="background:#f9f9f9;padding:20px;border-radius:10px;white-space:pre-wrap;line-height:1.7;">{message}</div>
    </body></html>
    """
    text_body = f"From: {name} <{email}>\nSubject: {subject}\n\nMessage:\n{message}"

    success, err = send_email(CONTACT_EMAIL, f"[Cricket Bingo] {subject} — from {name}", html_body, text_body)
    if success:
        log.info(f"Contact email sent from {email}")
        return jsonify({"success": True})
    else:
        log.warning(f"Contact email failed: {err}")
        return jsonify({"success": False, "error": "Email service unavailable. Please email us directly."})

@app.route("/api/create_room", methods=["POST"])
@login_required
def api_create_room():
    data = request.get_json(force=True)
    ds   = data.get("data_source", "overall")
    code = gen_room_code()
    init = {"data_source": ds, "grid_size": 3, "difficulty": "normal", "grid": [], "players": []}
    query_db("INSERT INTO active_games(room_code,player1_id,game_state,mode) VALUES(?,?,?,?)",
             (code, current_user.id, json.dumps(init), "friends"), commit=True)
    return jsonify({"code": code})

@app.route("/api/validate_move", methods=["POST"])
@login_required
def api_validate_move():
    data = request.get_json(force=True)
    pid  = data.get("player_id")
    cidx = data.get("cell_idx")
    ds   = data.get("data_source", "overall")

    gi = session.get("game_state")
    if not gi:
        return jsonify({"correct": False, "error": "no_session"})

    state = gi.get("state", {})
    grid  = state.get("grid", [])
    gst   = state.get("grid_state") or [None] * len(grid)

    if cidx is None or cidx >= len(grid):
        return jsonify({"correct": False})
    if gst[cidx] is not None:
        return jsonify({"correct": False, "reason": "already_filled"})

    pool   = get_pool(ds)
    player = next((p for p in pool if str(p.get("id")) == str(pid)), None)

    # FIX: Better fallback for player_N indexed IDs
    if not player and isinstance(pid, str) and pid.startswith("player_"):
        try:
            idx    = int(pid.split("_", 1)[1])
            player = pool[idx] if 0 <= idx < len(pool) else None
        except (ValueError, IndexError):
            pass

    if not player:
        log.warning(f"Player not found: id={pid}, ds={ds}, pool_size={len(pool)}")
        return jsonify({"correct": False, "reason": "player_not_found"})

    correct = player_matches_cell(player, grid[cidx], ds)

    # Update session grid_state
    if correct:
        if "grid_state" not in state or len(state["grid_state"]) != len(grid):
            state["grid_state"] = [None] * len(grid)
        state["grid_state"][cidx] = str(pid)
        gi["state"] = state
        session["game_state"] = gi

    return jsonify({"correct": correct})

@app.route("/api/wildcard_hint", methods=["POST"])
@login_required
def api_wildcard_hint():
    data = request.get_json(force=True)
    pid  = data.get("player_id"); ds = data.get("data_source", "overall")
    gi   = session.get("game_state")
    if not gi: return jsonify({"matching_cells": []})
    state  = gi.get("state", {}); grid = state.get("grid", [])
    # FIX: safe guard for grid_state
    gstate = state.get("grid_state") or [None] * len(grid)
    pool   = get_pool(ds)
    player = next((p for p in pool if str(p.get("id")) == str(pid)), None)
    if not player and isinstance(pid, str) and pid.startswith("player_"):
        try:
            idx = int(pid.split("_", 1)[1])
            player = pool[idx] if 0 <= idx < len(pool) else None
        except (ValueError, IndexError):
            pass
    if not player: return jsonify({"matching_cells": []})
    cells  = [i for i, c in enumerate(grid) if gstate[i] is None and player_matches_cell(player, c, ds)]
    return jsonify({"matching_cells": cells})

@app.route("/api/end_game", methods=["POST"])
@login_required
def api_end_game():
    data      = request.get_json(force=True)
    gmode     = data.get("mode", "solo"); ds = data.get("data_source", "overall")
    score     = float(data.get("score", 0)); elapsed = float(data.get("elapsed", 0))
    accuracy  = float(data.get("accuracy", 0)); room_code = data.get("room_code")
    result    = {"rating_change": 0}; season = get_current_season()

    if gmode == "daily":
        today = date.today().isoformat()
        try:
            query_db("INSERT OR IGNORE INTO daily_results(user_id,challenge_date,score,completion_time,accuracy) VALUES(?,?,?,?,?)",
                     (current_user.id, today, score, elapsed, accuracy), commit=True)
        except Exception as e:
            log.error(f"Daily result insert failed: {e}")

    elif gmode == "rated" and room_code and season:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if row and row["status"] != "finished":
            gs      = json.loads(row["game_state"]); results = gs.get("results", {})
            results[str(current_user.id)] = {"score": score, "elapsed": elapsed, "accuracy": accuracy}
            gs["results"] = results
            if len(results) >= 2:
                p1, p2 = row["player1_id"], row["player2_id"]
                r1     = results.get(str(p1), {"score": 0, "elapsed": 9999})
                r2     = results.get(str(p2), {"score": 0, "elapsed": 9999})
                winner = p1 if r1["score"] > r2["score"] or (r1["score"] == r2["score"] and r1["elapsed"] <= r2["elapsed"]) else p2
                rat1   = get_user_rating(p1, season["id"]); rat2 = get_user_rating(p2, season["id"])
                exp1   = elo_expected(rat1, rat2); act1 = 1.0 if winner == p1 else 0.0
                new1   = elo_update(rat1, exp1, act1); new2 = elo_update(rat2, 1 - exp1, 1 - act1)
                delta  = round(new1 - rat1, 1)
                ensure_season_rating(p1, season["id"]); ensure_season_rating(p2, season["id"])
                for uid, nr, w, rd in [(p1, new1, 1 if winner == p1 else 0, r1), (p2, new2, 1 if winner == p2 else 0, r2)]:
                    query_db("""UPDATE season_ratings SET rating=?,wins=wins+?,losses=losses+?,
                        total_games=total_games+1,accuracy_sum=accuracy_sum+?,time_sum=time_sum+?,
                        win_streak=CASE WHEN ?=1 THEN win_streak+1 ELSE 0 END,
                        best_streak=MAX(best_streak,CASE WHEN ?=1 THEN win_streak+1 ELSE best_streak END)
                        WHERE user_id=? AND season_id=?""",
                        (nr, w, 1-w, rd.get("accuracy", 0), rd.get("elapsed", 0), w, w, uid, season["id"]), commit=True)
                query_db("""INSERT INTO matches(player1_id,player2_id,winner_id,
                    player1_score,player2_score,player1_time,player2_time,
                    player1_accuracy,player2_accuracy,rating_change,mode,season_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p1, p2, winner, r1["score"], r2["score"], r1["elapsed"], r2["elapsed"],
                     r1.get("accuracy", 0), r2.get("accuracy", 0), abs(delta), "rated", season["id"]), commit=True)
                query_db("UPDATE active_games SET status='finished',game_state=? WHERE room_code=?",
                         (json.dumps(gs), room_code), commit=True)
                result["rating_change"] = delta if current_user.id == p1 else -delta
                result["winner"]        = winner == current_user.id
            else:
                query_db("UPDATE active_games SET game_state=? WHERE room_code=?",
                         (json.dumps(gs), room_code), commit=True)
    elif season:
        ensure_season_rating(current_user.id, season["id"])
        query_db("UPDATE season_ratings SET total_games=total_games+1,accuracy_sum=accuracy_sum+?,time_sum=time_sum+? WHERE user_id=? AND season_id=?",
                 (accuracy, elapsed, current_user.id, season["id"]), commit=True)

    session.pop("game_state", None)
    return jsonify(result)

# ── SocketIO ──────────────────────────────────────────────────────────────────
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
    ds   = data.get("data_source", "overall"); gs = data.get("grid_size", 3); diff = data.get("difficulty", "normal")
    s    = get_current_season(); rat = get_user_rating(current_user.id, s["id"]) if s else 1200.0
    query_db("INSERT OR REPLACE INTO matchmaking_queue(user_id,rating,data_source,grid_size,difficulty) VALUES(?,?,?,?,?)",
             (current_user.id, rat, ds, gs, diff), commit=True)
    cands = query_db("""SELECT * FROM matchmaking_queue WHERE user_id!=? AND data_source=?
        AND grid_size=? AND difficulty=? AND ABS(rating-?)<=300 ORDER BY ABS(rating-?) ASC LIMIT 1""",
        (current_user.id, ds, gs, diff, rat, rat))
    if cands:
        opp = cands[0]
        query_db("DELETE FROM matchmaking_queue WHERE user_id IN (?,?)", (current_user.id, opp["user_id"]), commit=True)
        code  = gen_room_code(); state = create_game_state(ds, gs, diff)
        query_db("INSERT INTO active_games(room_code,player1_id,player2_id,game_state,mode,status) VALUES(?,?,?,?,?,?)",
                 (code, opp["user_id"], current_user.id, json.dumps(state, default=str), "rated", "active"), commit=True)
        emit("match_found", {"room_code": code})
        emit("match_found", {"room_code": code}, to=f"queue_{opp['user_id']}")
    else:
        join_room(f"queue_{current_user.id}")
        emit("matchmaking_status", {"message": "Searching for opponent with similar rating…"})

@socketio.on("leave_matchmaking")
def on_leave_q():
    if current_user.is_authenticated:
        query_db("DELETE FROM matchmaking_queue WHERE user_id=?", (current_user.id,), commit=True)

@socketio.on("start_room_game")
def on_start(data):
    rm   = data.get("room"); ds = data.get("data_source", "overall")
    gs   = data.get("grid_size", 3); diff = data.get("difficulty", "normal")
    row  = query_db("SELECT * FROM active_games WHERE room_code=?", (rm,), one=True)
    if not row or row["player1_id"] != current_user.id: return
    state = create_game_state(ds, gs, diff)
    query_db("UPDATE active_games SET game_state=?,status='active' WHERE room_code=?",
             (json.dumps(state, default=str), rm), commit=True)
    emit("game_start", {"room_code": rm}, to=rm)

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    email_status = "✓ Configured" if SMTP_USER and SMTP_PASSWORD else "✗ Not configured"
    print(f"""
╔══════════════════════════════════════════════════════════╗
║       🏏  Cricket Bingo v4  — UI/UX Overhaul             ║
╠══════════════════════════════════════════════════════════╣
║  URL     → http://localhost:{port:<6}                     ║
║  DB      → {DATABASE:<20}                    ║
║  Players → {len(OVERALL_DATA):<5} overall / {len(IPL26_DATA):<5} ipl26               ║
║  Email   → {email_status:<40}║
║  Theme   → Dark (default) + Light mode toggle            ║
╚══════════════════════════════════════════════════════════╝
""")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
