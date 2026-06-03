from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, ping, public
from app.config import settings
from app.realtime import get_worker_heartbeat_age

app = FastAPI(title="Monitoring Dashboard API")

origins = ["*"] if settings.cors_origins == "*" else [
    o.strip() for o in settings.cors_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(ping.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/worker")
def worker_health(response: Response):
    """Dead-man switch: report the probe worker's liveness from its Redis
    heartbeat. Returns 503 when the last beat is missing or too old, so an
    external uptime check can alert if monitoring itself has stopped."""
    age = get_worker_heartbeat_age()
    healthy = age is not None and age <= settings.worker_stale_sec
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "stale",
        "last_beat_age_sec": None if age is None else round(age, 1),
        "stale_after_sec": settings.worker_stale_sec,
    }
