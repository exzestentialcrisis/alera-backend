from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.alerts.router import router as alert_router
from app.health_events.router import router as health_event_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
    )
    application.state.settings = app_settings
    application.include_router(health_event_router)
    application.include_router(alert_router)

    @application.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": app_settings.app_name,
            "environment": app_settings.environment,
        }

    return application


app = create_app()
