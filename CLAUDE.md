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
- Notifications go **only** through the `NotificationChannel` interface
  (`services/notifications.py`). `TelegramChannel` is the first implementation (plain `httpx`
  to the Bot API, not aiogram). The worker never talks to Telegram directly, so new channels
  (email, MAX, SMS) are added without touching the worker. `users.telegram_chat_id` stays as
  Telegram's address field.
- **Database-level tenant isolation:** references between business tables are composite FKs
  `(x_id, organization_id) → x(id, organization_id)`. The DB itself rejects a review pointing at
  another org's branch/point/employee/consent. Keep this pattern for every new table.

## Product decisions (agreed with the owner — do not change silently)

- **No review gating:** Yandex Maps / 2GIS buttons are shown to every customer regardless of rating.
- **Antifraud flags, never blocks:** suspicious reviews are saved with `is_suspicious=true`
  and `suspicious_reasons` (list of codes). Thresholds in env (`AF_*`).
  - IP is a **weak** signal (shared salon Wi-Fi): `AF_IP_SOFT_THRESHOLD` flags only together
    with another signal (burst or similar text); `AF_IP_HARD_THRESHOLD` (default 20/24h) flags alone.
  - Simhash near-duplicate check only for texts ≥ `AF_SIMHASH_MIN_LENGTH` (30) chars after normalization.
  - Redis down → accept review without flags (fail-open) and log a warning.
- **Soft delete only** for branches, employees, QR points (`is_active` / `archived_at`).
  Reviews and `qr_assignments` history are never deleted.
- `QRAssignment`: reassignment closes the open row (`ended_at`) and opens a new one; at most
  one open row per point (partial unique index). `Review.employee_id` is denormalized —
  the employee assigned **at the moment of the review**.
- **An employee may be assigned to a QR point of another branch** of the same organization
  (masters often work in several salons). `Review.branch_id` always comes from the **QR point**,
  not from the employee. Employee stats therefore can span several branches.
- Review text ≤ 2000 chars (`REVIEW_TEXT_MAX_LENGTH`): DB CHECK + Pydantic.
- Phones are stored in E.164 (`+79991234567`), normalized by `services/phone.normalize_phone`;
  DB CHECK `^\+[0-9]{10,15}$`.
- Stats group by day/week/month **in the organization's timezone** (`OrgSettings.timezone`,
  default `Europe/Moscow`) using `AT TIME ZONE`. MVP computes with SQL aggregates + indexes,
  no pre-aggregation.
- Auth: access JWT (15 min) in memory / `Authorization` header; refresh JWT in cookie
  `HttpOnly; SameSite=Lax; Secure` (Secure via `COOKIE_SECURE`, mandatory in prod), rotated.
  Passwords: argon2.
- Redis rate limits per IP on `POST /api/v1/auth/login` and on public form POST (`RATE_LIMIT_*`).
- Manager invitations are copyable links with a token (no email/SMTP in MVP).
- `collect_contact` can be enabled only when all PD operator details are filled
  (`legal_name`, `inn`, `legal_address`, `privacy_email`): API answers 400
  `legal_details_required` with `fields`. The public form uses `OrgSettings.contact_step_enabled`
  and silently skips the contact step when details are incomplete.
- QR codes (`services/qr_render.py`): segno builds the matrix, error level **Q** (no boost),
  quiet zone 4 modules, PNG ≥ 1000 px. Under every code: «филиал · точка» and the short link
  as text. PDFs are drawn with reportlab (vector modules, embedded DejaVu Sans from
  `app/assets/fonts` — no system fonts, Cyrillic everywhere). Print sheets: A4, layouts
  `1x1` / `2x3` / `3x4`, cells edge to edge with crop marks in the margins.
- QR management is owner + manager; a manager creates, edits, assigns, downloads and prints
  only points of their own branch and can assign only employees of their branch. Employees
  have no QR access.
- Archiving a branch archives its QR points (employees stay). Archiving a point or employee
  closes open assignments. `is_active=false` is a pause, not a delete.

- **Public form** (`app/public_form/`): GET form → POST review (303, PRG) → contact step (only
  if `contact_step_enabled`) → thanks. Works without JS. CSS/JS live in `static/` but are
  **inlined** into the page: one request, **budget ≤ 15 KB raw** (a test enforces it; now ~8 KB,
  ~3 KB gzip). No external resources except optional Yandex Metrika. Strict CSP with a
  per-request nonce, `Referrer-Policy: no-referrer` (the point token must not leak to maps),
  `Cache-Control: no-store`. Tap targets ≥ 44 px (stars 56 px, buttons 52 px), inputs 16 px font.
