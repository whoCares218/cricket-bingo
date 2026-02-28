# Cricket Bingo

A web-based Cricket Bingo game where you match cricket players to their teams, nations, and trophies. Play solo, with friends, or compete in rated online matches.

## Features

- **Rated matches** – Compete online with ELO-style ranking
- **Friends mode** – Share a room code and play together
- **Daily challenge** – One shared board per day
- **Solo practice** – Play at your own pace
- **Google sign-in** – OAuth login

## Tech Stack

- Python, Flask, Flask-Login, Flask-Dance (Google OAuth), Flask-SocketIO, Eventlet, SQLite

## Setup

### 1. Clone and install

```bash
git clone https://github.com/whoCares218/cricket-bingo.git
cd cricket-bingo
pip install -r requirements.txt
```

### 2. Environment variables

Copy the example env file and add your values (never commit `.env`):

```bash
copy .env.example .env   # Windows
# or: cp .env.example .env   # Mac/Linux
```

Edit `.env` and set:

- `SECRET_KEY` – e.g. generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
- `GOOGLE_CLIENT_ID` – from [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials
- `GOOGLE_CLIENT_SECRET` – from the same credentials page
- `OAUTHLIB_INSECURE_TRANSPORT=1` – for local HTTP (omit or set to 0 in production)

### 3. Google OAuth

In Google Cloud Console:

- Create a project and enable relevant APIs
- Create OAuth 2.0 Client ID (Web application)
- Add redirect URI: `http://localhost:5000/login/google/authorized`

### 4. Run locally

```bash
python app.py
```

Open http://localhost:5000

## Deployment (Render)

- Build: `pip install -r requirements.txt`
- Start: `python app.py`
- Set `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` in Render dashboard (no `OAUTHLIB_INSECURE_TRANSPORT` on HTTPS)
- Add redirect URI: `https://your-app.onrender.com/login/google/authorized`

## Security

- Never commit `.env` or any file containing real credentials.
- Use `.env.example` as a template only.
- See `SECURITY.md` for credential rotation and best practices.

## License

Use this project for learning and personal use. Ensure you comply with Google OAuth and any third-party terms.
