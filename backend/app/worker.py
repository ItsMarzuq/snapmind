"""Process OCR, vision, and embeddings for each screenshot."""

import base64
import io
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.exc import (
    OperationalError,
    ProgrammingError,
)

from app.embeddings import EMBED_MODEL, embed
from app.main import Screenshot, SessionLocal, UPLOAD_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("snapmind.worker")

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://host.docker.internal:11434",
).rstrip("/")
OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen3-vl:2b",
)

PROMPT = (
    "Analyze this screenshot for a personal screenshot library. "
    "Describe what is visibly shown so someone can find it later. "
    "Return JSON with these keys: description (one or two sentences), "
    "category (short, broad label), platform (website or app name, "
    "or empty string), tags (up to 8 distinctive search terms), "
    "notable_details (up to 5 specific visible details). "
    "Include names, objects, activities, places, apps, dates, and "
    "numbers only when clearly visible and relevant. Treat websites "
    "and apps separately from geographic locations. Omit uncertain "
    "details and generic buttons or navigation controls. "
    "Do not invent context."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "category": {"type": "string"},
        "platform": {"type": "string"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "notable_details": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "description",
        "category",
        "platform",
        "tags",
        "notable_details",
    ],
    "additionalProperties": False,
}


def recognize(path: Path) -> str:
    result = subprocess.run(
        [
            "tesseract",
            str(path),
            "stdout",
            "-l",
            "eng",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return result.stdout.strip()


def analyze(path: Path) -> dict:
    # Resize only the model input.
    with Image.open(path) as source:
        source.thumbnail(
            (1600, 1600),
            Image.Resampling.LANCZOS,
        )
        buffer = io.BytesIO()
        source.convert("RGB").save(
            buffer,
            format="JPEG",
            quality=85,
        )

    image = base64.b64encode(
        buffer.getvalue()
    ).decode("ascii")

    with httpx.Client(
        timeout=httpx.Timeout(300, connect=10)
    ) as client:
        for attempt in range(2):
            response = client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": PROMPT,
                    "images": [image],
                    "format": (
                        SCHEMA
                        if attempt == 0
                        else "json"
                    ),
                    "stream": False,
                    "think": False,
                    "options": {
                        "num_predict": 1024,
                    },
                },
            )
            response.raise_for_status()

            result = response.json()
            answer = result.get(
                "response",
                "",
            ).strip()

            try:
                if not answer:
                    raise ValueError(
                        "empty model response "
                        f"(done_reason="
                        f"{result.get('done_reason')})"
                    )

                data = json.loads(answer)
                break

            except (
                ValueError,
                TypeError,
            ) as exc:
                if attempt == 1:
                    raise ValueError(
                        "Ollama did not return "
                        "valid JSON after retry: "
                        f"{exc}"
                    ) from exc

                log.warning(
                    "Invalid Ollama JSON; "
                    "retrying with JSON mode: %s",
                    exc,
                )

    if (
        not isinstance(data, dict)
        or not all(
            isinstance(data.get(key), str)
            for key in (
                "description",
                "category",
                "platform",
            )
        )
    ):
        raise ValueError(
            "Ollama returned incomplete "
            "visual metadata"
        )

    for key in (
        "tags",
        "notable_details",
    ):
        if (
            not isinstance(
                data.get(key),
                list,
            )
            or not all(
                isinstance(item, str)
                for item in data[key]
            )
        ):
            raise ValueError(
                f"Ollama returned invalid {key}"
            )

    return {
        "description":
            data["description"][:1000],
        "category":
            data["category"][:80],
        "platform":
            data["platform"][:80],
        "tags": [
            item[:100]
            for item in data["tags"][:8]
        ],
        "notable_details": [
            item[:250]
            for item in data[
                "notable_details"
            ][:5]
        ],
    }


def claim_next(
    field: str,
) -> str | None:
    with SessionLocal.begin() as db:
        status = {
            "status":
                Screenshot.status,
            "vision_status":
                Screenshot.vision_status,
            "embedding_status":
                Screenshot.embedding_status,
        }[field]

        statement = select(
            Screenshot
        ).where(status == "queued")

        if field == "embedding_status":
            statement = statement.where(
                Screenshot.status.in_(
                    ("ready", "failed")
                ),
                Screenshot.vision_status.in_(
                    ("ready", "failed")
                ),
            )

        shot = db.scalar(
            statement
            .order_by(
                Screenshot.created_at,
                Screenshot.id,
            )
            .with_for_update(
                skip_locked=True
            )
            .limit(1)
        )

        if shot is None:
            return None

        setattr(
            shot,
            field,
            "processing",
        )
        return shot.id


