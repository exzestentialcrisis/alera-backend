import asyncio

from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI

from app.alerts.router import router as alert_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.patients.router import router as patient_router
from app.devices.router import router as devices_router
from app.health_events.router import router as health_event_router
from app.household_access.router import router as household_access_router
from app.monitoring_devices.router import router as monitoring_device_router
from app.reminders.router import router as reminder_router
from app.reminders.template_router import router as reminder_template_router
from app.reminders.execution_router import router as reminder_execution_router
from app.nudges.router import router as nudge_router

from app.activity.router import router as activity_router
from app.monitoring_devices.runner import (device_liveness_loop, stop_device_liveness_task,)
from app.activity.runner import (inactivity_schedule_loop,stop_inactivity_task,)

@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = application.state.settings

    device_task = asyncio.create_task(
    device_liveness_loop(
        settings
        )
    )

    inactivity_task = asyncio.create_task(
    inactivity_schedule_loop(
        settings
        )
    )

    try:
        yield
        
    finally:
        await stop_device_liveness_task(
            device_task
        )

        await stop_inactivity_task(
            inactivity_task
        )

def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.dependency_overrides[get_settings] = lambda: app_settings
    application.include_router(health_event_router)
    application.include_router(alert_router)
    application.include_router(household_access_router)
    application.include_router(auth_router)
    application.include_router(monitoring_device_router)
    application.include_router(devices_router)
    application.include_router(patient_router)
    application.include_router(reminder_router)
    application.include_router(reminder_template_router)
    application.include_router(reminder_execution_router)
    application.include_router(nudge_router)
    application.include_router(activity_router)

    @application.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": app_settings.app_name,
            "environment": app_settings.environment,
        }

    return application


app = create_app()
