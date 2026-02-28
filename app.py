"""
=============================================================
CRICKET BINGO - Complete Production-Ready Web Application
=============================================================

SETUP INSTRUCTIONS:
-------------------
1. Install dependencies:
   pip install flask flask-login flask-dance flask-socketio eventlet python-dotenv

2. Google OAuth Setup:
   - Go to https://console.cloud.google.com
   - Create a project
   - APIs & Services → Library → Enable "Google People API"
   - APIs & Services → Credentials → Create OAuth 2.0 Client ID (Web app)
   - Authorized redirect URIs: http://localhost:5000/login/google/authorized
   - Copy Client ID and Secret

3. Create .env from template:
   copy .env.example .env   (Windows)  or  cp .env.example .env   (Mac/Linux)
   Edit .env with your real values (never commit .env)

4. Place overall.json and ipl26.json in same folder.

5. Run: python app.py

RENDER DEPLOYMENT:
------------------
- requirements.txt: flask flask-login flask-dance flask-socketio eventlet python-dotenv
- Build: pip install -r requirements.txt
- Start: python app.py
- Add env vars in Render dashboard (no OAUTHLIB_INSECURE_TRANSPORT on prod)
- Add redirect URI: https://your-app.onrender.com/login/google/authorized

FIX NOTE:
---------
The variable 'source' was renamed to 'data_source' throughout to avoid
the conflict with Flask's render_template_string() built-in 'source' parameter.
"""

import os, json, random, string, hashlib, time
from datetime import datetime, date, timedelta
from flask import Flask, render_template_string, request, session, redirect, url_for, jsonify, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_dance.contrib.google import make_google_blueprint, google
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from dotenv import load_dotenv

load_dotenv()

# ─── APP CONFIG ───────────────────────────────────────────────────────────────
app = Flask(__name__)
_secret = os.getenv("SECRET_KEY")
if not _secret and os.getenv("FLASK_DEBUG", "1") != "1":
    raise RuntimeError("SECRET_KEY must be set in production. Add it in Render dashboard.")
app.secret_key = _secret or "dev-secret-key-change-me"
app.config["OAUTHLIB_INSECURE_TRANSPORT"] = os.getenv("OAUTHLIB_INSECURE_TRANSPORT", "0") == "1"

socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")
login_manager = LoginManager(app)
login_manager.login_view = "home"

google_bp = make_google_blueprint(
    client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
    scope=["openid","https://www.googleapis.com/auth/userinfo.email",
           "https://www.googleapis.com/auth/userinfo.profile"],
    redirect_to="oauth_callback"
)
app.register_blueprint(google_bp, url_prefix="/login")

DATABASE = "cricket_bingo.db"

