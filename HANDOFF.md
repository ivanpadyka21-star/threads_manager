# SMM Studio — onboarding a new machine

This gets a teammate the **exact same working setup**: the full app + all logged-in
Threads accounts, metrics and settings.

The project splits into two parts:

1. **Code** — lives in this Git repo (what you cloned).
2. **Secrets + data** — never in Git. Delivered separately by the owner as an
   encrypted bundle: `dashboard.db`, `threads_credentials*.json`, `.env`,
   `threads.crt`, `threads.key`. These hold live access tokens and the app
   secret, so they travel out-of-band, not through GitHub.

## Prerequisites
- Python 3.12+
- Git

## 1. Clone + branch
```bash
git clone https://github.com/ivanpadyka21-star/threads_manager.git
cd threads_manager
git checkout feature/mobile-e2e-framework
```

## 2. Python env + deps
```bash
python -m venv .venv
# Windows (Git Bash):  source .venv/Scripts/activate
# macOS/Linux:         source .venv/bin/activate
pip install -r requirements.txt
```
If a module turns out to be missing at runtime, `pip install <name>` it (e.g.
`flask`, `python-dotenv`, `google-generativeai`).

## 3. Drop in the secrets bundle (from the owner)
Decrypt the bundle the owner sent you, then place the files:

| File(s) | Put here |
|---|---|
| `.env` | repo root |
| `threads.crt`, `threads.key` | repo root |
| `threads_credentials*.json` | repo root |
| `dashboard.db` | `mobile_e2e/web/data/dashboard.db` |

To decrypt the bundle (owner shares the password separately):
```bash
openssl enc -d -aes-256-cbc -pbkdf2 -in secrets_bundle.enc -out secrets_bundle.tar.gz
tar -xzf secrets_bundle.tar.gz    # unpacks the files listed above
```
Then move each file to the location in the table.

> These files are already in `.gitignore` — **never commit them.**

## 4. Run
```bash
python -m mobile_e2e.web
# open http://127.0.0.1:3000   (override port with E2E_WEB_PORT)
```
You now have the dashboard with all accounts, metrics and settings, identical to
the owner's machine.

## ⚠️ Important: don't run the SAME accounts from two machines at once
If two people run the bot against the same accounts from two separate copies of
`dashboard.db`, you'll double-post and the stats will diverge. For real teamwork,
move to **one shared server + one database** (ask the owner). Until then, use this
copy for **development / adding features**, not for parallel live posting.

## Adding features
The dashboard is a Flask app in `mobile_e2e/web/` (routes in `app.py`, data in
`store.py`, strategy/brain in `strategy.py`, front-end in `static/`). Tests:
`python -m pytest mobile_e2e -q`.
