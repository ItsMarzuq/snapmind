import hashlib
import io
import json
import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from starlette.staticfiles import StaticFiles

from app.embeddings import EMBED_MODEL, EmbeddingError, cosine, embed
from app.storage import (
    CLOUD_STORAGE,
    delete_image,
    load_image,
    save_image,
)

log = logging.getLogger("snapmind")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./snapmind.db")

# Supabase commonly provides postgresql:// URLs. Explicitly select psycopg 3,
# which is the PostgreSQL driver installed by requirements-cloud.txt.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = (
        "postgresql+psycopg://"
        + DATABASE_URL[len("postgresql://"):]
    )
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
MAX_BYTES = 10 * 1024 * 1024
CLOUD_MODE = os.getenv("APP_MODE", "local").casefold() == "cloud"

if os.getenv("DATABASE_URL") and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("Set SECRET_KEY before starting the API")

engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False
    } if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine)
passwords = PasswordHasher()
signer = URLSafeTimedSerializer(
    SECRET_KEY,
    salt="snapmind-session",
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255)
    )

    screenshots: Mapped[list["Screenshot"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class Screenshot(Base):
    __tablename__ = "screenshots"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(
        String(255)
    )
    format: Mapped[str] = mapped_column(
        String(10)
    )
    byte_size: Mapped[int] = mapped_column(
        Integer
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    ocr_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    processing_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    vision_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    vision_data: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    vision_search_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    vision_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    embedding_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    embedding_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    embedding_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    owner: Mapped[User] = relationship(
        back_populates="screenshots"
    )


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
    )


class ScreenshotInfo(BaseModel):
    id: str
    filename: str
    created_at: datetime
    image_url: str

    status: str
    ocr_text: str | None
    processing_error: str | None

    vision_status: str
    vision_data: dict | None
    vision_error: str | None

    embedding_status: str
    match_reason: str | None = None


class VisionEdit(BaseModel):
    description: str = Field(
        min_length=1,
        max_length=1000,
    )
    category: str = Field(
        default="",
        max_length=80,
    )
    platform: str = Field(
        default="",
        max_length=80,
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=8,
    )
    notable_details: list[str] = Field(
        default_factory=list,
        max_length=5,
    )


class UploadResult(BaseModel):
    added: list[ScreenshotInfo]
    duplicates: list[ScreenshotInfo]


app = FastAPI(
    title="SnapMind API"
)


@app.on_event("startup")
def startup():
    if not CLOUD_STORAGE:
        UPLOAD_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

    Base.metadata.create_all(
        bind=engine
    )

    with engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(
                connection
            ).get_columns("screenshots")
        }

        migrations = {
            "status":
                "ALTER TABLE screenshots "
                "ADD COLUMN status VARCHAR(20) "
                "NOT NULL DEFAULT 'queued'",
            "ocr_text":
                "ALTER TABLE screenshots "
                "ADD COLUMN ocr_text TEXT",
            "processing_error":
                "ALTER TABLE screenshots "
                "ADD COLUMN processing_error TEXT",
            "vision_status":
                "ALTER TABLE screenshots "
                "ADD COLUMN vision_status VARCHAR(20) "
                "NOT NULL DEFAULT 'queued'",
            "vision_data":
                "ALTER TABLE screenshots "
                "ADD COLUMN vision_data TEXT",
            "vision_search_text":
                "ALTER TABLE screenshots "
                "ADD COLUMN vision_search_text TEXT",
            "vision_error":
                "ALTER TABLE screenshots "
                "ADD COLUMN vision_error TEXT",
            "embedding_status":
                "ALTER TABLE screenshots "
                "ADD COLUMN embedding_status VARCHAR(20) "
                "NOT NULL DEFAULT 'queued'",
            "embedding_model":
                "ALTER TABLE screenshots "
                "ADD COLUMN embedding_model VARCHAR(100)",
            "embedding_json":
                "ALTER TABLE screenshots "
                "ADD COLUMN embedding_json TEXT",
            "embedding_error":
                "ALTER TABLE screenshots "
                "ADD COLUMN embedding_error TEXT",
            "content_hash":
                "ALTER TABLE screenshots "
                "ADD COLUMN content_hash VARCHAR(64)",
        }

        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(
                    text(statement)
                )

        # Old local databases can still backfill hashes from disk.
        # Cloud uploads always get a hash before they are saved.
        if not CLOUD_STORAGE:
            rows = connection.execute(
                text(
                    "SELECT id, owner_id "
                    "FROM screenshots "
                    "WHERE content_hash IS NULL "
                    "ORDER BY created_at, id"
                )
            ).all()

            seen = set(
                connection.execute(
                    text(
                        "SELECT owner_id, content_hash "
                        "FROM screenshots "
                        "WHERE content_hash IS NOT NULL"
                    )
                ).all()
            )

            for shot_id, owner_id in rows:
                path = (
                    UPLOAD_DIR
                    / f"{shot_id}.jpg"
                )

                if not path.is_file():
                    continue

                digest = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()

                if (
                    owner_id,
                    digest,
                ) not in seen:
                    connection.execute(
                        text(
                            "UPDATE screenshots "
                            "SET content_hash = :digest "
                            "WHERE id = :shot_id"
                        ),
                        {
                            "digest": digest,
                            "shot_id": shot_id,
                        },
                    )
                    seen.add(
                        (
                            owner_id,
                            digest,
                        )
                    )

        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_screenshots_owner_content_hash "
                "ON screenshots "
                "(owner_id, content_hash)"
            )
        )


