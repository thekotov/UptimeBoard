#!/usr/bin/env bash
set -e

# Wait for Postgres to accept connections before doing anything DB-related.
wait_for_db() {
  python - <<'PY'
import time, sys
from sqlalchemy import create_engine, text
from app.config import settings

for attempt in range(60):
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("database is ready")
        sys.exit(0)
    except Exception as exc:
        print(f"waiting for database... ({exc})")
        time.sleep(2)
print("database not reachable", file=sys.stderr)
sys.exit(1)
PY
}

case "$1" in
  api)
    wait_for_db
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    wait_for_db
    # Migrations are owned by the api service; wait until they're applied.
    python - <<'PY'
import time, sys
from sqlalchemy import create_engine, inspect
from app.config import settings

engine = create_engine(settings.database_url)
for _ in range(60):
    try:
        if inspect(engine).has_table("probes"):
            print("schema is ready")
            sys.exit(0)
    except Exception:
        pass
    print("waiting for migrations...")
    time.sleep(2)
sys.exit(1)
PY
    exec python -m app.worker
    ;;
  *)
    exec "$@"
    ;;
esac
