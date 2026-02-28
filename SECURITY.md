# Security Guide

## Credential Rotation (IMPORTANT)

If your Google OAuth credentials or SECRET_KEY were ever committed to Git or exposed:

1. **Google OAuth**: Go to [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials → Regenerate or create new OAuth 2.0 credentials
2. **SECRET_KEY**: Generate a new one: `python -c "import secrets; print(secrets.token_hex(32))"`
3. Update your local `.env` and Render dashboard with the new values

## Environment Variables

- **Never commit** `.env` or any file containing real credentials
- Use `.env.example` as a template (no real values)
- On Render: set all variables in Dashboard → Environment

## Render Deployment

Set these in Render Dashboard → Environment:

| Variable | Required | Notes |
|----------|----------|-------|
| SECRET_KEY | Yes | Random string for session encryption |
| GOOGLE_CLIENT_ID | Yes | From Google Cloud Console |
| GOOGLE_CLIENT_SECRET | Yes | From Google Cloud Console |
| OAUTHLIB_INSECURE_TRANSPORT | No | Omit or set to 0 (Render uses HTTPS) |
| FLASK_DEBUG | No | Set to 0 for production |
| PORT | No | Render sets automatically |