# ─── DATABASE ─────────────────────────────────────────────────────────────────
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
            google_id TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS season_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            season_id INTEGER NOT NULL,
            rating REAL DEFAULT 1200,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_games INTEGER DEFAULT 0,
            accuracy_sum REAL DEFAULT 0,
            time_sum REAL DEFAULT 0,
            win_streak INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0,
            UNIQUE(user_id, season_id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player1_id INTEGER, player2_id INTEGER, winner_id INTEGER,
            player1_score REAL DEFAULT 0, player2_score REAL DEFAULT 0,
            player1_time REAL DEFAULT 0,  player2_time REAL DEFAULT 0,
            player1_accuracy REAL DEFAULT 0, player2_accuracy REAL DEFAULT 0,
            rating_change REAL DEFAULT 0,
            mode TEXT DEFAULT 'rated', data_source TEXT DEFAULT 'overall',
            grid_size INTEGER DEFAULT 3, difficulty TEXT DEFAULT 'normal',
            season_id INTEGER, played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(player1_id) REFERENCES users(id),
            FOREIGN KEY(player2_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS active_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT UNIQUE NOT NULL,
            player1_id INTEGER, player2_id INTEGER,
            game_state TEXT NOT NULL,
            status TEXT DEFAULT 'waiting',
            mode TEXT DEFAULT 'rated',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(player1_id) REFERENCES users(id),
            FOREIGN KEY(player2_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS matchmaking_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            rating REAL NOT NULL,
            data_source TEXT DEFAULT 'overall',
            grid_size INTEGER DEFAULT 3,
            difficulty TEXT DEFAULT 'normal',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS daily_challenge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_date TEXT UNIQUE NOT NULL,
            game_state TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            challenge_date TEXT NOT NULL,
            score REAL DEFAULT 0,
            completion_time REAL DEFAULT 0,
            accuracy REAL DEFAULT 0,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, challenge_date),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        db.commit()
        _ensure_season()

def _ensure_season():
    db = get_db()
    today = date.today().isoformat()
    row = db.execute("SELECT * FROM seasons WHERE start_date<=? AND end_date>=?", (today,today)).fetchone()
    if not row:
        last = db.execute("SELECT MAX(id) as mid FROM seasons").fetchone()
        num  = (last["mid"] or 0) + 1
        s    = date.today()
        e    = s + timedelta(days=90)
        db.execute("INSERT INTO seasons(name,start_date,end_date) VALUES(?,?,?)",
                   (f"Season {num}", s.isoformat(), e.isoformat()))
        db.commit()

def get_current_season():
    today = date.today().isoformat()
    return query_db("SELECT * FROM seasons WHERE start_date<=? AND end_date>=?", (today,today), one=True)

# ─── USER ─────────────────────────────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, row):
        self.id=row["id"]; self.google_id=row["google_id"]
        self.email=row["email"]; self.name=row["name"]; self.avatar=row["avatar"]

@login_manager.user_loader
def load_user(user_id):
    row = query_db("SELECT * FROM users WHERE id=?", (user_id,), one=True)
    return User(row) if row else None

# ─── DATA ─────────────────────────────────────────────────────────────────────
def load_json(fp):
    if not os.path.exists(fp): return []
    with open(fp,"r",encoding="utf-8") as f: return json.load(f)

OVERALL_DATA = load_json("overall.json")
IPL26_DATA   = load_json("ipl26.json")

def get_pool(ds):
    return OVERALL_DATA if ds == "overall" else IPL26_DATA

# ─── GAME LOGIC ───────────────────────────────────────────────────────────────
def gen_cell(pool, ds, difficulty, cell_type):
    if not pool: return {"type":"team","value":"Unknown"}
    if cell_type=="combo" and difficulty=="hard":
        p = random.choice(pool)
        if ds=="overall":
            combos=[]
            if p.get("iplTeams"):
                t=random.choice(p["iplTeams"])
                combos.append(f"{t} + {p['nation']}")
                if p.get("trophies"):
                    tr=random.choice(p["trophies"])
                    combos.extend([f"{t} + {tr}",f"{p['nation']} + {tr}"])
            if combos: return {"type":"combo","value":random.choice(combos)}
        else:
            return {"type":"combo","value":f"{p['team']} + {p['nation']}"}
    if cell_type=="team":
        teams=list({t for p in pool for t in p.get("iplTeams",[])} if ds=="overall" else {p["team"] for p in pool})
        if teams: return {"type":"team","value":random.choice(teams)}
    if cell_type=="nation":
        nations=list({p["nation"] for p in pool})
        if nations: return {"type":"nation","value":random.choice(nations)}
    if cell_type=="trophy" and ds=="overall":
        trophies=list({t for p in pool for t in p.get("trophies",[])})
        if trophies: return {"type":"trophy","value":random.choice(trophies)}
    nations=list({p["nation"] for p in pool})
    return {"type":"nation","value":random.choice(nations) if nations else "India"}

def build_grid(size, ds, difficulty):
    pool=get_pool(ds)
    if not pool: return []
    n=size*size
    if difficulty=="easy": types=["team"]*n
    elif difficulty=="hard": types=["team"]*(n//3)+["nation"]*(n//3)+["combo"]*(n-2*(n//3))
    else: types=["team"]*(n//2)+["nation"]*(n-n//2)
    random.shuffle(types)
    cells,seen=[],set()
    for t in types:
        for _ in range(20):
            cell=gen_cell(pool,ds,difficulty,t)
            if cell["value"] not in seen:
                seen.add(cell["value"]); cells.append(cell); break
        else: cells.append(gen_cell(pool,ds,difficulty,t))
    return cells

def player_matches_cell(player, cell, ds):
    ct,cv=cell["type"],cell["value"]
    if ds=="overall":
        teams=player.get("iplTeams",[]); nation=player.get("nation",""); trophies=player.get("trophies",[])
    else:
        teams=[player.get("team","")]; nation=player.get("nation",""); trophies=[]
    if ct=="team":   return cv in teams
    if ct=="nation": return cv==nation
    if ct=="trophy": return cv in trophies
    if ct=="combo":
        parts=[p.strip() for p in cv.split("+")]
        return all(p in teams or p==nation or p in trophies for p in parts)
    return False

def create_game_state(ds, grid_size, difficulty, seed=None):
    if seed is not None: random.seed(seed)
    pool=list(get_pool(ds))
    if not pool: return None
    random.shuffle(pool)
    n=grid_size*grid_size
    selected=pool[:min(len(pool),n*3)]
    grid=build_grid(grid_size,ds,difficulty)
    return {
        "data_source":ds,"grid_size":grid_size,"difficulty":difficulty,
        "grid":grid,"players":selected,
        "current_player_idx":0,"grid_state":[None]*n,
        "skips_used":0,"wildcard_used":False,"correct":0,"wrong":0,
        "started_at":time.time(),"seed":seed or random.randint(0,9999999),
    }

# ─── ELO ──────────────────────────────────────────────────────────────────────
def elo_expected(a,b): return 1/(1+10**((b-a)/400))
def elo_update(r,exp,act,k=32): return r+k*(act-exp)

def get_user_rating(uid,sid):
    row=query_db("SELECT rating FROM season_ratings WHERE user_id=? AND season_id=?",(uid,sid),one=True)
    return row["rating"] if row else 1200.0

def ensure_season_rating(uid,sid):
    query_db("INSERT OR IGNORE INTO season_ratings(user_id,season_id,rating) VALUES(?,?,1200)",
             (uid,sid),commit=True)

def rating_tier(r):
    if r<1000:  return ("Beginner","#6B7280","🟤")
    elif r<1200: return ("Amateur","#3B82F6","🔵")
    elif r<1400: return ("Pro","#10B981","🟢")
    elif r<1600: return ("Elite","#F59E0B","🟡")
    else:        return ("Legend","#EF4444","🔴")

# ─── DAILY ────────────────────────────────────────────────────────────────────
def get_or_create_daily():
    today=date.today().isoformat()
    row=query_db("SELECT * FROM daily_challenge WHERE challenge_date=?",(today,),one=True)
    if row: return json.loads(row["game_state"])
    seed=int(hashlib.sha256(today.encode()).hexdigest(),16)%9999999
    state=create_game_state("overall",3,"normal",seed)
    if state:
        query_db("INSERT INTO daily_challenge(challenge_date,game_state) VALUES(?,?)",
                 (today,json.dumps(state,default=str)),commit=True)
    return state

def gen_room_code():
    return "".join(random.choices(string.digits,k=6))

# ══════════════════════════════════════════════════════════════════════════════
# HTML TEMPLATES  (dark premium theme)
# ══════════════════════════════════════════════════════════════════════════════

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
:root{
  --bg:#050A18;--bg2:#0D1529;--sur:#111827;--sur2:#1A2540;
  --bdr:#1E2D4D;--acc:#6C63FF;--acc2:#8B5CF6;--glow:rgba(108,99,255,.35);
  --grn:#10B981;--red:#EF4444;--yel:#F59E0B;--cyn:#06B6D4;
  --txt:#F1F5F9;--mut:#64748B;--mut2:#94A3B8;
  --grad:linear-gradient(135deg,#6C63FF,#8B5CF6,#EC4899);
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;overflow-x:hidden;}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(ellipse at 15% 20%,rgba(108,99,255,.07),transparent 55%),
             radial-gradient(ellipse at 85% 80%,rgba(139,92,246,.05),transparent 55%);}
*{position:relative;z-index:1;}

/* NAV */
.nav{background:rgba(17,24,39,.96);backdrop-filter:blur(20px);border-bottom:1px solid var(--bdr);
  padding:0 24px;height:64px;display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:100;}
.logo{font-size:1.25rem;font-weight:900;background:var(--grad);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-decoration:none;}
.nav-links{display:flex;align-items:center;gap:6px;}
.nav-links a{color:var(--mut2);font-size:.875rem;font-weight:500;padding:6px 12px;
  border-radius:8px;text-decoration:none;transition:all .2s;}
.nav-links a:hover{color:var(--txt);background:var(--sur2);}

/* BTNS */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:10px 20px;border-radius:10px;font-size:.9rem;font-weight:600;
  cursor:pointer;border:none;transition:all .2s;text-decoration:none;font-family:inherit;}
.bp{background:var(--grad);color:#fff;box-shadow:0 4px 20px var(--glow);}
.bp:hover{transform:translateY(-2px);box-shadow:0 8px 30px var(--glow);}
.bo{background:transparent;border:1px solid var(--bdr);color:var(--mut2);}
.bo:hover{border-color:var(--acc);color:var(--txt);background:rgba(108,99,255,.1);}
.bg{background:rgba(255,255,255,.05);color:var(--txt);border:1px solid var(--bdr);}
.bg:hover{background:rgba(255,255,255,.1);}
.bgrn{background:var(--grn);color:#fff;}
.bgrn:hover{filter:brightness(1.1);transform:translateY(-1px);}
.blg{padding:14px 28px;font-size:1rem;border-radius:12px;}
.bsm{padding:6px 14px;font-size:.8rem;border-radius:8px;}
.bxs{padding:4px 10px;font-size:.75rem;border-radius:6px;}
.btn:disabled{opacity:.4;cursor:not-allowed!important;transform:none!important;}

/* CARDS */
.card{background:var(--sur);border:1px solid var(--bdr);border-radius:16px;padding:24px;}
.card-sm{background:var(--sur);border:1px solid var(--bdr);border-radius:12px;padding:16px;}
.glow{box-shadow:0 0 40px rgba(108,99,255,.15);}

/* INPUTS */
.inp{background:var(--sur2);border:1px solid var(--bdr);border-radius:10px;
  padding:10px 14px;color:var(--txt);font-size:.9rem;width:100%;
  outline:none;font-family:inherit;transition:border-color .2s;}
.inp:focus{border-color:var(--acc);}
select.inp option{background:var(--sur);}
.lbl{font-size:.8rem;color:var(--mut2);font-weight:500;margin-bottom:6px;display:block;}

/* TABLE */
table{width:100%;border-collapse:collapse;}
th{padding:10px 16px;text-align:left;font-size:.75rem;font-weight:600;color:var(--mut);
  text-transform:uppercase;letter-spacing:.05em;background:var(--sur2);border-bottom:1px solid var(--bdr);}
td{padding:12px 16px;font-size:.875rem;border-bottom:1px solid var(--bdr);}
tr:last-child td{border-bottom:none;}
tr:hover td{background:rgba(108,99,255,.04);}

/* LAYOUT */
.ctr{max-width:1100px;margin:0 auto;padding:0 20px;}
.page{padding:40px 0;}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;}
.row{display:flex;}.aic{align-items:center;}.jcb{justify-content:space-between;}
.jcc{justify-content:center;}.fdc{flex-direction:column;}
.gap2{gap:8px;}.gap3{gap:12px;}.gap4{gap:16px;}.gap6{gap:24px;}
.tc{text-align:center;}.wf{width:100%;}
.mb1{margin-bottom:4px;}.mb2{margin-bottom:8px;}.mb3{margin-bottom:12px;}
.mb4{margin-bottom:16px;}.mb6{margin-bottom:24px;}
.mt2{margin-top:8px;}.mt4{margin-top:16px;}.mt6{margin-top:24px;}
.muted{color:var(--mut2);font-size:.875rem;}
.xs{font-size:.75rem;}.sm{font-size:.875rem;}.lg{font-size:1.1rem;}
.bold{font-weight:700;}.black{font-weight:900;}
.ca{color:var(--acc2);}.cg{color:var(--grn);}.cr{color:var(--red);}
.cy{color:var(--yel);}.cc{color:var(--cyn);}.cm{color:var(--mut2);}

/* BINGO GRID */
.bgrid{display:grid;gap:8px;margin:0 auto;}
.bgrid.s3{grid-template-columns:repeat(3,1fr);max-width:480px;}
.bgrid.s4{grid-template-columns:repeat(4,1fr);max-width:600px;}
.cell{background:var(--sur2);border:2px solid var(--bdr);border-radius:12px;
  padding:14px 10px;text-align:center;font-size:.78rem;font-weight:600;
  cursor:pointer;transition:all .2s;min-height:72px;
  display:flex;align-items:center;justify-content:center;
  line-height:1.3;user-select:none;color:var(--mut2);}
.cell:hover:not(.filled):not(.disabled){border-color:var(--acc);
  background:rgba(108,99,255,.15);color:var(--txt);transform:scale(1.04);
  box-shadow:0 0 20px var(--glow);}
.cell.filled{background:rgba(16,185,129,.15);border-color:var(--grn);
  color:var(--grn);cursor:default;animation:pop .3s ease;}
.cell.wrong{animation:shake .4s ease;}
.cell.hint{border-color:var(--yel);background:rgba(245,158,11,.1);}
@keyframes pop{0%{transform:scale(1.15);}100%{transform:scale(1);}}
@keyframes shake{0%,100%{transform:translateX(0);}25%{transform:translateX(-6px);}75%{transform:translateX(6px);}}

/* TIMER */
.tbar-wrap{background:var(--sur2);border-radius:999px;height:6px;overflow:hidden;}
.tbar{height:100%;border-radius:999px;transition:width .9s linear,background .5s;}

/* PLAYER CARD */
.pcard{background:var(--sur);border:2px solid var(--acc);border-radius:16px;
  padding:20px;text-align:center;overflow:hidden;}
.pcard::before{content:'';position:absolute;inset:0;
  background:radial-gradient(circle at 50% 0%,var(--glow),transparent 70%);pointer-events:none;}
.pname{font-size:1.4rem;font-weight:800;letter-spacing:-.5px;}

/* STAT */
.stat{background:var(--sur);border:1px solid var(--bdr);border-radius:14px;padding:20px;text-align:center;}
.stat-n{font-size:2rem;font-weight:900;line-height:1;}
.stat-l{font-size:.8rem;color:var(--mut2);margin-top:4px;font-weight:500;}

/* MODAL */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.8);backdrop-filter:blur(4px);
  display:flex;align-items:center;justify-content:center;z-index:1000;animation:fi .2s;}
.modal{background:var(--sur);border:1px solid var(--bdr);border-radius:20px;padding:32px;
  max-width:440px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.5);}
@keyframes fi{from{opacity:0;}to{opacity:1;}}

/* TOAST */
#toasts{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;}
.toast{background:var(--sur);border:1px solid var(--bdr);border-radius:10px;
  padding:12px 18px;font-size:.875rem;font-weight:500;animation:su .3s ease;
  max-width:280px;display:flex;align-items:center;gap:8px;}
.toast.success{border-left:3px solid var(--grn);}
.toast.error  {border-left:3px solid var(--red);}
.toast.info   {border-left:3px solid var(--acc);}
.toast.warn   {border-left:3px solid var(--yel);}
@keyframes su{from{transform:translateY(16px);opacity:0;}to{transform:translateY(0);opacity:1;}}

/* MISC */
.grad-txt{background:var(--grad);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;background-clip:text;}
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;
  border-radius:999px;font-size:.75rem;font-weight:700;border:1px solid currentColor;}