def db_session():
    with SessionLocal() as db:
        yield db


def current_user(
    request: Request,
    db: Session = Depends(db_session),
) -> User:
    token = request.cookies.get(
        "snapmind_session"
    )

    if not token:
        raise HTTPException(
            401,
            "Sign in required",
        )

    try:
        user_id = signer.loads(
            token,
            max_age=60 * 60 * 24 * 7,
        )
    except (
        BadSignature,
        SignatureExpired,
    ):
        raise HTTPException(
            401,
            "Session expired",
        )

    user = db.get(
        User,
        user_id,
    )

    if user is None:
        raise HTTPException(
            401,
            "Sign in required",
        )

    return user


def set_session(
    response: Response,
    user: User,
):
    response.set_cookie(
        "snapmind_session",
        signer.dumps(user.id),
        httponly=True,
        samesite="lax",
        secure=os.getenv(
            "COOKIE_SECURE"
        ) == "1",
        max_age=604800,
        path="/",
    )


def info(
    shot: Screenshot,
    match_reason: str | None = None,
) -> ScreenshotInfo:
    try:
        vision_data = (
            json.loads(
                shot.vision_data
            )
            if shot.vision_data
            else None
        )
    except (
        ValueError,
        TypeError,
    ):
        vision_data = None

    return ScreenshotInfo(
        id=shot.id,
        filename=shot.filename,
        created_at=shot.created_at,
        image_url=(
            f"/api/screenshots/"
            f"{shot.id}/image"
        ),
        status=shot.status,
        ocr_text=shot.ocr_text,
        processing_error=(
            shot.processing_error
        ),
        vision_status=(
            shot.vision_status
        ),
        vision_data=vision_data,
        vision_error=(
            shot.vision_error
        ),
        embedding_status=(
            shot.embedding_status
        ),
        match_reason=match_reason,
    )


def lexical_reason(
    shot: Screenshot,
    query: str,
) -> str:
    term = query.casefold()

    if term in shot.filename.casefold():
        return "Found in filename"

    if shot.vision_data:
        try:
            data = json.loads(
                shot.vision_data
            )
        except (
            ValueError,
            TypeError,
        ):
            data = {}

        for field in (
            "description",
            "category",
            "platform",
        ):
            if term in str(
                data.get(
                    field,
                    "",
                )
            ).casefold():
                return (
                    f"Found in visual {field}"
                )

        if any(
            term in str(
                value
            ).casefold()
            for value in data.get(
                "tags",
                [],
            )
        ):
            return "Found in visual tags"

        if any(
            term in str(
                value
            ).casefold()
            for value in data.get(
                "notable_details",
                [],
            )
        ):
            return "Found in visual details"

    if term in (
        shot.ocr_text or ""
    ).casefold():
        return "Found in extracted text"

    return (
        "Similar to screenshot content"
    )


