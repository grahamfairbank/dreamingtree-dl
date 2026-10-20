"""FastAPI app and routes; serves web/index.html."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.backend import Backend, FakeBackend

INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


class QueueRequest(BaseModel):
    ids: list[str]
    destination: str


def create_app(backend: Backend) -> FastAPI:
    app = FastAPI(title="dreamingtree-dl")

    @app.exception_handler(KeyError)
    async def _not_found(_, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc.args[0]) if exc.args else "not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad_request(_, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(INDEX, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    def status() -> dict:
        return backend.status()

    @app.get("/api/listing")
    def listing(page: int = 1, q: str = "", artist: str = "", year: str = "",
                source: str = "", library: str = "", seeded: str = "") -> dict:
        filters = {k: v for k, v in {"artist": artist, "year": year, "source": source,
                                     "library": library, "seeded": seeded}.items() if v}
        return backend.listing(page, q, filters)

    @app.post("/api/queue")
    def queue(req: QueueRequest) -> dict:
        if not req.ids:
            raise HTTPException(400, "no torrents selected")
        return {"jobs": backend.queue(req.ids, req.destination)}

    @app.get("/api/jobs")
    def jobs() -> dict:
        return {"jobs": backend.jobs()}

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: int) -> dict:
        return {"events": backend.job_events(job_id)}

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: int) -> dict:
        return backend.retry(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: int) -> dict:
        return backend.cancel(job_id)

    @app.get("/api/settings")
    def get_settings() -> dict:
        return _redact(backend.get_settings())

    @app.put("/api/settings")
    def put_settings(patch: dict = Body(...)) -> dict:
        return _redact(backend.save_settings(patch))

    @app.post("/api/library/rescan")
    def rescan_library() -> dict:
        return backend.rescan_library()

    @app.post("/api/relogin")
    def relogin() -> dict:
        return backend.relogin()

    return app


def _redact(settings: dict) -> dict:
    """Defence in depth: the password never leaves the server, whatever the backend returns."""
    dt = settings.get("dreamingtree")
    if isinstance(dt, dict) and "password" in dt:
        dt = dict(dt)
        dt["password_set"] = bool(dt.pop("password"))
        settings = {**settings, "dreamingtree": dt}
    return settings


def _default_app() -> FastAPI:
    """DTDL_FAKE=1 (the default, used by tests and the demo) serves in-memory sample data;
    DTDL_FAKE=0 (set in docker-compose) runs the real backend."""
    if os.environ.get("DTDL_FAKE", "1") == "1":
        return create_app(FakeBackend())
    import logging

    from app.live import LiveBackend

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    backend = LiveBackend()
    backend.start()
    return create_app(backend)


app = _default_app()
