# CLAUDE.md — QR Reviews

B2B SaaS for collecting customer reviews via QR codes. A business prints QR codes
(counter, business card, table, online); each QR is a collection point bound to
organization → branch → (optionally) employee. A customer scans, rates 1–5, picks
categories, writes a text, optionally leaves a phone. Owners see stats in the dashboard
and get Telegram alerts about negative reviews. First niche: beauty salons / barbershops.

**Status and the next steps live in [`docs/HANDOFF.md`](docs/HANDOFF.md). Read it first.**
Stages 1–5 are done; stage 6 (worker, bot, alerts) is next.

## Repository layout

| Path | What |
|---|---|
| `backend/` | FastAPI API (`/api/v1`), public review form (`/r/{token}`, Jinja2), arq worker, Alembic, seed |
| `frontend/` | Dashboard: React 19 + Vite + TS, TanStack Query, React Router, Tailwind 4, ECharts (shell only so far) |
| `bot/` | aiogram 3 bot — **only** links Telegram chats to users (`/start <code>`) |
| `ml/` | Placeholder for future text analytics (README lands in stage 6) |
| `infra/` | nginx (dev `nginx.conf`, prod `templates/default.conf.template` + Let's Encrypt), postgres init, scripts |
| `docs/` | `HANDOFF.md` — status, plan, open questions, setup from scratch |

## Backend architecture (strict layering)

```
api/v1/*.py          HTTP only: parse request, permission guard via Depends, call a service
public_form/router.py  HTTP only for the public form (HTML rendering, cookies, redirects)
   ↓
services/*.py        ALL business logic; takes AsyncSession + AccessScope; commits
   ↓
models/*.py          SQLAlchemy 2.x declarative models, no logic
```

- `schemas/` — Pydantic v2 request/response models; routers never return ORM objects directly.
- `core/` — `config` (pydantic-settings), `db`, `redis`, `security` (argon2, JWT, HMAC, signed
  values), `errors` (typed `AppError` → JSON `{detail, code, ...extra}`), `rate_limit`, `logging`.
- `api/deps.py` — `SessionDep`, `CurrentUser`, `ScopeDep` / `OwnerScope` / `ManagerScope`,
  `login_rate_limit`. The membership is resolved **from the DB on every request**
  (`X-Organization-Id` header picks the org, default = first membership), so role changes
  apply immediately. Services re-check roles (`scope.require_role`) as defense in depth.
- `services/scope.py` — `AccessScope` with `org_filter`, `branch_filter`, `review_filters`,
  `ensure_same_org`, `ensure_branch`. Objects outside the scope → **404**, never 403.
- `services/` — `auth_service`, `invitation_service`, `organization_service`, `branch_service`,
  `employee_service`, `category_service`, `qr_service` + `qr_render`, `assignments`,
  `review_service` (public intake), `antifraud`, `contact_service` (PD revocation),
  `consent_service`, `niches`, `names` (genitive), `phone` (E.164).
- Niche-specific things (categories, «мастер», form texts) live in `app/niches/*.yaml`
  (`beauty`, `generic`). The core never hardcodes a niche. Categories (with a stable `code`)
  are copied into `review_categories` on organization creation and editable afterwards.
- Config only via env. New setting → `Settings` **and** `.env.example` with a Russian comment.
- Workers: arq tasks in `app/workers/tasks.py`; services enqueue, never call Telegram inline.
- Notifications go **only** through a `NotificationChannel` interface
  (`services/notifications.py`, stage 6). `TelegramChannel` is the first implementation
  (plain `httpx` to the Bot API, not aiogram). New channels (email, MAX, SMS) must be
  addable without touching the worker. `users.telegram_chat_id` is Telegram's address field.

### Data model highlights

- Every business table has `organization_id`. References between business tables are
  **composite FKs** `(x_id, organization_id) → x(id, organization_id)`: the database itself
  rejects a review that points at another org's branch / point / employee / consent document.
  **Keep this pattern for every new table.** Exceptions: `users` (global, access via
  `memberships`), `review_category_links` (association), `telegram_link_codes` (per user).
- Enums are `varchar + CHECK` (not native PG enums) to keep migrations simple.
- `reviews.id` and `qr_assignments.id` are `bigint identity`; everything else is UUID v4.
- `QRAssignment`: history of employee ↔ point; partial unique index — at most one open row per
  point. `Review.employee_id` is denormalized: the employee assigned **at submit time**.
- Indexes for stats: `reviews (organization_id, created_at)`, `(organization_id, branch_id,
  created_at)`, `(organization_id, employee_id, created_at)`, `(qr_point_id, created_at)`,
  partial `(organization_id, customer_phone)`.
- DB CHECKs: rating 1–5, text ≤ 2000 chars, phone `^\+[0-9]{10,15}$`, contact requires a valid
  (not revoked) PD consent, marketing consent requires PD consent, membership role scope
  (owner: no branch/employee; manager: branch; employee: employee).
- `consent_documents` are immutable: a trigger rejects UPDATE/DELETE (TRUNCATE still works for tests).

## Product decisions (agreed with the owner — do not change silently)

**Auth & roles**
- Access JWT (15 min) in memory / `Authorization: Bearer`; refresh JWT in cookie
  `refresh_token` (`HttpOnly; SameSite=Lax; Path=/api/v1/auth`, `Secure` via `COOKIE_SECURE`,
  mandatory in prod). Refresh rotates; **reusing an old refresh token revokes the whole
  family** (Redis). The frontend must serialize refresh calls (two tabs refreshing at once
  would trigger reuse detection).
- Passwords: argon2 (cheap params only when `APP_ENV=test`).
- `owner` — everything; `manager` — only their branch; `employee` — only their own stats,
  **no access to QR management** (confirmed by the owner).
- Manager/employee invitations are one-time copyable links (only the token hash is stored);
  no email/SMTP in MVP.
- Redis rate limits per hashed IP: login/register (`RATE_LIMIT_LOGIN_*`), public form POSTs
  (`RATE_LIMIT_PUBLIC_FORM_*`, generous — shared salon Wi-Fi). Fail-open if Redis is down.

**Entities**
- **Soft delete only** (`archived_at`, restore endpoints) for branches, employees, categories,
  QR points. Reviews and assignment history are never deleted. `is_active=false` on an
  employee or point is a pause, not a delete. Archiving a branch archives its points
  (employees stay); archiving a point or employee closes open assignments.
- **An employee may be assigned to a QR point of another branch** (masters work in several
  salons). `Review.branch_id` always comes from the **point**. Only owners can do cross-branch
  assignments (managers only see their branch's employees).
- Category `code` from the niche survives renaming (stats/ML rely on it).

**QR codes** (`services/qr_render.py`)
- Token: 10 chars from `abcdefghjkmnpqrstuvwxyz23456789` (no look-alikes), `secrets`.
- segno matrix, error level **Q without boost**, quiet zone 4 modules, PNG ≥ 1000 px.
  Under every code: «филиал · точка» and the short link as text (never inside the quiet zone).
- PDFs with reportlab: vector modules, embedded DejaVu Sans from `app/assets/fonts`
  (vendored with its license — no system fonts, Cyrillic everywhere). Single = A6 page;
  sheets = A4, layouts `1x1` / `2x3` / `3x4`, cells edge to edge, crop marks in the margins.
- Managers create, edit, assign, download and print only their branch's points.

> ⚠️ **`PUBLIC_BASE_URL` is baked into every printed QR code.** It must be the final
> production domain. Never print real QR codes for clients until the domain is fixed.

**Public form** (`app/public_form/`)
- Flow: GET form → POST review (303, PRG) → contact step (only if `contact_step_enabled`) →
  thanks. Works without JS. CSS/JS live in `static/` but are **inlined**: one request,
  **budget ≤ 15 KB raw** (test-enforced; now ~8 KB, ~3 KB gzip). Strict CSP with per-request
  nonce, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. Tap targets ≥ 44 px
  (stars 56, buttons 52), inputs 16 px font, dark mode.
- Title «Оцените работу мастера Анны» when an employee is assigned
  (`niche.form.title_with_employee` + `services/names.genitive_first_name`, left undeclined
  when unsure), otherwise the niche's neutral title.
- Protection: signed `form_token` (2 h, bound to the point), `qr_device` cookie (HttpOnly,
  path `/r`, 2 y), rate limit, honeypot. **Filled honeypot → answered as success, nothing
  stored** (a bot is not a customer review; awaiting final owner confirmation).
- Unknown/archived point → 404 stub; paused point → 200 stub «приём приостановлен».
- **No review gating:** Yandex Maps / 2GIS buttons on the thanks page for every rating, only if
  the URLs are set in settings.

**Antifraud** (`services/antifraud.py`) — flags, never blocks; thresholds in `AF_*`
- `device_repeat`, `point_burst`, `employee_burst` (sliding window), `duplicate_text`
  (64-bit simhash, only texts ≥ 30 chars after normalization, compared with recent texts of the
  same org), `ip_repeat` — IP is a **weak** signal: soft threshold only together with a burst or
  duplicate text, hard threshold (≥ 20/24 h) alone. Redis down → no flags (fail-open).

**Stats** (stage 7) — group by day/week/month **in the organization's timezone**
(`OrgSettings.timezone`, default `Europe/Moscow`) with `AT TIME ZONE`; SQL aggregates + the
indexes above, no pre-aggregation in MVP.

## Deviations from the original spec (approved)

| Spec | Implemented | Why |
|---|---|---|
| Tailwind CSS (v3 implied by `tailwind.config.ts`) | Tailwind **4** + `@tailwindcss/vite`, config kept via `@config` | v3 chain had unfixable `npm audit` findings |
| segno PDF for single codes, bulk PDF unspecified | reportlab for all PDFs | captions with Cyrillic, crop marks, vector output |
| Telegram via aiogram | Bot uses aiogram; alerts are sent from the worker via `httpx` behind `NotificationChannel` | no aiogram in the backend; pluggable channels |
| `pd_consent` boolean in `reviews` | `pd_consent_document_id` / `marketing_consent_document_id` (FK) + `*_at` + `*_revoked_at` | 152-FZ: consent is a separate versioned document; FK points at the exact text agreed to |
| Consent texts (global) | `consent_documents` **per organization** | the salon is the PD operator; its name/INN/address must be in the text |
| `invitation.py`, `models/*` per plan | + `consent.py`, `invitation.py`, `telegram_link.py` | new tables |
| ML README in stage 3 (second spec draft) | stage 6, together with the worker stub | agreed |
| Contact step whenever `collect_contact` | only when PD operator details are complete | legal requirement, agreed |

## Требования законодательства РФ

Обязательны для всего кода. Если задача им противоречит, остановись и спроси владельца.

**152-ФЗ «О персональных данных»** (с 01.09.2025 согласие — отдельный документ):
- Оператор ПД — компания-клиент (салон); наш сервис обрабатывает ПД по её поручению.
  Реквизиты сервиса — `PD_PROCESSOR_DETAILS`; реквизиты салона — `OrgSettings`
  (`legal_name`, `inn`, `legal_address`, `privacy_email`), они попадают в текст согласия.
  Их изменение автоматически публикует новую версию согласий.
- `collect_contact` нельзя включить без всех четырёх реквизитов (API 400 `legal_details_required`).
- Тексты согласий — `consent_documents`, неизменяемые версии; шаблоны `app/consents/*.txt.j2`
  (**перед продажей тексты должен проверить юрист**).
- Каждое согласие — на **отдельной странице** `/r/{token}/consent/{pd|marketing}?v=<id>`,
  с блоком «Как отозвать согласие» (email из `privacy_email`).
- Отзыв хранит, на какой документ и когда согласился клиент.
- Контакт без действующего согласия запрещён CHECK-ом в БД.
- **Отзыв согласия** (`services/contact_service.py`, API только для владельца):
  имя и телефон обнуляются во всех отзывах клиента в компании, ставится
  `pd_consent_revoked_at` (и `marketing_consent_revoked_at`); ссылка на документ и дата
  согласия остаются как доказательство; сам отзыв сохраняется.
- ПД хранятся только на серверах в РФ (242-ФЗ). Сырые IP не хранятся, только HMAC (`HASH_SALT`).

**38-ФЗ «О рекламе»:** согласие на рекламу — отдельная **необязательная** галочка; обе галочки
по умолчанию **выключены**; бонус выдаётся и без рекламного согласия.

**Зарубежные сервисы:** никаких зарубежных сервисов, обрабатывающих ПД (Google Analytics,
Sentry SaaS, Google Fonts, зарубежные CDN). Аналитика — только Яндекс Метрика
(`YANDEX_METRIKA_ID`, пусто — выключена). Шрифты — системные или в репозитории.

**Логи:** не писать телефоны, имена клиентов, тексты отзывов и тела запросов формы.
Можно: ID отзыва/компании/точки, оценку, коды причин антифрода.

## Conventions

- Identifiers, comments, commit messages: **English**. Everything user-facing (UI, form, bot,
  error messages): **Russian**.
- Python 3.12, full type hints, `Annotated[..., Depends()]` deps. Ruff (line 100) is the style
  authority; RUF001–003 are off (Cyrillic).
- `pytest` turns `DeprecationWarning` from `app.*` into errors — fix them (e.g. SQLAlchemy 2.1
  deprecated `Row.tuple()`: use `tuple(row)`).
- Tests: pytest + httpx `ASGITransport` against real Postgres (`qr_reviews_test`, rebuilt via
  Alembic downgrade/upgrade each run) and Redis DB 15. Every permission / isolation rule gets
  a test. Fixtures: `session`, `client`, `team` (org with two branches and one user per role),
  helpers in `tests/api_helpers.py`, `tests/factories.py`, `tests/public_helpers.py`.
  In tests re-read rows with `populate_existing=True`, not `expire_all()` (lazy loads fail in async).
  Mock Telegram via `httpx.MockTransport`.
- Migrations: Alembic, one revision per logical change, never edit an applied one.
  `0001_initial_schema` is applied in dev; new changes go into `0002_*`.
- Frontend: function components; data fetching only through hooks in `src/hooks/`
  (TanStack Query); API calls only through `src/api/`; Tailwind utilities, no CSS-in-JS.
- Commit after each stage; tests + linters must pass first.

## Commands

The owner's machine is Windows **without make** — use the docker compose forms.

| make | docker compose |
|---|---|
| `make up` | `docker compose up -d --build` (first: copy `.env.example` → `.env`) |
| `make down` | `docker compose down` |
| `make migrate` | `docker compose exec backend alembic upgrade head` |
| `make makemigration m="msg"` | `docker compose exec backend alembic revision --autogenerate -m "msg"` |
| `make seed` | `docker compose exec backend python -m scripts.seed` (`--reset` wipes all data) |
| `make test` | `docker compose exec backend pytest` |
| `make test-bot` | `docker compose exec bot pytest` |
| `make lint` | `docker compose exec backend ruff check .` + `ruff format --check .` (same in `bot`), `docker compose exec frontend npm run lint` |
| `make fmt` | `docker compose exec backend ruff check --fix .` + `ruff format .` |
| `make frontend-build` | `docker compose exec frontend npm run build` |

Dev URLs: dashboard http://localhost:8080, API docs http://localhost:8080/api/docs,
form http://localhost:8080/r/{token}. Demo logins after seed: `owner@lavanda.demo`,
`manager@lavanda.demo`, `anna@lavanda.demo`, password `demo12345`.

### Windows gotchas

- PowerShell 5.1 mangles double quotes in native args: commit with `git commit -F <file>`.
- Git Bash rewrites `/paths` in `docker run -v`: use PowerShell or `MSYS_NO_PATHCONV=1`.
- `Invoke-RestMethod` shows Cyrillic as mojibake (decodes JSON as Latin-1) — the API is fine.
- Backend start on a Windows bind mount takes ~10 s; a 502 right after `up` is just startup.
- If host port 5432 is taken, set `POSTGRES_HOST_PORT` in `.env`.
