# UptimeBoard — заметки для Claude

Мониторинг/статус-борд: FastAPI + PostgreSQL + Redis (backend), React + Vite + TypeScript (frontend), деплой через docker-compose. Пробы: icmp/tcp/http/tls/heartbeat.

## Рабочие правила

- **Changelog — обязательно.** После каждого commit + push, затрагивающего пользовательскую функциональность, обнови [`frontend/src/changelog.ts`](frontend/src/changelog.ts): добавь новую запись (или дополни верхнюю) — `version`, `date` (YYYY-MM-DD), список изменений на русском — и подними `version` в самой верхней записи по семверу (fix → patch, фича → minor). `VERSION` и плашка версии на сайте берутся из этого файла автоматически. Чисто внутренние правки (рефактор, тесты, CI) в changelog можно не вносить.
- Миграции БД (`backend/alembic/versions/`) применяются автоматически при старте контейнера `api` (`alembic upgrade head`) — вручную накатывать не нужно.
- Деплой: `./deploy.sh` в корне (`git pull` → build → up -d).
- Коммит-сообщения — на русском, в духе истории репозитория.

## Проверка перед коммитом

- Frontend: `cd frontend && npx tsc --noEmit && npx vite build`.
- Backend: `python -m py_compile <изменённые файлы>`; полный `pytest` — только в CI (локально мешает версия SQLAlchemy; гоняется с `DATABASE_URL=sqlite://`).
- CSS-классы для новых экранов держи с уникальными префиксами — общий `styles.css` уже большой, легко словить коллизию имён.
