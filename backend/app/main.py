from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .routers import audit, briefings, inbox, knowledge, podcasts, reminders, runs, search, settings, tasks
from .services.scheduler import start_scheduler


def create_app() -> FastAPI:
    app = FastAPI(title="Local ResearchCast Workbench", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(settings.router)
    app.include_router(inbox.router)
    app.include_router(tasks.router)
    app.include_router(knowledge.router)
    app.include_router(reminders.router)
    app.include_router(podcasts.router)
    app.include_router(briefings.router)
    app.include_router(runs.router)
    app.include_router(audit.router)
    app.include_router(search.router)
    app_settings = get_settings()
    app.mount("/media/podcasts", StaticFiles(directory=str(app_settings.podcasts_dir)), name="podcasts-media")

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()
        app.state.scheduler = start_scheduler(get_settings())

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler:
            scheduler.shutdown(wait=False)

    @app.get("/")
    def root():
        return {"name": "Local ResearchCast Workbench", "status": "ok"}

    return app


app = create_app()

