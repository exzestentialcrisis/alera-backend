from urllib.parse import quote

import httpx

from app.core.config import Settings

MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024

ALLOWED_PROFILE_PHOTO_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class PatientPhotoStorageError(RuntimeError):
    pass


def build_profile_photo_path(
    *,
    patient_id: str,
    content_type: str,
) -> str:
    extension = ALLOWED_PROFILE_PHOTO_TYPES.get(content_type)

    if extension is None:
        raise PatientPhotoStorageError("Profile photo must be JPEG, PNG, or WebP.")

    return f"patients/{patient_id}/profile{extension}"


def public_profile_photo_url(
    *,
    settings: Settings,
    object_path: str | None,
) -> str | None:
    if not object_path or not settings.supabase_url:
        return None

    bucket = quote(
        settings.supabase_patient_photo_bucket,
        safe="",
    )
    encoded_path = quote(object_path, safe="/")

    return (
        f"{settings.supabase_url.rstrip('/')}"
        f"/storage/v1/object/public/"
        f"{bucket}/{encoded_path}"
    )


def upload_profile_photo(
    *,
    settings: Settings,
    object_path: str,
    content: bytes,
    content_type: str,
) -> None:
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise PatientPhotoStorageError("Patient photo storage is not configured.")

    secret_key = settings.supabase_secret_key.get_secret_value()
    bucket = quote(
        settings.supabase_patient_photo_bucket,
        safe="",
    )
    encoded_path = quote(object_path, safe="/")

    url = (
        f"{settings.supabase_url.rstrip('/')}"
        f"/storage/v1/object/"
        f"{bucket}/{encoded_path}"
    )

    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "apikey": secret_key,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
        content=content,
        timeout=20.0,
    )

    if response.status_code not in (200, 201):
        raise PatientPhotoStorageError(
            f"Profile photo upload failed " f"with status {response.status_code}."
        )


def delete_profile_photo(
    *,
    settings: Settings,
    object_path: str,
) -> None:
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise PatientPhotoStorageError("Patient photo storage is not configured.")

    secret_key = settings.supabase_secret_key.get_secret_value()

    url = f"{settings.supabase_url.rstrip('/')}" "/storage/v1/object"

    response = httpx.request(
        "DELETE",
        url,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "apikey": secret_key,
            "Content-Type": "application/json",
        },
        json={
            "prefixes": [f"{settings.supabase_patient_photo_bucket}/" f"{object_path}"]
        },
        timeout=20.0,
    )

    if response.status_code not in (200, 204):
        raise PatientPhotoStorageError(
            f"Profile photo deletion failed " f"with status {response.status_code}."
        )
