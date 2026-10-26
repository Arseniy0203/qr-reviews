# CLAUDE.md — QR Reviews

B2B SaaS for collecting customer reviews via QR codes. A business prints QR codes
(counter, business card, table, online); each QR is a collection point bound to
organization → branch → (optionally) employee. Customer scans, rates 1–5, picks
categories, writes text. Owners see stats in the dashboard and get Telegram alerts
about negative reviews.

## Repository layout

| Path | What |
|---|---|
| `backend/` | FastAPI API (`/api/v1`), public review form (`/r/{token}`, Jinja2), arq worker |
| `frontend/` | Dashboard: React + Vite + TS, TanStack Query, React Router, Tailwind 4, ECharts |
| `bot/` | aiogram 3 bot — **only** links Telegram chats to users (`/start <code>`) |
| `ml/` | Placeholder for future text analytics (README only) |
| `infra/` | nginx configs (dev + prod template with Let's Encrypt), postgres init, scripts |

## Backend architecture (strict layering)

```
api/v1/*.py      HTTP only: parse request, check permissions via Depends, call a service
   ↓
services/*.py    ALL business logic; receives AsyncSession + AccessScope; commits
   ↓
models/*.py      SQLAlchemy 2.0 declarative models only, no logic
```

- `schemas/` — Pydantic v2 request/response models. Never return ORM objects without a schema.
- `api/deps.py` — current user, `AccessScope` (organization_id, role, branch_id, employee_id), role guards.
- **Tenant isolation:** every business table has `organization_id`. Every service query
  must be filtered through the scope helper. Objects outside the caller's scope → **404**, not 403.
- Roles: `owner` sees everything in the org; `manager` only their branch; `employee` only their own stats.
- Niche-specific things (categories, employee title «мастер», form texts) live in
  `app/niches/*.yaml`. The core never hardcodes a niche. Categories are copied into
  `review_categories` on organization creation and are editable afterwards.
- Config: only via env (`app/core/config.py`, pydantic-settings). New setting → add to
  `Settings` **and** `.env.example` with a Russian comment.
- Workers: arq tasks in `app/workers/tasks.py`; services enqueue, never call Telegram inline.
- Telegram alerts are sent from the worker via plain `httpx` to the Bot API, not aiogram.

## Product decisions (agreed with the owner — do not change silently)

- **No review gating:** Yandex Maps / 2GIS buttons are shown to every customer regardless of rating.
- **Antifraud flags, never blocks:** suspicious reviews are saved with `is_suspicious=true`
  and `suspicious_reasons` (list of codes). Thresholds in env (`AF_*`).
  - IP is a **weak** signal (shared salon Wi-Fi): `AF_IP_SOFT_THRESHOLD` flags only together
    with another signal (burst or similar text); `AF_IP_HARD_THRESHOLD` (default 20/24h) flags alone.
  - Simhash near-duplicate check only for texts ≥ `AF_SIMHASH_MIN_LENGTH` (30) chars after normalization.
  - Redis down → accept review without flags (fail-open) and log a warning.
- Never store raw IPs. IP and device id are stored as HMAC-SHA256 with `HASH_SALT`.
- **Soft delete only** for branches, employees, QR points (`is_active` / `archived_at`).
  Reviews and `qr_assignments` history are never deleted.
- `QRAssignment`: reassignment closes the open row (`ended_at`) and opens a new one; at most
  one open row per point (partial unique index). `Review.employee_id` is denormalized —
  the employee assigned **at the moment of the review**.
- Stats group by day/week/month **in the organization's timezone** (`OrgSettings.timezone`,
  default `Europe/Moscow`) using `AT TIME ZONE`. MVP computes with SQL aggregates + indexes,
  no pre-aggregation.
- Auth: access JWT (15 min) in memory / `Authorization` header; refresh JWT in cookie
  `HttpOnly; SameSite=Lax; Secure` (Secure via `COOKIE_SECURE`, mandatory in prod), rotated.
  Passwords: argon2.
- Redis rate limits per IP on `POST /api/v1/auth/login` and on public form POST (`RATE_LIMIT_*`).
- Manager invitations are copyable links with a token (no email/SMTP in MVP).
- Bulk QR print PDF uses reportlab; single QR files use segno (PNG/SVG/PDF).

## Conventions

- Identifiers, comments, commit messages: **English**. All user-facing texts (UI, form, bot, errors shown to users): **Russian**.
- Python 3.12, full type hints, `Annotated[..., Depends()]` style for FastAPI deps.
- Ruff (line length 100) is the source of truth for style: `ruff check` + `ruff format`.
- Tests: pytest + httpx `ASGITransport` against a real Postgres (`qr_reviews_test`) and Redis DB 15.
  Every permission / isolation rule gets a test. Mock Telegram via httpx mock transport.
- Migrations: Alembic, one revision per logical change; never edit an applied migration.
- Frontend: function components, data fetching only through hooks in `src/hooks/` (TanStack Query),
  API calls only through `src/api/`. Tailwind utility classes, no CSS-in-JS.
- Commit after each completed stage; run tests + linters before committing.

## Commands

The dev machine is Windows **without make** — use the docker compose forms.

| make | docker compose |
|---|---|
| `make up` | `docker compose up -d --build` (first: `cp .env.example .env`) |
| `make down` | `docker compose down` |
| `make migrate` | `docker compose exec backend alembic upgrade head` |
| `make makemigration m="msg"` | `docker compose exec backend alembic revision --autogenerate -m "msg"` |
| `make seed` | `docker compose exec backend python -m scripts.seed` |
| `make test` | `docker compose exec backend pytest` |
| `make test-bot` | `docker compose exec bot pytest` |
| `make lint` | `docker compose exec backend ruff check .` + `ruff format --check .` (same in `bot`), `docker compose exec frontend npm run lint` |
| `make fmt` | `docker compose exec backend ruff check --fix .` + `ruff format .` |
| `make frontend-build` | `docker compose exec frontend npm run build` |

Dev URLs: dashboard http://localhost:8080, API docs http://localhost:8080/api/docs,
form http://localhost:8080/r/{token}. Dev Postgres host port is `POSTGRES_HOST_PORT` (default 5432).
