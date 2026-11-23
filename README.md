# Otter Assistant

> **Your schedule. Your tasks. Your Otter.**
> AI-Powered Personal Academic Assistant for UTH (University of Transport Ho Chi Minh City) Students.

## Architecture Overview
Otter Assistant is a **Modular Monolith** built for production reliability and strict user data isolation:

- **Backend:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, Uvicorn, HTTPX
- **Database:** MySQL 8.0+ (Source of Truth, UTC timestamps, `utf8mb4`, 14 normalized tables)
- **Cache & Locking:** Redis 7 (distributed scheduler locks, cache, rate limiting — never long-term academic storage)
- **AI Agent:** Single Agent + 24 validated backend tools powered by **Google Gemini API (Free Tier)** behind a pluggable `LLMProvider` interface (ready for Ollama)
- **Messaging:** Telegram Bot API (`@BotFather`) + Secret-Token verified HTTPS Webhook + one-time account linking (`/link <CODE>`)
- **Scheduler:** APScheduler (`class_reminders`, `assignment_reminders`, `custom_reminders`, `daily_summary`, `weekly_summary`) with Redis distributed locking and MySQL `idempotency_key` duplicate prevention
- **Frontend:** React 19, TypeScript, Vite, Nginx SPA (7 responsive pages)

## Project Structure
```text
otter-assistant/
├── backend/
│   ├── app/
│   │   ├── agents/          # OtterAssistantAgent, GeminiLLMProvider, ConversationMemoryManager, prompts
│   │   ├── api/             # FastAPI routes & JWT auth dependencies
│   │   ├── core/            # Config, Fernet encryption, structured JSON redacting logs, middleware
│   │   ├── database/        # SQLAlchemy 2.x Base, UTC TimestampMixin, session factory
│   │   ├── integrations/    # portal/ (PortalSessionManager), lms/ (LMSAuthManager, CourseMapping), telegram/
│   │   ├── models/          # 14 normalized MySQL 8.0+ ORM models
│   │   ├── notifications/   # NotificationService with idempotency_key duplicate prevention
│   │   ├── repositories/    # User-isolated data access layer
│   │   ├── scheduler/       # APScheduler jobs, RedisDistributedLock, standalone worker.py
│   │   ├── schemas/         # Pydantic v2 request/response DTOs
│   │   ├── services/        # Subject, Schedule (UTH Buổi/Ca/Tiết + conflict detection), Assignment, Reminder
│   │   └── tools/           # 24 validated backend tools for AI Agent
│   ├── alembic/             # MySQL 8.0+ database migrations
│   └── tests/               # Pytest unit & integration test suite
├── frontend/                # React + TypeScript + Vite Admin Dashboard + Nginx config
├── docker-compose.yml       # Local development stack
├── docker-compose.prod.yml  # Production stack (multi-worker API + dedicated scheduler worker)
├── .env.example
└── README.md
```

## Quick Start

### 1. Configure Environment Variables
```bash
cp .env.example .env
# Generate SECRET_KEY and Fernet ENCRYPTION_KEY:
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))"
python3 -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

### 2. Local Development (Docker Compose)
```bash
docker compose up --build -d
docker compose exec backend alembic upgrade head
```
- Backend API: `http://localhost:8000` (`/health`, `/health/ready`, `/docs`)
- Frontend Dashboard: `http://localhost:5173`

### 3. Production Deployment
```bash
docker compose -f docker-compose.prod.yml up --build -d
```
Production mode enforces:
- Non-root container user (`otter`)
- Startup security guard (`SECRET_KEY`, `ENCRYPTION_KEY`, `APP_DEBUG=false`)
- Automatic Alembic migration step (`migrate` service) before API/Scheduler boot
- Dedicated single `scheduler` worker (`python -m app.scheduler.worker`) separated from multi-worker Uvicorn API (`SCHEDULER_ENABLED=false`)
- Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`) and sliding-window rate limiting on `/api/v1/auth/*` and `/api/v1/agent/*`

### 4. Running Tests Locally
```bash
# Backend tests (Pytest)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v

# Frontend typecheck & production build
cd frontend
npm install
npm run typecheck && npm run build
```

## Production Readiness & Real Integration Checklist (Section 19)
Before enabling live student traffic, complete these real-credential verifications:
1. **Portal UTH (`portal.ut.edu.vn`):** Set verified `PORTAL_SCHEDULE_ENDPOINT` (and `PORTAL_REFRESH_ENDPOINT` if supported) in `.env` from an authorized DevTools Network capture, then verify live sync against real student timetable data.
2. **UTH LMS (`courses.ut.edu.vn`):** Connect via an authorized method (`ical_feed` `.ics` export URL or `moodle_ws` `wstoken`) without bypassing Cloudflare anti-bot challenges, and verify live assignment sync + Course Mapping.
3. **Google Gemini API (Free Tier):** Set `GEMINI_API_KEY` and `GEMINI_MODEL` in `.env` and confirm Free Tier quota on `aistudio.google.com/rate-limit`.
4. **Telegram Bot (`@BotFather`):** Configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET` in `.env` and register your HTTPS webhook (`setWebhook` with `url=https://<your-domain>/api/v1/webhooks/telegram` and `secret_token=$TELEGRAM_WEBHOOK_SECRET`).