.room-code{font-size:2.5rem;font-weight:900;letter-spacing:16px;color:var(--acc);text-align:center;padding:16px;}
.prog-wrap{background:var(--sur2);border-radius:999px;height:8px;overflow:hidden;}
.prog-bar{height:100%;border-radius:999px;background:var(--grad);transition:width .4s;}

::-webkit-scrollbar{width:6px;}
::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:var(--sur2);border-radius:3px;}

@media(max-width:640px){
  .g2,.g3,.g4{grid-template-columns:1fr;}
  .hsm{display:none;}
  .cell{font-size:.7rem;padding:10px 6px;min-height:60px;}
}
@keyframes pulse2{0%,100%{opacity:.5;}50%{opacity:1;}}
</style>
"""

_NAV = """
<nav class="nav">
  <a class="logo" href="/">🏏 Cricket Bingo</a>
  <div class="nav-links">
    <a href="/leaderboard">🏆 Board</a>
    <a href="/daily">📅 Daily</a>
    {% if current_user.is_authenticated %}
      <a href="/profile/{{ current_user.id }}">👤 {{ current_user.name.split()[0] }}</a>
      <a href="/logout" class="btn bo bsm">Logout</a>
    {% else %}
      <a href="/login/google" class="btn bp bsm">Sign In</a>
    {% endif %}
  </div>
</nav>
"""

_FOOT = """
<div id="toasts"></div>
<script>
function toast(msg,type='info'){
  const d=document.createElement('div');
  d.className='toast '+type;d.textContent=msg;
  document.getElementById('toasts').appendChild(d);
  setTimeout(()=>d.remove(),2800);
}
</script>
"""

def page(body, title="Cricket Bingo"):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
{_CSS}
</head>
<body>
{_NAV}
{body}
{_FOOT}
</body>
</html>"""

