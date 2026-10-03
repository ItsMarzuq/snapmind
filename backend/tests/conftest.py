import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Make the backend project root importable.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# app.main reads these values at import time.
# Always isolate tests from the real SnapMind database/uploads.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="snapmind-tests-"))

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_ROOT / 'snapmind-test.db'}"
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / "uploads")
os.environ["SECRET_KEY"] = "snapmind-test-secret-key"

# The Docker environment may set COOKIE_SECURE=1 for production.
# TestClient normally uses http://testserver, so Secure cookies would not
# be sent back and every authenticated request would incorrectly return 401.
os.environ["COOKIE_SECURE"] = "0"

from app import main  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_files():
    yield
    main.engine.dispose()
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture()
def app_module():
    return main


@pytest.fixture()
def client():
    # Every test gets a clean database and upload directory.
    main.engine.dispose()
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)

    shutil.rmtree(main.UPLOAD_DIR, ignore_errors=True)
    main.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def png_bytes():
    def make(
        colour=(120, 160, 95),
        size=(120, 80),
    ) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", size, colour).save(buffer, format="PNG")
        return buffer.getvalue()

    return make


@pytest.fixture()
def register():
    def do_register(
        client: TestClient,
        email: str = "user@example.com",
        password: str = "password123",
    ):
        return client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": password,
            },
        )

    return do_register


@pytest.fixture()
def upload_png(png_bytes):
    def do_upload(
        client: TestClient,
        filename: str = "test.png",
        data: bytes | None = None,
    ):
        payload = data if data is not None else png_bytes()

        return client.post(
            "/api/screenshots",
            files={
                "files": (
                    filename,
                    payload,
                    "image/png",
                )
            },
        )

    return do_upload