def process_ocr(
    shot_id: str,
) -> None:
    try:
        recognized = recognize(
            UPLOAD_DIR
            / f"{shot_id}.jpg"
        )
        status, error = (
            "ready",
            None,
        )

    except (
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        log.exception(
            "OCR failed for screenshot %s",
            shot_id,
        )
        recognized = None
        status, error = (
            "failed",
            str(exc)[:1000],
        )

    with SessionLocal.begin() as db:
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.id == shot_id
            )
            .values(
                status=status,
                ocr_text=recognized,
                processing_error=error,
            )
        )

    log.info(
        "OCR %s: %s",
        shot_id,
        status,
    )


def process_vision(
    shot_id: str,
) -> None:
    try:
        metadata = analyze(
            UPLOAD_DIR
            / f"{shot_id}.jpg"
        )

        vision_data = json.dumps(
            metadata,
            ensure_ascii=False,
        )
        status, error = (
            "ready",
            None,
        )

        search_text = " ".join([
            metadata["description"],
            metadata["category"],
            metadata["platform"],
            *metadata["tags"],
            *metadata["notable_details"],
        ])

    except (
        OSError,
        ValueError,
        KeyError,
        httpx.HTTPError,
    ) as exc:
        log.exception(
            "Vision failed for "
            "screenshot %s",
            shot_id,
        )
        vision_data = None
        search_text = None
        status, error = (
            "failed",
            str(exc)[:1000],
        )

    with SessionLocal.begin() as db:
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.id == shot_id
            )
            .values(
                vision_status=status,
                vision_data=vision_data,
                vision_search_text=
                    search_text,
                vision_error=error,
                embedding_status="queued",
                embedding_json=None,
                embedding_model=None,
                embedding_error=None,
            )
        )

    log.info(
        "Vision %s: %s",
        shot_id,
        status,
    )


def process_embedding(
    shot_id: str,
) -> None:
    with SessionLocal() as db:
        shot = db.get(
            Screenshot,
            shot_id,
        )
        if shot is None:
            return

        # Put useful visual details before OCR.
        content = "\n".join(
            filter(
                None,
                [
                    shot.vision_search_text,
                    shot.ocr_text,
                    shot.filename,
                ],
            )
        )[:4000]

    try:
        vector = embed(
            content,
            "search_document",
        )
        values = {
            "embedding_status":
                "ready",
            "embedding_model":
                EMBED_MODEL,
            "embedding_json":
                json.dumps(vector),
            "embedding_error":
                None,
        }

    except (
        OSError,
        ValueError,
        KeyError,
        httpx.HTTPError,
    ) as exc:
        log.exception(
            "Embedding failed for "
            "screenshot %s",
            shot_id,
        )
        values = {
            "embedding_status":
                "failed",
            "embedding_error":
                str(exc)[:1000],
        }

    with SessionLocal.begin() as db:
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.id == shot_id
            )
            .values(**values)
        )

    log.info(
        "Embedding %s: %s",
        shot_id,
        values[
            "embedding_status"
        ],
    )


def run() -> None:
    while True:
        try:
            # Wait for the API's schema update.
            with SessionLocal.begin() as db:
                db.execute(
                    select(
                        Screenshot.status,
                        Screenshot.vision_status,
                        Screenshot.embedding_status,
                    ).limit(1)
                ).all()
            break

        except (
            OperationalError,
            ProgrammingError,
        ):
            log.info(
                "Waiting for "
                "database schema"
            )
            time.sleep(2)

    with SessionLocal.begin() as db:
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.status
                == "processing"
            )
            .values(
                status="queued"
            )
        )
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.vision_status
                == "processing"
            )
            .values(
                vision_status="queued"
            )
        )
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.embedding_status
                == "processing"
            )
            .values(
                embedding_status="queued"
            )
        )
        db.execute(
            update(Screenshot)
            .where(
                Screenshot.embedding_status
                == "ready",
                Screenshot.embedding_model
                != EMBED_MODEL,
            )
            .values(
                embedding_status="queued"
            )
        )

    log.info(
        "OCR, vision, and embedding "
        "worker started"
    )

    while True:
        try:
            ocr_id = claim_next(
                "status"
            )
            if ocr_id:
                process_ocr(ocr_id)

            vision_id = claim_next(
                "vision_status"
            )
            if vision_id:
                process_vision(vision_id)

            embed_id = claim_next(
                "embedding_status"
            )
            if embed_id:
                process_embedding(
                    embed_id
                )

            if (
                not ocr_id
                and not vision_id
                and not embed_id
            ):
                time.sleep(2)

        except Exception:
            log.exception(
                "Worker loop failed; retrying"
            )
            time.sleep(3)


if __name__ == "__main__":
    run()