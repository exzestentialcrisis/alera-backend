from fastapi import FastAPI

from app.alerts.router import router as alert_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.patients.router import router as patient_router
from app.devices.router import router as devices_router
from app.health_events.router import router as health_event_router
from app.household_access.router import router as household_access_router
<<<<<<< HEAD
from app.auth.router import router as auth_router
from app.monitoring_devices.router import router as monitoring_device_router
=======
from app.reminders.router import router as reminder_router
>>>>>>> origin/master


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
<<<<<<< HEAD
    application.include_router(monitoring_device_router)
=======
    application.include_router(devices_router)
    application.include_router(patient_router)
    application.include_router(reminder_router)

>>>>>>> origin/master
    @application.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": app_settings.app_name,
            "environment": app_settings.environment,
        }

    return application


app = create_app()
