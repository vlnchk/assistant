# EduJobs Assistant Bot

[Русский](README.md) | **English**

A Telegram assistant for an education jobs channel. It answers questions about
vacancy and advertising placement, helps clients check and book advertising
slots, creates document requests, and hands conversations over to a human
manager when needed.

The application uses Gemini 3.5 Flash-Lite as a strict intent and tool router,
SQLite for conversation state, Google Sheets for advertising slots and document
tasks, and Telegram forum topics as the manager workspace.

## Technology stack

- **Python 3.12.13** — production runtime defined by the `Dockerfile`.
- **aiogram 3.30.0** — asynchronous Telegram Bot API client.
- **google-genai 2.8.0** — Gemini API and native function calling.
- **SQLAlchemy 2.0.48 + aiosqlite 0.22.1** — asynchronous SQLite access.
- **Alembic 1.16.5** — database migrations.
- **gspread 6.2.1** — Google Sheets integration.
- **Docker and Docker Compose** — production packaging and deployment.

`requirements.txt` is a complete lock file. Direct and transitive dependencies
are pinned to exact versions. After changing dependencies, rebuild the lock file
and repeat the security audit instead of replacing `==` pins with version
ranges.

### Production container security

- The process runs as the unprivileged user `10001:10001`.
- The root filesystem is read-only.
- All Linux capabilities are dropped and `no-new-privileges` is enabled.
- Writable application data is limited to the `./data:/app/data` bind mount.
- Temporary files are limited to a dedicated `/tmp` tmpfs.
- `.env` is read by Docker Compose and is not copied into the image.
- The Google service-account JSON is excluded from the build context and
  mounted read-only.

## Architecture

### Strict LLM dispatcher

The central rule is: **the LLM never writes client-facing prose**. Every
semantic message is sent to Gemini 3.5 Flash-Lite, which must select exactly one
registered tool. The final client response comes from `bot/replies.py`, a
trusted template, or a trusted integration such as Google Sheets.

This design provides:

1. **Consistent responses.** The same business situation produces the same
   approved wording.
2. **Hallucination resistance.** The model cannot invent prices, channel
   statistics, links, or company details in a free-form response.
3. **Prompt-injection resistance.** Telegram identity, contact information, and
   admin-thread links are injected from server-side context in
   `llm/tool_executor.py`, never trusted from model arguments.

Routing and tool execution are separate. The model finishes classification
before any side effect is performed, and an action can run at most once.

Production uses one model only: `gemini-3.5-flash-lite`. There is no automatic
model fallback, second LLM provider, or model cascade. LangChain and LangGraph
are not used.

Transport events that need no semantic classification stay in the Telegram
adapter: `/start` and `/help` receive a deterministic greeting, while media
without a caption is acknowledged and handed to a manager. All ordinary user
text, including gratitude, goes through Gemini.

### Message flow

```text
Client sends a private Telegram message
        ↓
bot/handlers.py: rate limit, length limit, admin-topic state
        ↓
The client message is mirrored to the admin topic
        ↓
Technical commands and uncaptioned media are handled by the transport adapter
        ↓
If the topic is waiting_human or human_mode, LLM routing does not run
        ↓
assistant/engine.py receives a provider-neutral TurnRequest
        ↓
Gemini 3.5 Flash-Lite selects exactly one business tool
        ↓
llm/tool_executor.py validates and executes the tool once
        ↓
bot/handlers.py sends the result to the client and mirrors it to the topic
```

Telegram is only a transport adapter. The same `AssistantEngine` is used by the
local interactive runner and batch evaluations.

### Admin-topic states

Each Telegram user is associated with one forum topic in the private admin
supergroup.

| State | Behaviour |
|---|---|
| `active` | The bot responds automatically. |
| `waiting_human` | A manager has been requested; the bot tells the client that a manager is on the way. |
| `human_mode` | Only the manager responds; the bot stays silent. |

When a manager replies in the topic, the message is delivered to the client and
the topic enters `human_mode`. The `/close` command returns it to `active`.

### Gemini tools

Tool declarations live in `llm/tools.py`. Their docstrings are internal routing
metadata; client-facing Russian text lives in `bot/replies.py`.