# ─── HOME ─────────────────────────────────────────────────────────────────────
HOME_BODY = """
<div class="ctr page">
  <div class="tc" style="padding:60px 0 48px;">
    <div style="font-size:4rem;margin-bottom:16px;filter:drop-shadow(0 0 30px rgba(108,99,255,.5));">🏏</div>
    <h1 class="black grad-txt" style="font-size:3.2rem;letter-spacing:-2px;margin-bottom:12px;">Cricket Bingo</h1>
    <p class="muted" style="font-size:1.05rem;max-width:480px;margin:0 auto 36px;">
      Match cricket legends to their teams, nations & trophies.<br>Compete online or play with friends!
    </p>

    {% if not current_user.is_authenticated %}
    <a href="/login/google" class="btn bp blg" style="gap:12px;">
      <svg width="20" height="20" viewBox="0 0 24 24"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#fff"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#fff" opacity=".9"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#fff" opacity=".8"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#fff" opacity=".7"/></svg>
      Continue with Google
    </a>
    {% else %}

    <div id="s1" class="card glow" style="max-width:560px;margin:0 auto;">
      <h2 class="bold mb2" style="font-size:1.15rem;">Choose your game</h2>
      <p class="muted mb4 sm">Which player pool?</p>
      <div class="g2 gap4">
        <button class="btn bo blg wf" style="flex-direction:column;gap:6px;padding:20px;height:auto;" onclick="pickSrc('overall')">
          <span style="font-size:1.8rem;">🌍</span>
          <span style="font-size:.95rem;font-weight:700;">All-Time Overall</span>
          <span class="xs muted">All IPL players 2008–2026</span>
        </button>
        <button class="btn bo blg wf" style="flex-direction:column;gap:6px;padding:20px;height:auto;" onclick="pickSrc('ipl26')">
          <span style="font-size:1.8rem;">🏆</span>
          <span style="font-size:.95rem;font-weight:700;">IPL 2026 Edition</span>
          <span class="xs muted">Current season squads only</span>
        </button>
      </div>
    </div>

    <div id="s2" class="card glow" style="max-width:560px;margin:24px auto;display:none;">
      <div class="row aic gap3 mb4">
        <button onclick="go('s1','s2')" class="btn bg bxs">← Back</button>
        <h2 class="bold" id="s2t" style="font-size:1.05rem;"></h2>
      </div>
      <div class="g3 gap3">
        <button class="btn bo wf" style="flex-direction:column;gap:6px;padding:16px;height:auto;" onclick="pickMode('rated')">
          <span style="font-size:1.4rem;">⚡</span><span class="bold">Rated</span>
          <span class="xs muted">ELO matchmaking</span>
        </button>
        <button class="btn bo wf" style="flex-direction:column;gap:6px;padding:16px;height:auto;" onclick="pickMode('friends')">
          <span style="font-size:1.4rem;">👥</span><span class="bold">Friends</span>
          <span class="xs muted">6-digit room code</span>
        </button>
        <button class="btn bo wf" style="flex-direction:column;gap:6px;padding:16px;height:auto;" onclick="pickMode('solo')">
          <span style="font-size:1.4rem;">🎮</span><span class="bold">Solo</span>
          <span class="xs muted">Practice mode</span>
        </button>
      </div>
    </div>

    <div id="s3-rated" class="card glow" style="max-width:460px;margin:24px auto;display:none;">
      <div class="row aic gap3 mb4">
        <button onclick="go('s2','s3-rated')" class="btn bg bxs">← Back</button>
        <h2 class="bold" style="font-size:1.05rem;">⚡ Rated Match</h2>
      </div>
      <div class="g2 gap3 mb4">
        <div><label class="lbl">Grid Size</label>
          <select id="gs-r" class="inp"><option value="3">3×3 Standard</option><option value="4">4×4 Large</option></select>
        </div>
        <div><label class="lbl">Difficulty</label>
          <select id="df-r" class="inp">
            <option value="easy">Easy — Teams only</option>
            <option value="normal" selected>Normal — Teams & Nations</option>
            <option value="hard">Hard — All + Combos</option>
          </select>
        </div>
      </div>
      <button class="btn bp wf" onclick="goRated()">🔍 Find Opponent</button>
    </div>

    <div id="s3-friends" class="card glow" style="max-width:460px;margin:24px auto;display:none;">
      <div class="row aic gap3 mb4">
        <button onclick="go('s2','s3-friends')" class="btn bg bxs">← Back</button>
        <h2 class="bold" style="font-size:1.05rem;">👥 Friends Room</h2>
      </div>
      <div class="g2 gap3">
        <button class="btn bp wf" style="flex-direction:column;gap:4px;padding:18px;height:auto;" onclick="createRoom()">
          <span style="font-size:1.3rem;">➕</span><span>Create Room</span>
        </button>
        <div style="display:flex;flex-direction:column;gap:8px;">
          <input id="jcode" class="inp" placeholder="6-digit code" maxlength="6"
            style="text-align:center;font-size:1.2rem;letter-spacing:8px;font-weight:700;">
          <button class="btn bo wf" onclick="joinRoom()">🚪 Join Room</button>
        </div>
      </div>
    </div>

    <div id="s3-solo" class="card glow" style="max-width:460px;margin:24px auto;display:none;">
      <div class="row aic gap3 mb4">
        <button onclick="go('s2','s3-solo')" class="btn bg bxs">← Back</button>
        <h2 class="bold" style="font-size:1.05rem;">🎮 Solo Practice</h2>
      </div>
      <div class="g2 gap3 mb4">
        <div><label class="lbl">Grid Size</label>
          <select id="gs-s" class="inp"><option value="3">3×3</option><option value="4">4×4</option></select>
        </div>
        <div><label class="lbl">Difficulty</label>
          <select id="df-s" class="inp">
            <option value="easy">Easy</option><option value="normal" selected>Normal</option><option value="hard">Hard</option>
          </select>
        </div>
      </div>
      <button class="btn bgrn wf blg" onclick="startSolo()">▶ Start Game</button>
    </div>

    {% endif %}
  </div>

  <div class="g3 gap4 mt4">
    <div class="card tc"><div style="font-size:2rem;margin-bottom:10px;">⚡</div>
      <h3 class="bold mb2">Rated Matches</h3>
      <p class="muted sm">ELO-based ranking with 5 tiers from Beginner to Legend</p>
    </div>
    <div class="card tc"><div style="font-size:2rem;margin-bottom:10px;">📅</div>
      <h3 class="bold mb2">Daily Challenge</h3>
      <p class="muted sm">One board for all players every day. Who's fastest?</p>
    </div>
    <div class="card tc"><div style="font-size:2rem;margin-bottom:10px;">🏆</div>
      <h3 class="bold mb2">Season Rankings</h3>
      <p class="muted sm">90-day seasons with badges and reward history</p>
    </div>
  </div>
</div>

<script>
let selSrc=null;
function pickSrc(s){selSrc=s;go('s2','s1');document.getElementById('s2t').textContent=s==='overall'?'🌍 Overall Mode':'🏆 IPL 2026 Mode';}
function pickMode(m){['rated','friends','solo'].forEach(x=>hide('s3-'+x));go('s3-'+m,'s2');}
function go(show_id,hide_id){hide(hide_id);show(show_id);}
function show(id){document.getElementById(id).style.display='';}
function hide(id){document.getElementById(id).style.display='none';}
function goRated(){const gs=document.getElementById('gs-r').value,df=document.getElementById('df-r').value;window.location.href=`/matchmaking?data_source=${selSrc}&grid_size=${gs}&difficulty=${df}`;}
function startSolo(){const gs=document.getElementById('gs-s').value,df=document.getElementById('df-s').value;window.location.href=`/play?data_source=${selSrc}&grid_size=${gs}&difficulty=${df}&mode=solo`;}
function createRoom(){fetch('/api/create_room',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data_source:selSrc})}).then(r=>r.json()).then(d=>{if(d.code)window.location.href='/room/'+d.code;});}
function joinRoom(){const c=document.getElementById('jcode').value.trim();if(c.length===6)window.location.href='/room/'+c;else toast('Enter a valid 6-digit code','warn');}
</script>
"""

