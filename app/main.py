from fastapi import FastAPI

from app.alerts.router import router as alert_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.devices.router import router as devices_router
from app.health_events.router import router as health_event_router
from app.household_access.router import router as household_access_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
    )
    application.state.settings = app_settings
    application.dependency_overrides[get_settings] = lambda: app_settings
    application.include_router(health_event_router)
    application.include_router(alert_router)
    application.include_router(household_access_router)
    application.include_router(auth_router)
    application.include_router(devices_router)

    @application.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": app_settings.app_name,
            "environment": app_settings.environment,
        }

    return application


app = create_app()