| Tool | Purpose |
|---|---|
| `send_greeting` | Ordinary text greetings |
| `reply_gratitude` | Standalone gratitude |
| `reply_bot_nature` | Questions about whether the assistant is a bot |
| `reply_offtopic` | Polite handling of unrelated conversation |
| `faq_free_posting` | Free vacancy placement form |
| `faq_paid_post` | Paid advertising terms |
| `faq_stats` | Reach, ERR, and audience geography |
| `faq_ord` | Advertising labelling and ORD |
| `faq_docs` | Contracts, invoices, EDI, and document workflow |
| `answer_information` | One or more canonical FAQ blocks, optionally including read-only calendar data |
| `ask_ad_topic` | Request the subject of an advertisement |
| `get_free_slots` | Read available dates from Google Sheets |
| `check_dates_availability` | Check one or more requested dates |
| `book_slot` | Book a slot after validating date and advertising topic |
| `get_client_bookings` | Return bookings for the authenticated Telegram user only |
| `create_document_task` | Create an invoice/contract/act task and notify a manager |
| `request_publication_support` | Status, edit, delete, expedite, and related publication operations |
| `request_mutual_pr_support` | Hand mutual-promotion proposals to a manager |
| `handover_to_admin` | Pause automation and request a human manager |

### Changing replies or adding an intent

To change approved client wording, edit the relevant constant in
`bot/replies.py`.

To add an intent:

1. Add the canonical response to `bot/replies.py`.
2. Add a tool with an English routing docstring to `llm/tools.py`.
3. Register it in `TERMINAL_TOOLS`, `bot_tools`, and `TOOL_FUNCTIONS` in
   `llm/tool_executor.py`.
4. Update the system prompt in `llm/gemini_client.py` if the routing rule needs
   explicit clarification.
5. Add unit tests and at least one JSONL eval case.

### Storage and logs

- `db.sqlite3`, or `data/db.sqlite3` in Docker, stores representatives, admin
  topics, message history, bot settings, and clients.
- `/app/data/bot.log` is rotated at 10 MB and keeps five archived files. The
  path is controlled by `BOT_LOG_FILE`.
- The `Календарь (Слоты)` Google Sheets worksheet stores advertising slots.
- The `Задачи (Документы)` worksheet stores document requests.
- A successful document task sends a service notification without pausing the
  client conversation.
- If the saved escalation topic was deleted, the bot creates a replacement,
  stores its ID, and retries the notification once.

## Requirements

- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- A Google Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
- A Google service-account JSON file with access to the required spreadsheet.
- A private Telegram supergroup with forum topics enabled. The bot must be an
  administrator with permission to create topics and pin messages.
- Docker and Docker Compose for server deployment.

## Environment configuration

Create `.env` next to `main.py`:

```ini
# --- Telegram ---
TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
SUPERGROUP_CHAT_ID="-100..."

# --- Database ---
DATABASE_URL="sqlite+aiosqlite:///db.sqlite3"          # local
# DATABASE_URL="sqlite+aiosqlite:///data/db.sqlite3"   # Docker

# --- Google Gemini ---
GEMINI_API_KEY="YOUR_KEY"
# Optional defaults:
# GEMINI_MODEL="gemini-3.5-flash-lite"
# GEMINI_TIMEOUT="10"
# ASSISTANT_METRICS_ENABLED="0"    # 1 = write trace metrics to the log
# GEMINI_LITE_MODEL is retained as a deprecated alias for GEMINI_MODEL.

# --- Google Sheets ---
GOOGLE_SHEETS_CREDENTIALS_FILE="credentials.json"
# The VPS compose file expects ejs-admin-bot-a275ca626752.json
GOOGLE_SHEETS_DOCUMENT_URL="https://docs.google.com/spreadsheets/d/..."

# --- Escalation ---
ADMIN_TELEGRAM_ID="123456789"
# ESCALATION_TOPIC_ID="..."        # optional; the bot can create and store it
```

Never commit `.env` or the service-account JSON. Both are excluded from Git and
the Docker build context. On the VPS they should use restrictive permissions.

```bash
cd /opt/assistant
chown root:root .env
chmod 600 .env
chown 10001:10001 ejs-admin-bot-a275ca626752.json
chown -R 10001:10001 data
chmod 600 ejs-admin-bot-a275ca626752.json
chmod 700 data
chmod 600 data/db.sqlite3
```

## Local run without Docker

```bash
# 1. Virtual environment
python -m venv .venv
source .venv/bin/activate            # macOS/Linux
# .venv\Scripts\activate             # Windows

# 2. Dependencies
pip install -r requirements.txt

# 3. Database migrations
alembic upgrade head

# 4. Start Telegram polling
python main.py
```

## Local testing without Telegram

The interactive runner invokes Gemini for real but uses `DryRunToolExecutor`.
Bookings, document tasks, Google Sheets writes, and Telegram notifications are
simulated rather than executed.

```bash
python scripts/eval_assistant.py --interactive
```

Run the starter JSONL dataset:

```bash
python scripts/eval_assistant.py \
  --dataset tests/fixtures/dialog_cases.jsonl \
  --metrics
```

