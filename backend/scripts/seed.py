"""Seed the database with an admin user and a demo page.

Idempotent: running it again will not duplicate the admin or the demo page.
Run inside the api container:

    docker compose run --rm api python -m scripts.seed
"""

from app.auth.security import hash_password
from app.config import settings
from app.db import SessionLocal
from app.models import Page, Probe, Server, Service, User


def seed() -> None:
    db = SessionLocal()
    try:
        # Admin user
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

        # Demo page
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

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