- Title: «Оцените работу мастера Анны» when an employee is assigned (`niche.form.title_with_employee`,
  genitive by `services/names.py`, undeclined if unsure), otherwise the niche's neutral title.
  The employee is resolved **at submit time**, not when the page was opened.
- Form protection: signed `form_token` (2 h, bound to the point), `qr_device` cookie (HttpOnly,
  path `/r`, 2 years), generous per-IP rate limit (`RATE_LIMIT_PUBLIC_FORM_*`, shared salon
  Wi-Fi). A filled honeypot field means a bot: the request is answered as success and
  **nothing is stored** (this is not a customer review, so the flag-don't-block rule does not apply).
- Contact step: linked to the review by a signed `ref` (30 min, bound to the point). The form
  posts the ids of the exact consent documents it displayed; both checkboxes are unchecked by
  default, PD is required, marketing is optional. Consent pages `/r/{token}/consent/{pd|marketing}?v=<id>`
  show that exact version and how to revoke (privacy_email).
- Consent revocation: owner-only API `GET /api/v1/reviews/contacts?phone=` and
  `POST /api/v1/reviews/contacts/revoke {phone, kind: pd|marketing}`.

> ⚠️ **`PUBLIC_BASE_URL` is baked into every printed QR code.** It must be the final
> production domain. Never print real QR codes for clients until the domain is fixed:
> changing it later means reprinting every card, sticker and table tent.

## Требования законодательства РФ

Обязательны для всего кода. Если задача им противоречит, остановись и спроси.

**152-ФЗ «О персональных данных»** (с 01.09.2025 согласие оформляется отдельным документом):
- Оператор ПД — компания-клиент (салон). Наш сервис обрабатывает ПД по её поручению;
  реквизиты сервиса задаются в `PD_PROCESSOR_DETAILS`, реквизиты салона — в `OrgSettings`
  (`legal_name`, `inn`, `legal_address`, `privacy_email`) и попадают в текст согласия.
- Тексты согласий хранятся в `consent_documents` (`type`: `pd` | `marketing`, `version`,
  `text`, `published_at`) отдельно для каждой компании. Опубликованные версии **неизменяемы**
  (триггер в БД): правка означает публикацию новой версии. Шаблоны — `app/consents/*.txt.j2`
  (перед продажей тексты должен проверить юрист).
- Каждое согласие показывается на **отдельной странице**, а форма ссылается на неё.
- Отзыв хранит, на какой именно документ согласился клиент и когда:
  `pd_consent_document_id` + `pd_consent_at`, `marketing_consent_document_id` + `marketing_consent_at`.
- Контакт (имя или телефон) без действующего согласия на ПД запрещён CHECK-ом в БД.
- **Отзыв согласия** (`services/contact_service.py`): имя и телефон обнуляются во всех отзывах клиента в компании,
  проставляется `pd_consent_revoked_at` (и `marketing_consent_revoked_at`). Ссылка на документ
  и дата согласия сохраняются как доказательство. Сам отзыв о качестве остаётся.
- Хранить ПД только на серверах в РФ (242-ФЗ, локализация).
- Сырые IP не хранить, только HMAC (`HASH_SALT`).

**38-ФЗ «О рекламе»:**
- Согласие на рекламные сообщения — отдельная **необязательная** галочка. Обе галочки
  (ПД и реклама) по умолчанию **выключены**. Без маркетингового согласия клиент всё равно
  получает обещанный бонус.

**Зарубежные сервисы:**
- Никаких зарубежных сервисов, обрабатывающих ПД: без Google Analytics, Sentry SaaS, Google Fonts,
  зарубежных CDN на публичной форме. Аналитика — только Яндекс Метрика, по желанию
  (`YANDEX_METRIKA_ID`, пусто — выключена). Шрифты — системные или self-hosted.

**Логи:**
- Не писать в логи телефоны, имена клиентов и тексты отзывов (и тела запросов формы целиком).
  Логировать можно ID отзыва, ID компании и коды причин антифрода.

## Backlog (вне MVP)

- ML-анализ текстов (аспекты, тренды, дайджест) — см. `ml/README.md`.
- Биллинг и тарифы, админка владельцев сервиса, интеграции с CRM и онлайн-записью.
- Каналы уведомлений кроме Telegram (email, MAX, SMS) через `NotificationChannel`.
- Предупреждение в кабинете, если не заполнены реквизиты оператора ПД.

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