Results are written to `eval_runs/<timestamp>/results.csv` and `summary.json`.
The report includes route accuracy, errors, tokens, and mean/p50/p95/max
latency. CSV columns `manual_score` and `manual_notes` are available for human
review. Scores use a 1–5 rubric, with 4 or 5 treated as accepted. A turn may
declare semantically equivalent routes through `expected_tools`.

Create anonymized candidates from a Telegram Desktop JSON export:

```bash
python scripts/import_telegram_export.py \
  --input /path/to/result.json \
  --output /tmp/telegram-eval-candidates.jsonl
```

The importer reconstructs forum topics, keeps client messages only, and masks
common identifiers. Its output still requires human review. Never commit the
raw Telegram export or unreviewed candidates.

Current baseline:

| Metric | Result |
|---|---:|
| Dialog cases | 65 |
| Anonymized real dialogs | 55 |
| Evaluated turns | 73 |
| Strictly passed | 71/73 |
| Route accuracy | 97.26% |
| Errors | 0 |
| Manually reviewed | 73/73 |
| Mean manual score | 4.89/5 |
| Manual acceptance | 70/73 (95.89%) |
| Mean latency | 765 ms |
| p50 / p95 / max | 731 / 1,088 / 1,283 ms |
| Total tokens | 403,284 |

## Docker deployment

```bash
docker compose config --quiet
docker compose up -d --build
docker compose logs -f bot
```

The container command automatically runs `alembic upgrade head` before
starting `main.py`.

Verify hardening after startup:

```bash
docker inspect -f 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} security={{json .HostConfig.SecurityOpt}} capdrop={{json .HostConfig.CapDrop}}' edujobs_bot
docker inspect -f '{{range .Mounts}}{{.Destination}} rw={{.RW}}{{println}}{{end}}' edujobs_bot
docker exec edujobs_bot id
```

Expected: `user=10001:10001`, `readonly=true`, `no-new-privileges`, and
`capdrop=["ALL"]`. `/app/data` must be writable; the credentials JSON must be
read-only.

## Updating the VPS

### 1. Create and verify a backup

```bash
sudo systemctl start edujobs-bot-backup.service
sudo systemctl show edujobs-bot-backup.service \
  -p Result -p ExecMainStatus -p ActiveState
```

Continue only when `Result=success` and `ExecMainStatus=0`.

### 2. Push the application changes

```bash
git add .
git commit -m "Describe the change"
git push origin main
```

### 3. Update the server

```bash
cd /opt/assistant
./update.sh
```

`update.sh` exists on the VPS only. It pulls Git changes, rebuilds the image,
restarts the container, and removes dangling images. Run it from
`/opt/assistant` and only after a successful backup.

### 4. Verify production

```bash
docker compose ps
docker compose logs --tail 100 bot
docker exec edujobs_bot id
sqlite3 /opt/assistant/data/db.sqlite3 'PRAGMA integrity_check;'
```

## Backups and recovery

The server SQLite databases are backed up by one systemd service:

- unit: `edujobs-bot-backup.service`;
- timer: daily at 04:30 server time with up to 15 minutes of random delay;
- script: `/usr/local/sbin/edujobs-bot-backup.sh`;
- local storage: `/opt/bot-backups/daily/assistant/`, retained for 14 days;
- Google Drive: `gdrive:edujobs-bot-backups/assistant/daily`, retained for 30
  days.

The script uses SQLite `.backup`, runs `PRAGMA integrity_check`, compresses the
archive, and validates the gzip file.

```bash
systemctl list-timers edujobs-bot-backup.timer --no-pager
systemctl status edujobs-bot-backup.service --no-pager
ls -lh /opt/bot-backups/daily/assistant/
rclone lsf gdrive:edujobs-bot-backups/assistant/daily --files-only | tail
```

Test a restore without stopping production:

```bash
restore_dir=$(mktemp -d /tmp/assistant-restore.XXXXXX)
archive=$(ls -1t /opt/bot-backups/daily/assistant/*.sqlite3.gz | head -n 1)
gzip -cd "$archive" > "$restore_dir/db.sqlite3"
sqlite3 "$restore_dir/db.sqlite3" 'PRAGMA integrity_check;'
```

The expected result is `ok`. Replacing the production database requires first
stopping the bot, preserving another copy of the current database, and
rechecking owner `10001:10001` and mode `600`.

## Useful diagnostics

```bash
# Recent bot logs
docker compose logs --tail 50 bot

# Container shell
docker compose exec bot sh

# Inspect topic state from the host
sqlite3 /opt/assistant/data/db.sqlite3 \
  "SELECT telegram_id, status FROM admin_topics;"

# Unit tests
python -m unittest discover -s tests

# Syntax/import compilation
python -m compileall -q assistant bot llm scripts tests

# Compose validation
docker compose config --quiet
```

---

Last updated: August 29, 2026.
