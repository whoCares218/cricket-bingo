# Push to GitHub – Safe Commands

Run these in **Git Bash** or a terminal where `git` is in your PATH (e.g. after installing [Git for Windows](https://git-scm.com/download/win)).

Repository: **https://github.com/whoCares218/cricket-bingo**

---

## 1. Remove any previously tracked secret files (if this repo was used before)

```bash
cd C:\Users\patel\cric-bingo

git rm --cached .env 2>/dev/null || true
git rm -r --cached .venv 2>/dev/null || true
git rm -r --cached venv 2>/dev/null || true
git rm -r --cached __pycache__ 2>/dev/null || true
```

## 2. Initialize Git (if not already)

```bash
git init
```

## 3. Add remote

```bash
git remote add origin https://github.com/whoCares218/cricket-bingo.git
```

If you get "remote origin already exists", use:

```bash
git remote set-url origin https://github.com/whoCares218/cricket-bingo.git
```

## 4. Add all safe files (.env is ignored by .gitignore)

```bash
git add .
```

## 5. Verify no secrets are staged

```bash
git status
```

Confirm that **.env** and **.venv** do NOT appear in the list of files to be committed.

## 6. Commit

```bash
git commit -m "Initial secure project upload"
```

## 7. Push to main

```bash
git branch -M main
git push -u origin main
```

---

After pushing, your repo should contain:

- All project source code (`app.py`, `ipl26.json`, `overall.json`, etc.)
- `requirements.txt`
- `README.md`
- `.gitignore`
- `.env.example`
- `SECURITY.md`, `GIT_SAFE_PUSH.md`

**.env is not and will not be in the repository** – it is listed in `.gitignore`.