def queue_cloud_full_processing(
    background_tasks: BackgroundTasks,
    shot_id: str,
):
    if not CLOUD_MODE:
        return

    from app.cloud_processing import (
        process_screenshot,
    )

    background_tasks.add_task(
        process_screenshot,
        shot_id,
    )


def queue_cloud_embedding(
    background_tasks: BackgroundTasks,
    shot_id: str,
):
    if not CLOUD_MODE:
        return

    from app.cloud_processing import (
        process_embedding_only,
    )

    background_tasks.add_task(
        process_embedding_only,
        shot_id,
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mode": (
            "cloud"
            if CLOUD_MODE
            else "local"
        ),
    }


@app.post(
    "/api/auth/register",
    status_code=201,
)
def register(
    data: Credentials,
    response: Response,
    db: Session = Depends(db_session),
):
    email = str(
        data.email
    ).lower()

    if db.scalar(
        select(
            User.id
        ).where(
            User.email == email
        )
    ):
        raise HTTPException(
            409,
            "Email already registered",
        )

    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=passwords.hash(
            data.password
        ),
    )

    db.add(user)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            409,
            "Email already registered",
        )

    set_session(
        response,
        user,
    )

    return {
        "email": user.email
    }


@app.post("/api/auth/login")
def login(
    data: Credentials,
    response: Response,
    db: Session = Depends(db_session),
):
    user = db.scalar(
        select(User).where(
            User.email
            == str(
                data.email
            ).lower()
        )
    )

    try:
        valid = (
            user is not None
            and passwords.verify(
                user.password_hash,
                data.password,
            )
        )
    except VerifyMismatchError:
        valid = False

    if not valid:
        raise HTTPException(
            401,
            "Invalid email or password",
        )

    set_session(
        response,
        user,
    )

    return {
        "email": user.email
    }


@app.post("/api/auth/logout")
def logout(
    response: Response,
):
    response.delete_cookie(
        "snapmind_session",
        path="/",
    )

    return {
        "ok": True
    }


@app.get("/api/auth/me")
def me(
    user: User = Depends(
        current_user
    ),
):
    return {
        "email": user.email
    }


