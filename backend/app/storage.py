"""Screenshot file storage: local disk or private Supabase bucket."""

import os
from functools import lru_cache
from pathlib import Path


STORAGE_PROVIDER = os.getenv(
    "STORAGE_PROVIDER",
    "local",
).casefold()

CLOUD_STORAGE = (
    STORAGE_PROVIDER
    == "supabase"
)

UPLOAD_DIR = Path(
    os.getenv(
        "UPLOAD_DIR",
        "./uploads",
    )
)

SUPABASE_BUCKET = os.getenv(
    "SUPABASE_BUCKET",
    "screenshots",
)


def object_path(
    owner_id: str,
    shot_id: str,
) -> str:
    return (
        f"{owner_id}/"
        f"{shot_id}.jpg"
    )


@lru_cache(maxsize=1)
def _supabase():
    try:
        from supabase import (
            create_client,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Install the cloud requirements "
            "to use Supabase storage"
        ) from exc

    url = os.getenv(
        "SUPABASE_URL",
        "",
    ).strip()

    key = os.getenv(
        "SUPABASE_SECRET_KEY",
        "",
    ).strip()

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and "
            "SUPABASE_SECRET_KEY "
            "must be set"
        )

    return create_client(
        url,
        key,
    )


def save_image(
    owner_id: str,
    shot_id: str,
    data: bytes,
) -> None:
    if not CLOUD_STORAGE:
        UPLOAD_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        (
            UPLOAD_DIR
            / f"{shot_id}.jpg"
        ).write_bytes(data)

        return

    path = object_path(
        owner_id,
        shot_id,
    )

    _supabase().storage.from_(
        SUPABASE_BUCKET
    ).upload(
        path=path,
        file=data,
        file_options={
            "content-type":
                "image/jpeg",
            "cache-control":
                "3600",
            "upsert":
                "false",
        },
    )


def load_image(
    owner_id: str,
    shot_id: str,
) -> bytes:
    if not CLOUD_STORAGE:
        path = (
            UPLOAD_DIR
            / f"{shot_id}.jpg"
        )

        if not path.is_file():
            raise FileNotFoundError(
                path
            )

        return path.read_bytes()

    path = object_path(
        owner_id,
        shot_id,
    )

    try:
        data = (
            _supabase()
            .storage
            .from_(
                SUPABASE_BUCKET
            )
            .download(path)
        )
    except Exception as exc:
        message = str(
            exc
        ).casefold()

        if (
            "not found"
            in message
            or "404"
            in message
        ):
            raise FileNotFoundError(
                path
            ) from exc

        raise

    return bytes(data)


def delete_image(
    owner_id: str,
    shot_id: str,
    *,
    ignore_missing: bool = False,
) -> None:
    if not CLOUD_STORAGE:
        (
            UPLOAD_DIR
            / f"{shot_id}.jpg"
        ).unlink(
            missing_ok=(
                ignore_missing
            )
        )

        return

    path = object_path(
        owner_id,
        shot_id,
    )

    try:
        (
            _supabase()
            .storage
            .from_(
                SUPABASE_BUCKET
            )
            .remove(
                [path]
            )
        )
    except Exception:
        if ignore_missing:
            return

        raise
