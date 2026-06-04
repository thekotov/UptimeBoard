"""Seed helpers for the database.

`ensure_admin` is idempotent and runs automatically on `api` startup
(see entrypoint.sh) so the first admin is created from ADMIN_EMAIL /
ADMIN_PASSWORD without a manual step.

The demo page is example content and is created only by the manual seed:

    docker compose run --rm api python -m scripts.seed
"""

from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.config import settings
from app.db import SessionLocal
from app.models import Page, Probe, Server, Service, User


def ensure_admin(db: Session) -> None:
    """Create the admin user from settings if it does not exist yet."""
    admin = db.query(User).filter(User.email == settings.admin_email).first()
    if admin is None:
        db.add(
            User(
                email=settings.admin_email,
                hashed_password=hash_password(settings.admin_password),
                role="admin",
            )
        )
        print(f"created admin user: {settings.admin_email}")
    else:
        print("admin user already exists")


def ensure_demo_page(db: Session) -> None:
    """Create the example status page if it does not exist yet."""
    if db.query(Page).filter(Page.slug == "demo").first() is None:
        page = Page(slug="demo", title="Demo Status Page",
                    description="Example monitoring dashboard",
                    group_name="Demo", is_published=True)
        service = Service(name="Public Website", order=0)
        server = Server(name="web-1", host="example.com", order=0)
        server.probes = [
            Probe(name="HTTP 200", type="http", interval_sec=30, timeout_sec=10,
                  config={"url": "https://example.com", "expected_status": 200}),
            Probe(name="HTTPS port", type="tcp", interval_sec=30, timeout_sec=10,
                  config={"port": 443}),
            Probe(name="Ping", type="icmp", interval_sec=30, timeout_sec=5,
                  config={"count": 3}),
        ]
        service.servers = [server]
        page.services = [service]
        db.add(page)
        print("created demo page: /demo")
    else:
        print("demo page already exists")


def bootstrap_admin() -> None:
    """Entrypoint hook: ensure the admin user exists (called on api startup)."""
    db = SessionLocal()
    try:
        ensure_admin(db)
        db.commit()
    finally:
        db.close()


def seed() -> None:
    db = SessionLocal()
    try:
        ensure_admin(db)
        ensure_demo_page(db)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