@app.get(
    "/api/screenshots",
    response_model=list[
        ScreenshotInfo
    ],
)
def list_screenshots(
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    shots = db.scalars(
        select(Screenshot)
        .where(
            Screenshot.owner_id
            == user.id
        )
        .order_by(
            Screenshot.created_at.desc(),
            Screenshot.id.desc(),
        )
    ).all()

    return [
        info(shot)
        for shot in shots
    ]


@app.get(
    "/api/screenshots/search",
    response_model=list[
        ScreenshotInfo
    ],
)
def search_screenshots(
    q: str = Query(
        min_length=1,
        max_length=120,
    ),
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    query = q.strip()

    if not query:
        return []

    escaped = (
        query
        .replace(
            "\\",
            "\\\\",
        )
        .replace(
            "%",
            "\\%",
        )
        .replace(
            "_",
            "\\_",
        )
    )

    pattern = f"%{escaped}%"

    exact_match = or_(
        Screenshot.filename.ilike(
            pattern,
            escape="\\",
        ),
        Screenshot.ocr_text.ilike(
            pattern,
            escape="\\",
        ),
        Screenshot.vision_search_text.ilike(
            pattern,
            escape="\\",
        ),
    )

    if engine.dialect.name == "postgresql":
        document = func.to_tsvector(
            text(
                "'simple'::regconfig"
            ),
            func.concat_ws(
                " ",
                Screenshot.filename,
                func.coalesce(
                    Screenshot.ocr_text,
                    "",
                ),
                func.coalesce(
                    Screenshot.vision_search_text,
                    "",
                ),
            ),
        )

        terms = (
            func.websearch_to_tsquery(
                text(
                    "'simple'::regconfig"
                ),
                query,
            )
        )

        matches = or_(
            document.op("@@")(
                terms
            ),
            exact_match,
        )

        ranking = func.ts_rank_cd(
            document,
            terms,
        ).desc()

        statement = (
            select(Screenshot)
            .where(
                Screenshot.owner_id
                == user.id,
                matches,
            )
            .order_by(
                ranking,
                Screenshot.created_at.desc(),
            )
            .limit(100)
        )

    else:
        statement = (
            select(Screenshot)
            .where(
                Screenshot.owner_id
                == user.id,
                exact_match,
            )
            .order_by(
                Screenshot.created_at.desc()
            )
            .limit(100)
        )

    lexical = db.scalars(
        statement
    ).all()

    ranked = {
        shot.id: (
            0.65
            - min(index, 30)
            * 0.005,
            shot,
        )
        for index, shot
        in enumerate(
            lexical
        )
    }

    reasons = {
        shot.id: lexical_reason(
            shot,
            query,
        )
        for shot in lexical
    }

    candidates = db.scalars(
        select(Screenshot)
        .where(
            Screenshot.owner_id
            == user.id,
            Screenshot.embedding_status
            == "ready",
            Screenshot.embedding_model
            == EMBED_MODEL,
            Screenshot.embedding_json
            .is_not(None),
        )
        .order_by(
            Screenshot.created_at.desc()
        )
        .limit(2000)
    ).all()

    if candidates:
        try:
            query_vector = embed(
                query,
                "search_query",
            )

            similarities = []

            for shot in candidates:
                similarity = cosine(
                    query_vector,
                    json.loads(
                        shot.embedding_json
                    ),
                )

                similarities.append(
                    (
                        similarity,
                        shot,
                    )
                )

            similarities.sort(
                key=lambda item:
                    item[0],
                reverse=True,
            )

            best = similarities[0][0]

            cutoff = max(
                0.48,
                best - 0.06,
            )

            max_semantic = min(
                20,
                max(
                    2,
                    math.ceil(
                        len(candidates)
                        * 0.1
                    ),
                ),
            )

            for (
                similarity,
                shot,
            ) in similarities[
                :max_semantic
            ]:
                if similarity < cutoff:
                    break

                old_score = ranked.get(
                    shot.id,
                    (
                        0,
                        shot,
                    ),
                )[0]

                ranked[shot.id] = (
                    old_score
                    + 0.65 * similarity,
                    shot,
                )

                if shot.id not in reasons:
                    try:
                        description = (
                            json.loads(
                                shot.vision_data
                            ).get(
                                "description",
                                "",
                            )
                            if shot.vision_data
                            else ""
                        )
                    except (
                        ValueError,
                        TypeError,
                    ):
                        description = ""

                    reasons[shot.id] = (
                        "Similar in meaning to: "
                        f"{description[:115]}"
                        if description
                        else (
                            "Similar in meaning "
                            "to extracted "
                            "screenshot text"
                        )
                    )

        except (
            EmbeddingError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            log.warning(
                "Semantic search unavailable; "
                "returning keyword matches: %s",
                exc,
            )

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            item[0],
            item[1].created_at,
        ),
        reverse=True,
    )

    return [
        info(
            shot,
            reasons.get(
                shot.id
            ),
        )
        for _, shot in ordered[
            :100
        ]
    ]


@app.post(
    "/api/screenshots",
    response_model=UploadResult,
    status_code=201,
)
async def upload_screenshots(
    background_tasks: BackgroundTasks,
    files: list[
        UploadFile
    ] = File(...),
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    if not 1 <= len(files) <= 20:
        raise HTTPException(
            400,
            "Upload 1 to 20 images at a time",
        )

    prepared = []

    for file in files:
        raw = await file.read(
            MAX_BYTES + 1
        )

        if len(raw) > MAX_BYTES:
            raise HTTPException(
                413,
                f"{file.filename}: "
                "image exceeds 10 MiB",
            )

        try:
            with Image.open(
                io.BytesIO(raw)
            ) as image:
                if image.format not in (
                    "PNG",
                    "JPEG",
                    "WEBP",
                ):
                    raise ValueError(
                        "Unsupported image format"
                    )

                image.verify()

            with Image.open(
                io.BytesIO(raw)
            ) as image:
                if (
                    image.width
                    * image.height
                    > 40_000_000
                ):
                    raise ValueError(
                        "Image dimensions "
                        "are too large"
                    )

                normalized = (
                    image
                    .convert("RGB")
                )

                buffer = io.BytesIO()

                normalized.save(
                    buffer,
                    format="JPEG",
                    quality=90,
                )

                data = (
                    buffer.getvalue()
                )

        except (
            UnidentifiedImageError,
            ValueError,
            OSError,
            Image.DecompressionBombError,
        ):
            raise HTTPException(
                400,
                f"{file.filename}: "
                "invalid PNG, JPEG, "
                "or WebP image",
            )

        prepared.append(
            (
                Path(
                    file.filename
                    or "screenshot"
                ).name[:255],
                data,
                hashlib.sha256(
                    data
                ).hexdigest(),
            )
        )

    saved: list[
        tuple[str, str]
    ] = []
    added = []
    duplicates = []

    try:
        for (
            filename,
            data,
            digest,
        ) in prepared:
            existing = db.scalar(
                select(
                    Screenshot
                ).where(
                    Screenshot.owner_id
                    == user.id,
                    Screenshot.content_hash
                    == digest,
                )
            )

            if existing:
                duplicates.append(
                    existing
                )
                continue

            shot = Screenshot(
                id=str(
                    uuid.uuid4()
                ),
                owner_id=user.id,
                filename=filename,
                format="jpeg",
                byte_size=len(data),
                content_hash=digest,
            )

            save_image(
                user.id,
                shot.id,
                data,
            )

            saved.append(
                (
                    user.id,
                    shot.id,
                )
            )

            try:
                with db.begin_nested():
                    db.add(shot)
                    db.flush()

            except IntegrityError:
                delete_image(
                    user.id,
                    shot.id,
                    ignore_missing=True,
                )

                saved.remove(
                    (
                        user.id,
                        shot.id,
                    )
                )

                existing = db.scalar(
                    select(
                        Screenshot
                    ).where(
                        Screenshot.owner_id
                        == user.id,
                        Screenshot.content_hash
                        == digest,
                    )
                )

                if existing is None:
                    raise

                duplicates.append(
                    existing
                )
                continue

            added.append(
                shot
            )

        db.commit()

    except Exception:
        db.rollback()

        for (
            owner_id,
            shot_id,
        ) in saved:
            try:
                delete_image(
                    owner_id,
                    shot_id,
                    ignore_missing=True,
                )
            except Exception:
                log.exception(
                    "Could not clean up "
                    "uploaded image %s",
                    shot_id,
                )

        raise

    for shot in added:
        queue_cloud_full_processing(
            background_tasks,
            shot.id,
        )

    return UploadResult(
        added=[
            info(shot)
            for shot in added
        ],
        duplicates=[
            info(shot)
            for shot in duplicates
        ],
    )


def owned_screenshot(
    shot_id: str,
    user: User,
    db: Session,
) -> Screenshot:
    shot = db.get(
        Screenshot,
        shot_id,
    )

    if (
        shot is None
        or shot.owner_id
        != user.id
    ):
        raise HTTPException(
            404,
            "Screenshot not found",
        )

    return shot


@app.patch(
    "/api/screenshots/"
    "{shot_id}/vision",
    response_model=ScreenshotInfo,
)
def edit_vision(
    shot_id: str,
    data: VisionEdit,
    background_tasks: BackgroundTasks,
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    shot = owned_screenshot(
        shot_id,
        user,
        db,
    )

    if (
        shot.vision_status
        not in (
            "ready",
            "failed",
        )
        or shot.embedding_status
        == "processing"
    ):
        raise HTTPException(
            409,
            "Wait for visual analysis "
            "to finish",
        )

    description = (
        data.description.strip()
    )

    if not description:
        raise HTTPException(
            422,
            "Description cannot be empty",
        )

    tags = [
        tag.strip()
        for tag in data.tags
        if tag.strip()
    ]

    details = [
        detail.strip()
        for detail
        in data.notable_details
        if detail.strip()
    ]

    if (
        any(
            len(tag) > 100
            for tag in tags
        )
        or any(
            len(detail) > 250
            for detail in details
        )
    ):
        raise HTTPException(
            422,
            "Tag or detail is too long",
        )

    metadata = {
        "description":
            description,
        "category":
            data.category.strip(),
        "platform":
            data.platform.strip(),
        "tags":
            tags,
        "notable_details":
            details,
    }

    shot.vision_data = json.dumps(
        metadata,
        ensure_ascii=False,
    )

    shot.vision_search_text = (
        " ".join(
            [
                description,
                metadata["category"],
                metadata["platform"],
                *tags,
                *details,
            ]
        )
    )

    shot.vision_status = "ready"
    shot.vision_error = None

    shot.embedding_status = "queued"
    shot.embedding_json = None
    shot.embedding_model = None
    shot.embedding_error = None

    db.commit()
    db.refresh(shot)

    queue_cloud_embedding(
        background_tasks,
        shot.id,
    )

    return info(shot)


@app.post(
    "/api/screenshots/"
    "{shot_id}/retry",
    response_model=ScreenshotInfo,
)
def retry_failed(
    shot_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    shot = owned_screenshot(
        shot_id,
        user,
        db,
    )

    retry_ocr = (
        shot.status == "failed"
    )

    retry_vision = (
        shot.vision_status
        == "failed"
    )

    retry_embedding = (
        shot.embedding_status
        == "failed"
    )

    if not (
        retry_ocr
        or retry_vision
        or retry_embedding
    ):
        raise HTTPException(
            409,
            "No failed processing to retry",
        )

    if (
        shot.embedding_status
        == "processing"
    ):
        raise HTTPException(
            409,
            "Wait for search indexing "
            "to finish",
        )

    full_retry = (
        retry_ocr
        or retry_vision
    )

    if retry_ocr:
        shot.status = "queued"
        shot.processing_error = None

    if retry_vision:
        shot.vision_status = "queued"
        shot.vision_error = None

    if (
        retry_ocr
        or retry_vision
        or retry_embedding
    ):
        shot.embedding_status = "queued"
        shot.embedding_json = None
        shot.embedding_model = None
        shot.embedding_error = None

    db.commit()
    db.refresh(shot)

    if CLOUD_MODE:
        if full_retry:
            queue_cloud_full_processing(
                background_tasks,
                shot.id,
            )
        else:
            queue_cloud_embedding(
                background_tasks,
                shot.id,
            )

    return info(shot)


@app.get(
    "/api/screenshots/"
    "{shot_id}/image"
)
def get_image(
    shot_id: str,
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    shot = owned_screenshot(
        shot_id,
        user,
        db,
    )

    try:
        data = load_image(
            shot.owner_id,
            shot.id,
        )
    except FileNotFoundError:
        raise HTTPException(
            404,
            "Image missing",
        )

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control":
                "private, max-age=3600",
        },
    )


@app.delete(
    "/api/screenshots/"
    "{shot_id}",
    status_code=204,
)
def delete_screenshot(
    shot_id: str,
    user: User = Depends(
        current_user
    ),
    db: Session = Depends(
        db_session
    ),
):
    shot = owned_screenshot(
        shot_id,
        user,
        db,
    )

    owner_id = shot.owner_id
    saved_id = shot.id

    db.delete(shot)
    db.commit()

    try:
        delete_image(
            owner_id,
            saved_id,
            ignore_missing=True,
        )
    except Exception:
        log.exception(
            "Screenshot row deleted but "
            "storage cleanup failed: %s",
            saved_id,
        )

    return Response(
        status_code=204
    )


# Render production builds place the exported Next.js site here.
# Mounting after the API routes preserves /api/* routing.
static_dir_value = os.getenv(
    "STATIC_DIR",
    "",
).strip()

if static_dir_value:
    static_dir = Path(
        static_dir_value
    )

    if static_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(
                directory=static_dir,
                html=True,
            ),
            name="frontend",
        )
