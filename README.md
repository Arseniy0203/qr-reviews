# QR Отзывы

B2B-сервис сбора отзывов через QR-коды: клиент сканирует код на стойке, визитке или столе,
ставит оценку и пишет отзыв; владелец видит статистику по филиалам, сотрудникам и точкам
и получает уведомления о негативе в Telegram.

> Проект в разработке. Полная документация (архитектура, переменные окружения, деплой)
> появится по завершении MVP.

## Быстрый старт

Нужны Docker и Docker Compose.

```bash
cp .env.example .env
docker compose up -d --build
```

- Кабинет: http://localhost:8080
- API (Swagger): http://localhost:8080/api/docs

Если порт 5432 на машине занят, задайте в `.env` `POSTGRES_HOST_PORT=5433`.

## Команды

Все цели `Makefile` и их эквиваленты для машин без `make`:

| make | Без make |
|---|---|
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f --tail=100` |
| `make migrate` | `docker compose exec backend alembic upgrade head` |
| `make makemigration m="описание"` | `docker compose exec backend alembic revision --autogenerate -m "описание"` |
| `make seed` | `docker compose exec backend python -m scripts.seed` |
| `make test` | `docker compose exec backend pytest` |
| `make test-bot` | `docker compose exec bot pytest` |
| `make lint` | `docker compose exec backend ruff check .`<br>`docker compose exec backend ruff format --check .`<br>`docker compose exec bot ruff check .`<br>`docker compose exec bot ruff format --check .`<br>`docker compose exec frontend npm run lint` |
| `make fmt` | `docker compose exec backend ruff check --fix .`<br>`docker compose exec backend ruff format .`<br>`docker compose exec bot ruff check --fix .`<br>`docker compose exec bot ruff format .` |
| `make frontend-build` | `docker compose exec frontend npm run build` |
| `make shell` | `docker compose exec backend bash` |
| `make psql` | `docker compose exec postgres sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB'` |