GAME_BODY = """
<div class="ctr page" style="max-width:700px;">
  <div class="g3 gap3 mb4">
    <div class="stat card-sm tc"><div class="stat-l">Score</div><div class="stat-n ca" id="sc">0</div></div>
    <div class="stat card-sm tc"><div class="stat-l">Players Left</div><div class="stat-n" id="pl">{{ total_players }}</div></div>
    <div class="stat card-sm tc"><div class="stat-l">Accuracy</div><div class="stat-n cg" id="ac">—</div></div>
  </div>

  <div class="tbar-wrap mb2"><div id="tb" class="tbar" style="width:100%;background:var(--grn);"></div></div>
  <div class="row jcb xs muted mb4"><span id="tt">30s</span><span>{{ mode_label }}</span></div>

  <div class="pcard mb4">
    <div class="xs muted mb1" id="ps">Loading…</div>
    <div class="pname grad-txt" id="pn">—</div>
  </div>

  <div class="bgrid s{{ grid_size }}" id="grid">
    {% for cell in grid %}
    <div class="cell" id="c{{ loop.index0 }}" onclick="clickCell({{ loop.index0 }})">
      <span>{{ cell.value }}</span>
    </div>
    {% endfor %}
  </div>

  <div class="row gap3 mt4 jcc" style="flex-wrap:wrap;">
    <button id="skip-btn" class="btn bg" onclick="doSkip()">⏭ Skip (3)</button>
    <button id="wc-btn" class="btn bg" style="color:var(--yel);" onclick="doWildcard()">🃏 Wildcard</button>
    <button class="btn bg cm" onclick="quitGame()">🏳 Quit</button>
  </div>

  {% if opponent %}
  <div class="card mt4">
    <div class="row jcb aic mb2">
      <span class="sm muted">vs <strong style="color:var(--txt);">{{ opponent }}</strong></span>
      <span class="sm">Score: <strong id="os">0</strong></span>
    </div>
    <div class="prog-wrap"><div id="ob" class="prog-bar" style="width:0%;background:var(--red);"></div></div>
  </div>
  {% endif %}
</div>

<div id="emod" class="modal-bg" style="display:none;">
  <div class="modal tc">
    <div style="font-size:3rem;margin-bottom:12px;" id="ee">🎯</div>
    <h2 class="black mb2" style="font-size:1.5rem;" id="et">Game Over</h2>
    <div class="black grad-txt" style="font-size:3rem;margin:16px 0;" id="es">0</div>
    <p class="muted sm mb3" id="ed"></p>
    <div id="er" style="font-size:1.1rem;margin-bottom:20px;font-weight:700;"></div>
    <div class="g2 gap3">
      <a href="/" class="btn bo wf">🏠 Home</a>
      <button class="btn bp wf" onclick="location.href='/'">🔄 Play Again</button>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const G={
  room:{{ room_code|tojson }},mode:{{ game_mode|tojson }},ds:{{ data_source|tojson }},
  gs:{{ grid_size }},players:{{ players|tojson }},idx:0,
  gstate:new Array({{ grid_size*grid_size }}).fill(null),
  correct:0,wrong:0,skips:3,wcUsed:false,
  t0:Date.now(),tsec:30,tleft:30,tint:null,ended:false,clickable:true
};
const io_sock=io();
if(G.room){io_sock.emit('join_room',{room:G.room});io_sock.on('opponent_move',d=>updOpp(d.score));}

function refresh(){
  document.getElementById('pl').textContent=G.players.length-G.idx;
  const a=G.correct+G.wrong;
  document.getElementById('ac').textContent=a>0?Math.round(G.correct/a*100)+'%':'—';
}
function showP(){
  if(G.idx>=G.players.length){end('no_players');return;}
  const p=G.players[G.idx];
  document.getElementById('pn').textContent=p.name;
  document.getElementById('ps').textContent=`Player ${G.idx+1} of ${G.players.length}`;
  startT();refresh();
}
function startT(){
  clearInterval(G.tint);G.tleft=G.tsec;G.clickable=true;tickT();
  G.tint=setInterval(()=>{G.tleft--;tickT();if(G.tleft<=0){clearInterval(G.tint);timeUp();}},1000);
}
function tickT(){
  const p=G.tleft/G.tsec*100,bar=document.getElementById('tb');
  bar.style.width=p+'%';
  bar.style.background=p>50?'var(--grn)':p>25?'var(--yel)':'var(--red)';
  document.getElementById('tt').textContent=G.tleft+'s';
}
function timeUp(){G.wrong++;G.idx++;toast('⏰ Time\'s up!','warn');showP();}

function clickCell(i){
  if(!G.clickable||G.ended||G.gstate[i]!==null)return;
  if(G.idx>=G.players.length)return;
  G.clickable=false;clearInterval(G.tint);
  const p=G.players[G.idx];
  fetch('/api/validate_move',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({player_id:p.id,cell_idx:i,data_source:G.ds,room_code:G.room,mode:G.mode})
  }).then(r=>r.json()).then(res=>{
    const el=document.getElementById('c'+i);
    if(res.correct){
      G.correct++;G.gstate[i]=p.name;el.classList.add('filled');
      el.querySelector('span').textContent=p.name;updScore();toast('✅ Correct!','success');
    } else {
      G.wrong++;el.classList.add('wrong');setTimeout(()=>el.classList.remove('wrong'),500);toast('❌ Wrong!','error');
    }
    G.idx++;
    if(G.room)io_sock.emit('player_move',{room:G.room,score:calcScore()});
    if(G.gstate.every(x=>x!==null))end('grid_complete');else setTimeout(showP,300);
  });
}

function calcScore(){
  const el=(Date.now()-G.t0)/1000,n=G.gs**2,a=G.correct+G.wrong,acc=a>0?G.correct/a*100:0;
  return Math.max(0,Math.round(G.correct*100+acc*2+(G.gstate.every(x=>x!==null)?200:0)-Math.max(0,(el-n*15)*.5)));
}
function updScore(){document.getElementById('sc').textContent=calcScore();}
function updOpp(s){const e=document.getElementById('os');if(e){e.textContent=s;const b=document.getElementById('ob');if(b)b.style.width=Math.min(100,s/2000*100)+'%';}}

function doSkip(){
  if(G.skips<=0||G.ended)return;
  G.skips--;G.wrong++;G.idx++;clearInterval(G.tint);
  document.getElementById('skip-btn').textContent=`⏭ Skip (${G.skips})`;
  if(G.skips===0)document.getElementById('skip-btn').disabled=true;
  toast(`⏭ Skipped (${G.skips} left)`,'info');showP();
}
function doWildcard(){
  if(G.wcUsed||G.ended||G.idx>=G.players.length)return;
  G.wcUsed=true;document.getElementById('wc-btn').disabled=true;document.getElementById('wc-btn').textContent='🃏 Used';
  const p=G.players[G.idx];
  fetch('/api/wildcard_hint',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({player_id:p.id,data_source:G.ds,room_code:G.room})
  }).then(r=>r.json()).then(d=>{
    if(d.matching_cells)d.matching_cells.forEach(i=>{if(G.gstate[i]===null)document.getElementById('c'+i).classList.add('hint');});
    toast('🃏 Matching cells highlighted!','info');
  });
}
function quitGame(){if(confirm('Quit? Counts as a loss in rated matches.'))end('quit');}

function end(reason){
  if(G.ended)return;G.ended=true;clearInterval(G.tint);
  const el=Math.round((Date.now()-G.t0)/1000),score=calcScore(),a=G.correct+G.wrong,acc=a>0?Math.round(G.correct/a*100):0;
  fetch('/api/end_game',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({room_code:G.room,mode:G.mode,data_source:G.ds,score,correct:G.correct,wrong:G.wrong,elapsed:el,accuracy:acc,reason})
  }).then(r=>r.json()).then(d=>{
    const done=G.gstate.every(x=>x!==null);
    document.getElementById('ee').textContent=done?'🏆':'🎯';
    document.getElementById('et').textContent=done?'Grid Complete!':'Game Over';
    document.getElementById('es').textContent=score;
    document.getElementById('ed').textContent=`Accuracy: ${acc}%  •  Time: ${el}s  •  Correct: ${G.correct}/${a}`;
    if(d.rating_change&&d.rating_change!==0){
      const rc=d.rating_change;
      document.getElementById('er').innerHTML=`<span style="color:${rc>0?'var(--grn)':'var(--red)'}">${rc>0?'+':''}${rc} Rating</span>`;
    }
    document.getElementById('emod').style.display='flex';
  });
}
showP();
</script>
"""

MATCHMAKING_BODY = """
<div class="ctr page tc">
  <div class="card glow" style="max-width:400px;margin:80px auto;padding:40px;">
    <div style="font-size:3rem;margin-bottom:20px;">🔍</div>
    <h2 class="bold mb2">Finding Opponent…</h2>
    <p class="muted sm mb6" id="smsg">Searching for players with similar rating…</p>
    <div class="tbar-wrap mb4" style="height:8px;">
      <div id="sbar" class="tbar" style="width:0%;transition:width 30s linear;"></div>
    </div>
    <p class="xs muted" id="etxt">0s elapsed</p>
    <button class="btn bo mt6" onclick="cancel()">Cancel</button>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const sock=io();
const ds={{ data_source|tojson }},gs={{ grid_size }},diff={{ difficulty|tojson }};
let el=0;
sock.emit('join_matchmaking',{data_source:ds,grid_size:gs,difficulty:diff});
sock.on('match_found',d=>{window.location.href='/room/'+d.room_code;});
sock.on('matchmaking_status',d=>{document.getElementById('smsg').textContent=d.message;});
setTimeout(()=>{document.getElementById('sbar').style.width='100%';},100);
const t=setInterval(()=>{el++;document.getElementById('etxt').textContent=el+'s elapsed';},1000);
setTimeout(()=>{clearInterval(t);document.getElementById('smsg').textContent='No opponent found. Starting solo…';
  setTimeout(()=>{window.location.href=`/play?data_source=${ds}&grid_size=${gs}&difficulty=${diff}&mode=solo`;},1500);},30000);
function cancel(){sock.emit('leave_matchmaking');window.location.href='/';}
</script>
"""

ROOM_BODY = """
<div class="ctr page tc">
  <div class="card glow" style="max-width:460px;margin:60px auto;">
    <h2 class="bold mb1">Friends Room</h2>
    <p class="muted sm mb4">Share this code with your friend</p>
    <div class="room-code">{{ room_code }}</div>
    <p class="xs muted mb6">6-digit code • expires when game starts</p>
    <div id="plist" class="row gap3 jcc mb6" style="flex-wrap:wrap;"></div>
    <div id="wmsg" class="muted sm" style="animation:pulse2 1.5s ease infinite;">⏳ Waiting for friend to join…</div>
    <div id="ssec" style="display:none;">
      {% if is_host %}
      <div class="g2 gap3 mb4">
        <div><label class="lbl">Grid Size</label><select id="rgs" class="inp"><option value="3">3×3</option><option value="4">4×4</option></select></div>
        <div><label class="lbl">Difficulty</label><select id="rdf" class="inp"><option value="easy">Easy</option><option value="normal" selected>Normal</option><option value="hard">Hard</option></select></div>
      </div>
      <button class="btn bp wf blg" onclick="startR()">▶ Start Game</button>
      {% else %}
      <p class="cg bold">✅ Ready! Waiting for host to start…</p>
      {% endif %}
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const sock=io(),room={{ room_code|tojson }},isHost={{ 'true' if is_host else 'false' }},ds={{ data_source|tojson }};
sock.emit('join_room',{room});
sock.on('room_update',d=>{
  document.getElementById('plist').innerHTML=d.players.map(p=>`<div class="badge" style="color:var(--acc);background:rgba(108,99,255,.1);padding:8px 16px;font-size:.85rem;">👤 ${p}</div>`).join('');
  if(d.players.length>=2){document.getElementById('wmsg').style.display='none';document.getElementById('ssec').style.display='';}
});
sock.on('game_start',d=>{window.location.href='/play?room_code='+d.room_code+'&mode=friends';});
function startR(){
  const gs=document.getElementById('rgs').value,df=document.getElementById('rdf').value;
  sock.emit('start_room_game',{room,data_source:ds,grid_size:parseInt(gs),difficulty:df});
}
</script>
"""

