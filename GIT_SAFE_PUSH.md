# Safe Git Commands - After Secret Exposure

**⚠️ CRITICAL: Rotate your Google OAuth credentials and SECRET_KEY immediately** (see SECURITY.md). Exposed credentials in Git history remain compromised until rotated.

## Step 1: Remove sensitive files from Git tracking

Run these in Git Bash or PowerShell (with Git in PATH):

```bash
cd c:\Users\patel\cric-bingo

# Stop tracking .env (never commit it)
git rm --cached .env 2>nul

# Stop tracking virtual environments
git rm -r --cached .venv 2>nul
git rm -r --cached venv 2>nul

# Stop tracking Python cache
git rm -r --cached __pycache__ 2>nul
```

## Step 2: Verify .gitignore

Ensure `.gitignore` exists and includes `.env`, `.venv/`, `venv/`, `__pycache__/`. (Already done.)

## Step 3: Commit the security fixes

```bash
git add .gitignore .env.example requirements.txt SECURITY.md app.py
git status   # Verify .env and .venv are NOT staged
git commit -m "Security: Add .gitignore, use env vars, remove secrets from tracking"
```

## Step 4: If GitHub still blocks (secrets in history)

If secrets were previously committed, they exist in Git history. GitHub scans the entire history. You have two options:

### Option A: New repository (simplest)

```bash
# Create a fresh repo without history
rm -rf .git
git init
git add .
git commit -m "Initial commit - secure setup"
git remote add origin <your-new-repo-url>
git push -u origin main
```

### Option B: Rewrite history (advanced)

Use [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) or `git filter-repo` to remove .env and .venv from all commits. This rewrites history and requires force-push.

## Step 5: Push

```bash
git push origin main
```

## Pre-push checklist

- [ ] `.env` is NOT in `git status`
- [ ] `.venv/` and `venv/` are NOT tracked
- [ ] Rotated Google OAuth credentials
- [ ] Rotated SECRET_KEY
- [ ] Local `.env` has new values for development
- [ ] Render dashboard has new env vars for production