LEADERBOARD_BODY = """
<div class="ctr page">
  <div class="row jcb aic mb6" style="flex-wrap:wrap;gap:12px;">
    <div>
      <h1 class="black grad-txt" style="font-size:2rem;">🏆 Leaderboard</h1>
      <p class="muted sm mt2">{{ season.name }} · Ends {{ season.end_date }}</p>
    </div>
  </div>
  <div class="card">
    <table>
      <thead><tr><th>#</th><th>Player</th><th>Tier</th><th>Rating</th><th>W / L</th><th class="hsm">Win Rate</th></tr></thead>
      <tbody>
        {% for r in rows %}
        <tr>
          <td>{% if loop.index==1 %}<span style="color:#FFD700;font-weight:900;">🥇</span>
              {% elif loop.index==2 %}<span style="color:#C0C0C0;font-weight:900;">🥈</span>
              {% elif loop.index==3 %}<span style="color:#CD7F32;font-weight:900;">🥉</span>
              {% else %}<span class="muted">{{ loop.index }}</span>{% endif %}</td>
          <td>
            <a href="/profile/{{ r.user_id }}" style="font-weight:600;color:var(--txt);text-decoration:none;">{{ r.name }}</a>
            {% if loop.index==1 %} 🏆{% elif loop.index<=10 %} 🥇{% elif loop.index<=100 %} 🎖{% endif %}
          </td>
          <td><span class="badge" style="color:{{ r.tier_color }};">{{ r.tier_icon }} {{ r.tier }}</span></td>
          <td class="bold ca">{{ r.rating|int }}</td>
          <td><span class="cg">{{ r.wins }}</span> / <span class="cr">{{ r.losses }}</span></td>
          <td class="hsm muted">{{ r.win_rate }}%</td>
        </tr>
        {% endfor %}
        {% if not rows %}
        <tr><td colspan="6" class="tc muted" style="padding:48px;">No ranked players yet. Be the first! 🚀</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</div>
"""

PROFILE_BODY = """
<div class="ctr page">
  <div class="card mb6">
    <div class="row aic gap4">
      <img src="{{ profile_user.avatar or '' }}"
        style="width:72px;height:72px;border-radius:50%;border:3px solid var(--acc);object-fit:cover;"
        onerror="this.src='https://ui-avatars.com/api/?name={{ profile_user.name }}&background=6C63FF&color=fff&size=72'">
      <div>
        <h1 class="black" style="font-size:1.8rem;letter-spacing:-1px;">{{ profile_user.name }}</h1>
        <div class="row aic gap2 mt2">
          <span class="badge" style="color:{{ tier_color }};">{{ tier_icon }} {{ tier }}</span>
          <span class="muted sm">{{ rating|int }} Rating</span>
        </div>
      </div>
    </div>
  </div>

  <div class="g3 gap4 mb6">
    <div class="stat"><div class="stat-n ca">{{ stats.total_games }}</div><div class="stat-l">Total Games</div></div>
    <div class="stat"><div class="stat-n cg">{{ stats.wins }}/{{ stats.losses }}</div><div class="stat-l">W / L</div></div>
    <div class="stat"><div class="stat-n cc">{{ stats.win_rate }}%</div><div class="stat-l">Win Rate</div></div>
    <div class="stat"><div class="stat-n cy">{{ stats.avg_accuracy }}%</div><div class="stat-l">Avg Accuracy</div></div>
    <div class="stat"><div class="stat-n cr">{{ stats.best_streak }}</div><div class="stat-l">Best Streak</div></div>
    <div class="stat"><div class="stat-n cm">{{ stats.avg_time }}s</div><div class="stat-l">Avg Time</div></div>
  </div>

  <div class="card">
    <h2 class="bold mb4">Recent Matches</h2>
    <table>
      <thead><tr><th>Result</th><th>Score</th><th class="hsm">Opponent</th><th class="hsm">Δ Rating</th><th>Mode</th><th class="hsm">Date</th></tr></thead>
      <tbody>
        {% for m in matches %}
        <tr>
          <td>{% if m.won %}<span class="cg bold">WIN</span>{% elif m.won==False %}<span class="cr bold">LOSS</span>{% else %}<span class="muted">—</span>{% endif %}</td>
          <td class="bold">{{ m.score|int }}</td>
          <td class="hsm muted">{{ m.opponent or '—' }}</td>
          <td class="hsm">{% if m.rating_change>0 %}<span class="cg">+{{ m.rating_change|int }}</span>{% elif m.rating_change<0 %}<span class="cr">{{ m.rating_change|int }}</span>{% else %}<span class="muted">—</span>{% endif %}</td>
          <td><span class="badge muted" style="font-size:.7rem;">{{ m.mode }}</span></td>
          <td class="hsm muted xs">{{ m.played_at[:10] }}</td>
        </tr>
        {% endfor %}
        {% if not matches %}<tr><td colspan="6" class="tc muted" style="padding:32px;">No matches yet.</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
</div>
"""

DAILY_BODY = """
<div class="ctr page">
  <div class="row jcb aic mb6" style="flex-wrap:wrap;gap:12px;">
    <div>
      <h1 class="black grad-txt" style="font-size:2rem;">📅 Daily Challenge</h1>
      <p class="muted sm mt2">{{ today }} · Same board for everyone. Compete for fastest time!</p>
    </div>
    {% if not already_played %}
    <a href="/play?mode=daily&data_source=overall&grid_size=3&difficulty=normal" class="btn bp">▶ Play Today</a>
    {% else %}
    <span class="badge cg" style="padding:8px 16px;font-size:.85rem;">✅ Played Today</span>
    {% endif %}
  </div>
  <div class="card">
    <h2 class="bold mb4">Today's Leaderboard</h2>
    <table>
      <thead><tr><th>#</th><th>Player</th><th>Score</th><th>Accuracy</th><th>Time</th></tr></thead>
      <tbody>
        {% for r in rows %}
        <tr>
          <td>{% if loop.index==1 %}🥇{% elif loop.index==2 %}🥈{% elif loop.index==3 %}🥉{% else %}<span class="muted">{{ loop.index }}</span>{% endif %}</td>
          <td><a href="/profile/{{ r.user_id }}" style="font-weight:600;color:var(--txt);text-decoration:none;">{{ r.name }}</a></td>
          <td class="bold ca">{{ r.score|int }}</td>
          <td class="cg">{{ r.accuracy|int }}%</td>
          <td class="muted">{{ r.completion_time|int }}s</td>
        </tr>
        {% endfor %}
        {% if not rows %}<tr><td colspan="5" class="tc muted" style="padding:40px;">Be the first to play today! 🚀</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
</div>
"""

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return render_template_string(page(HOME_BODY, "Cricket Bingo"))

@app.route("/oauth_callback")
def oauth_callback():
    if not google.authorized: return redirect(url_for("google.login"))
    try:
        resp = google.get("/oauth2/v2/userinfo")
        if not resp.ok: return redirect("/")
        info = resp.json()
        gid  = info["id"]; email=info.get("email","")
        name = info.get("name", email.split("@")[0]); avatar=info.get("picture","")
        db   = get_db()
        if db.execute("SELECT id FROM users WHERE google_id=?", (gid,)).fetchone():
            db.execute("UPDATE users SET email=?,name=?,avatar=? WHERE google_id=?", (email,name,avatar,gid))
        else:
            db.execute("INSERT INTO users(google_id,email,name,avatar) VALUES(?,?,?,?)", (gid,email,name,avatar))
        db.commit()
        u = User(db.execute("SELECT * FROM users WHERE google_id=?", (gid,)).fetchone())
        login_user(u)
        s = get_current_season()
        if s: ensure_season_rating(u.id, s["id"])
    except Exception as e:
        app.logger.error(f"OAuth: {e}")
    return redirect("/")

@app.route("/logout")
@login_required
def logout():
    logout_user(); session.clear(); return redirect("/")

@app.route("/play")
@login_required
def play():
    game_mode = request.args.get("mode", "solo")
    ds        = request.args.get("data_source", "overall")
    grid_size = int(request.args.get("grid_size", 3))
    difficulty= request.args.get("difficulty", "normal")
    room_code = request.args.get("room_code", None)

    if game_mode == "daily":
        state = get_or_create_daily()
    elif room_code:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if not row: return redirect("/")
        state     = json.loads(row["game_state"])
        game_mode = row["mode"]
        ds        = state.get("data_source", "overall")
        grid_size = state.get("grid_size", 3)
    else:
        state = create_game_state(ds, grid_size, difficulty)

    if not state:
        return "Error: No player data. Ensure overall.json and ipl26.json exist.", 500

    session["game_state"] = {"state":state,"room_code":room_code,"mode":game_mode,"data_source":ds}

    mode_labels = {"solo":"Solo Practice","rated":"⚡ Rated","friends":"👥 Friends","daily":"📅 Daily"}

    opponent = None
    if room_code:
        row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
        if row:
            oid = row["player2_id"] if row["player1_id"]==current_user.id else row["player1_id"]
            if oid:
                ou = query_db("SELECT name FROM users WHERE id=?", (oid,), one=True)
                if ou: opponent = ou["name"]

    # NOTE: We pass data_source and game_mode (NOT 'source' or 'mode') to avoid
    # collision with render_template_string's own 'source' parameter.
    return render_template_string(
        page(GAME_BODY, "Cricket Bingo — Game"),
        grid          = state["grid"],
        players       = state["players"],
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
    ds        = request.args.get("data_source","overall")
    grid_size = int(request.args.get("grid_size",3))
    difficulty= request.args.get("difficulty","normal")
    return render_template_string(
        page(MATCHMAKING_BODY,"Finding Match…"),
        data_source=ds, grid_size=grid_size, difficulty=difficulty)

@app.route("/room/<room_code>")
@login_required
def room(room_code):
    row = query_db("SELECT * FROM active_games WHERE room_code=?", (room_code,), one=True)
    if not row: return redirect("/")
    is_host = row["player1_id"]==current_user.id
    state   = json.loads(row["game_state"])
    ds      = state.get("data_source","overall")
    if row["status"]=="active":
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
        return render_template_string(page(LEADERBOARD_BODY,"Leaderboard"),
            season={"name":"No Season","end_date":"—"}, rows=[])
    raw = query_db("""SELECT sr.user_id,sr.rating,sr.wins,sr.losses,sr.total_games,u.name
        FROM season_ratings sr JOIN users u ON u.id=sr.user_id
        WHERE sr.season_id=? ORDER BY sr.rating DESC LIMIT 100""", (season["id"],))
    rows=[]
    for r in raw:
        t,tc,ti=rating_tier(r["rating"])
        wr=round(r["wins"]/r["total_games"]*100) if r["total_games"]>0 else 0
        rows.append({"user_id":r["user_id"],"name":r["name"],"rating":r["rating"],
                     "wins":r["wins"],"losses":r["losses"],"tier":t,"tier_color":tc,"tier_icon":ti,"win_rate":wr})
    return render_template_string(page(LEADERBOARD_BODY,"Leaderboard"), season=season, rows=rows)

@app.route("/profile/<int:user_id>")
def profile(user_id):
    ur = query_db("SELECT * FROM users WHERE id=?", (user_id,), one=True)
    if not ur: return "User not found",404
    season=get_current_season(); rating=1200.0; tier="Beginner"; tier_color="#6B7280"; tier_icon="🟤"; sr=None
    if season:
        sr=query_db("SELECT * FROM season_ratings WHERE user_id=? AND season_id=?",(user_id,season["id"]),one=True)
        if sr: rating=sr["rating"]; tier,tier_color,tier_icon=rating_tier(rating)
    stats={
        "total_games":sr["total_games"] if sr else 0,
        "wins":sr["wins"] if sr else 0,"losses":sr["losses"] if sr else 0,
        "win_rate":round(sr["wins"]/sr["total_games"]*100) if sr and sr["total_games"]>0 else 0,
        "best_streak":sr["best_streak"] if sr else 0,
        "avg_accuracy":round(sr["accuracy_sum"]/sr["total_games"]) if sr and sr["total_games"]>0 else 0,
        "avg_time":round(sr["time_sum"]/sr["total_games"]) if sr and sr["total_games"]>0 else 0,
    }
    raw=query_db("""SELECT m.*,u1.name as p1name,u2.name as p2name FROM matches m
        LEFT JOIN users u1 ON u1.id=m.player1_id LEFT JOIN users u2 ON u2.id=m.player2_id
        WHERE m.player1_id=? OR m.player2_id=? ORDER BY m.played_at DESC LIMIT 10""",(user_id,user_id))
    matches=[]
    for m in raw:
        ip1=m["player1_id"]==user_id
        score=m["player1_score"] if ip1 else m["player2_score"]
        opp=m["p2name"] if ip1 else m["p1name"]
        won=None
        if m["winner_id"]==user_id: won=True
        elif m["winner_id"] is not None: won=False
        rc=m["rating_change"] if ip1 else -m["rating_change"]
        matches.append({"won":won,"score":score,"opponent":opp,"rating_change":rc,"mode":m["mode"],"played_at":m["played_at"]})
    return render_template_string(page(PROFILE_BODY, ur["name"]),
        profile_user=ur,tier=tier,tier_color=tier_color,tier_icon=tier_icon,
        rating=rating,stats=stats,matches=matches)

@app.route("/daily")
def daily():
    today=date.today().isoformat()
    raw=query_db("""SELECT dr.user_id,dr.score,dr.completion_time,dr.accuracy,u.name
        FROM daily_results dr JOIN users u ON u.id=dr.user_id
        WHERE dr.challenge_date=? ORDER BY dr.score DESC,dr.completion_time ASC LIMIT 50""",(today,))
    played=False
    if current_user.is_authenticated:
        played=query_db("SELECT id FROM daily_results WHERE user_id=? AND challenge_date=?",
                        (current_user.id,today),one=True) is not None
    return render_template_string(page(DAILY_BODY,"Daily Challenge"),
        today=today,rows=[dict(r) for r in raw],already_played=played)

# ─── API ──────────────────────────────────────────────────────────────────────
@app.route("/api/create_room", methods=["POST"])
@login_required
def api_create_room():
    data=request.get_json(force=True)
    ds=data.get("data_source","overall")
    code=gen_room_code()
    init={"data_source":ds,"grid_size":3,"difficulty":"normal","grid":[],"players":[]}
    query_db("INSERT INTO active_games(room_code,player1_id,game_state,mode) VALUES(?,?,?,?)",
             (code,current_user.id,json.dumps(init),"friends"),commit=True)
    return jsonify({"code":code})

@app.route("/api/validate_move", methods=["POST"])
@login_required
def api_validate_move():
    data=request.get_json(force=True)
    pid=data.get("player_id"); cidx=data.get("cell_idx"); ds=data.get("data_source","overall")
    gi=session.get("game_state")
    if not gi: return jsonify({"correct":False,"error":"no_game"})
    state=gi.get("state",{}); grid=state.get("grid",[])
    if cidx is None or cidx>=len(grid): return jsonify({"correct":False})
    if state.get("grid_state",[None]*len(grid))[cidx] is not None:
        return jsonify({"correct":False,"reason":"filled"})
    pool=get_pool(ds)
    player=next((p for p in pool if p["id"]==pid),None)
    if not player: return jsonify({"correct":False})
    return jsonify({"correct":player_matches_cell(player,grid[cidx],ds)})

@app.route("/api/wildcard_hint", methods=["POST"])
@login_required
def api_wildcard_hint():
    data=request.get_json(force=True)
    pid=data.get("player_id"); ds=data.get("data_source","overall")
    gi=session.get("game_state")
    if not gi: return jsonify({"matching_cells":[]})
    state=gi.get("state",{}); grid=state.get("grid",[]); gstate=state.get("grid_state",[])
    pool=get_pool(ds)
    player=next((p for p in pool if p["id"]==pid),None)
    if not player: return jsonify({"matching_cells":[]})
    return jsonify({"matching_cells":[i for i,c in enumerate(grid) if gstate[i] is None and player_matches_cell(player,c,ds)]})

@app.route("/api/end_game", methods=["POST"])
@login_required
def api_end_game():
    data=request.get_json(force=True)
    gmode=data.get("mode","solo"); ds=data.get("data_source","overall")
    score=float(data.get("score",0)); elapsed=float(data.get("elapsed",0))
    accuracy=float(data.get("accuracy",0)); room_code=data.get("room_code")
    result={"rating_change":0}; season=get_current_season()

    if gmode=="daily":
        today=date.today().isoformat()
        try:
            query_db("INSERT OR IGNORE INTO daily_results(user_id,challenge_date,score,completion_time,accuracy) VALUES(?,?,?,?,?)",
                     (current_user.id,today,score,elapsed,accuracy),commit=True)
        except: pass

    elif gmode=="rated" and room_code and season:
        row=query_db("SELECT * FROM active_games WHERE room_code=?",(room_code,),one=True)
        if row and row["status"]!="finished":
            state=json.loads(row["game_state"]); results=state.get("results",{})
            results[str(current_user.id)]={"score":score,"elapsed":elapsed,"accuracy":accuracy}
            state["results"]=results
            if len(results)>=2:
                p1,p2=row["player1_id"],row["player2_id"]
                r1=results.get(str(p1),{"score":0,"elapsed":9999})
                r2=results.get(str(p2),{"score":0,"elapsed":9999})
                winner=p1 if r1["score"]>r2["score"] or (r1["score"]==r2["score"] and r1["elapsed"]<=r2["elapsed"]) else p2
                rat1=get_user_rating(p1,season["id"]); rat2=get_user_rating(p2,season["id"])
                exp1=elo_expected(rat1,rat2); act1=1.0 if winner==p1 else 0.0
                new1=elo_update(rat1,exp1,act1); new2=elo_update(rat2,1-exp1,1-act1)
                delta=round(new1-rat1,1)
                ensure_season_rating(p1,season["id"]); ensure_season_rating(p2,season["id"])
                for uid,nr,w,rd in [(p1,new1,1 if winner==p1 else 0,r1),(p2,new2,1 if winner==p2 else 0,r2)]:
                    query_db("""UPDATE season_ratings SET rating=?,wins=wins+?,losses=losses+?,
                        total_games=total_games+1,accuracy_sum=accuracy_sum+?,time_sum=time_sum+?,
                        win_streak=CASE WHEN ?=1 THEN win_streak+1 ELSE 0 END,
                        best_streak=MAX(best_streak,CASE WHEN ?=1 THEN win_streak+1 ELSE best_streak END)
                        WHERE user_id=? AND season_id=?""",
                        (nr,w,1-w,rd.get("accuracy",0),rd.get("elapsed",0),w,w,uid,season["id"]),commit=True)
                query_db("""INSERT INTO matches(player1_id,player2_id,winner_id,
                    player1_score,player2_score,player1_time,player2_time,
                    player1_accuracy,player2_accuracy,rating_change,mode,season_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p1,p2,winner,r1["score"],r2["score"],r1["elapsed"],r2["elapsed"],
                     r1.get("accuracy",0),r2.get("accuracy",0),abs(delta),"rated",season["id"]),commit=True)
                query_db("UPDATE active_games SET status='finished',game_state=? WHERE room_code=?",
                         (json.dumps(state),room_code),commit=True)
                result["rating_change"]=delta if current_user.id==p1 else -delta
                result["winner"]=winner==current_user.id
            else:
                query_db("UPDATE active_games SET game_state=? WHERE room_code=?",
                         (json.dumps(state),room_code),commit=True)
    elif season:
        ensure_season_rating(current_user.id,season["id"])
        query_db("UPDATE season_ratings SET total_games=total_games+1,accuracy_sum=accuracy_sum+?,time_sum=time_sum+? WHERE user_id=? AND season_id=?",
                 (accuracy,elapsed,current_user.id,season["id"]),commit=True)
    session.pop("game_state",None)
    return jsonify(result)

# ─── SOCKETIO ─────────────────────────────────────────────────────────────────
@socketio.on("join_room")
def on_join(data):
    rm=data.get("room")
    if not rm: return
    join_room(rm)
    row=query_db("SELECT * FROM active_games WHERE room_code=?",(rm,),one=True)
    if row:
        players=[]
        for uid in [row["player1_id"],row["player2_id"]]:
            if uid:
                u=query_db("SELECT name FROM users WHERE id=?",(uid,),one=True)
                if u: players.append(u["name"])
        emit("room_update",{"players":players},to=rm)

@socketio.on("player_move")
def on_move(data):
    rm=data.get("room")
    if rm: emit("opponent_move",data,to=rm,include_self=False)

@socketio.on("join_matchmaking")
def on_queue(data):
    if not current_user.is_authenticated: return
    ds=data.get("data_source","overall"); gs=data.get("grid_size",3); diff=data.get("difficulty","normal")
    s=get_current_season(); rat=get_user_rating(current_user.id,s["id"]) if s else 1200.0
    query_db("INSERT OR REPLACE INTO matchmaking_queue(user_id,rating,data_source,grid_size,difficulty) VALUES(?,?,?,?,?)",
             (current_user.id,rat,ds,gs,diff),commit=True)
    cands=query_db("""SELECT * FROM matchmaking_queue WHERE user_id!=? AND data_source=?
        AND grid_size=? AND difficulty=? AND ABS(rating-?)<=300 ORDER BY ABS(rating-?) ASC LIMIT 1""",
        (current_user.id,ds,gs,diff,rat,rat))
    if cands:
        opp=cands[0]
        query_db("DELETE FROM matchmaking_queue WHERE user_id IN (?,?)",(current_user.id,opp["user_id"]),commit=True)
        code=gen_room_code(); state=create_game_state(ds,gs,diff)
        query_db("INSERT INTO active_games(room_code,player1_id,player2_id,game_state,mode,status) VALUES(?,?,?,?,?,?)",
                 (code,opp["user_id"],current_user.id,json.dumps(state,default=str),"rated","active"),commit=True)
        emit("match_found",{"room_code":code})
        emit("match_found",{"room_code":code},to=f"queue_{opp['user_id']}")
    else:
        join_room(f"queue_{current_user.id}")
        emit("matchmaking_status",{"message":"Searching for opponent with similar rating…"})

@socketio.on("leave_matchmaking")
def on_leave_q():
    if current_user.is_authenticated:
        query_db("DELETE FROM matchmaking_queue WHERE user_id=?",(current_user.id,),commit=True)

@socketio.on("start_room_game")
def on_start(data):
    rm=data.get("room"); ds=data.get("data_source","overall")
    gs=data.get("grid_size",3); diff=data.get("difficulty","normal")
    row=query_db("SELECT * FROM active_games WHERE room_code=?",(rm,),one=True)
    if not row or row["player1_id"]!=current_user.id: return
    state=create_game_state(ds,gs,diff)
    query_db("UPDATE active_games SET game_state=?,status='active' WHERE room_code=?",
             (json.dumps(state,default=str),rm),commit=True)
    emit("game_start",{"room_code":rm},to=rm)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    init_db()
    port=int(os.getenv("PORT", 5000))
    debug=os.getenv("FLASK_DEBUG", "1") == "1"
    print(f"""
╔══════════════════════════════════════╗
║      🏏  Cricket Bingo  Starting     ║
╠══════════════════════════════════════╣
║  URL  →  http://localhost:{port}       ║
║  DB   →  {DATABASE}        ║
╚══════════════════════════════════════╝""")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)