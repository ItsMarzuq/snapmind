import json

from sqlalchemy import select


def test_protected_routes_require_sign_in(client):
    response = client.get("/api/screenshots")

    assert response.status_code == 401
    assert response.json()["detail"] == "Sign in required"


def test_register_me_logout_and_login(client, register):
    response = register(client)

    assert response.status_code == 201
    assert response.json()["email"] == "user@example.com"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"email": "user@example.com"}

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}

    after_logout = client.get("/api/auth/me")
    assert after_logout.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={
            "email": "USER@EXAMPLE.COM",
            "password": "password123",
        },
    )
    assert login.status_code == 200

    me_again = client.get("/api/auth/me")
    assert me_again.status_code == 200


def test_duplicate_registration_and_wrong_password(client, register):
    assert register(client).status_code == 201

    duplicate = register(client)
    assert duplicate.status_code == 409

    client.post("/api/auth/logout")

    wrong_password = client.post(
        "/api/auth/login",
        json={
            "email": "user@example.com",
            "password": "definitely-wrong",
        },
    )

    assert wrong_password.status_code == 401


def test_valid_screenshot_upload_and_listing(
    client,
    register,
    upload_png,
):
    register(client)

    response = upload_png(
        client,
        filename="shopping.png",
    )

    assert response.status_code == 201

    body = response.json()
    assert len(body["added"]) == 1
    assert body["duplicates"] == []

    shot = body["added"][0]
    assert shot["filename"] == "shopping.png"
    assert shot["status"] == "queued"
    assert shot["vision_status"] == "queued"
    assert shot["embedding_status"] == "queued"

    listing = client.get("/api/screenshots")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["id"] == shot["id"]

    image = client.get(shot["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/jpeg")


def test_duplicate_upload_is_not_added_twice(
    client,
    register,
    upload_png,
    png_bytes,
):
    register(client)

    image = png_bytes()

    first = upload_png(
        client,
        filename="first.png",
        data=image,
    )
    second = upload_png(
        client,
        filename="same-image-again.png",
        data=image,
    )

    assert first.status_code == 201
    assert second.status_code == 201

    result = second.json()
    assert result["added"] == []
    assert len(result["duplicates"]) == 1

    listing = client.get("/api/screenshots")
    assert len(listing.json()) == 1


def test_invalid_file_is_rejected(client, register):
    register(client)

    response = client.post(
        "/api/screenshots",
        files={
            "files": (
                "not-an-image.txt",
                b"hello, this is not an image",
                "text/plain",
            )
        },
    )

    assert response.status_code == 400
    assert "invalid PNG, JPEG, or WebP image" in response.json()["detail"]


def test_oversized_file_is_rejected(
    client,
    register,
    app_module,
    monkeypatch,
):
    register(client)

    # Lower the limit during this test so we do not need to allocate 10+ MiB.
    monkeypatch.setattr(app_module, "MAX_BYTES", 100)

    response = client.post(
        "/api/screenshots",
        files={
            "files": (
                "huge.png",
                b"x" * 101,
                "image/png",
            )
        },
    )

    assert response.status_code == 413
    assert "exceeds 10 MiB" in response.json()["detail"]


def test_keyword_search_uses_ocr_and_visual_metadata(
    client,
    register,
    upload_png,
    app_module,
):
    register(client)

    uploaded = upload_png(
        client,
        filename="saved-product.png",
    ).json()["added"][0]

    with app_module.SessionLocal.begin() as db:
        shot = db.get(
            app_module.Screenshot,
            uploaded["id"],
        )
        shot.status = "ready"
        shot.ocr_text = "Sony WH-CH720N wireless headphones €75.57"
        shot.vision_status = "ready"
        shot.vision_data = json.dumps(
            {
                "description": "A shopping page for Sony headphones.",
                "category": "Shopping",
                "platform": "Amazon",
                "tags": ["Sony", "headphones", "shopping"],
                "notable_details": ["Price €75.57"],
            }
        )
        shot.vision_search_text = (
            "A shopping page for Sony headphones. "
            "Shopping Amazon Sony headphones shopping Price €75.57"
        )

    response = client.get(
        "/api/screenshots/search",
        params={"q": "Sony"},
    )

    assert response.status_code == 200

    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == uploaded["id"]
    assert results[0]["match_reason"]


def test_editing_visual_metadata_requeues_embedding(
    client,
    register,
    upload_png,
    app_module,
):
    register(client)

    uploaded = upload_png(client).json()["added"][0]

    with app_module.SessionLocal.begin() as db:
        shot = db.get(
            app_module.Screenshot,
            uploaded["id"],
        )
        shot.vision_status = "ready"
        shot.embedding_status = "ready"
        shot.embedding_model = app_module.EMBED_MODEL
        shot.embedding_json = "[1.0, 0.0]"

    response = client.patch(
        f"/api/screenshots/{uploaded['id']}/vision",
        json={
            "description": "Train ticket to Dublin Airport",
            "category": "Travel",
            "platform": "Aircoach",
            "tags": ["airport", "coach"],
            "notable_details": ["Terminal 2"],
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["vision_data"]["category"] == "Travel"
    assert body["vision_data"]["platform"] == "Aircoach"
    assert body["embedding_status"] == "queued"

    with app_module.SessionLocal() as db:
        shot = db.get(
            app_module.Screenshot,
            uploaded["id"],
        )
        assert shot.embedding_json is None
        assert shot.embedding_model is None
        assert "Terminal 2" in shot.vision_search_text


def test_retry_failed_processing(
    client,
    register,
    upload_png,
    app_module,
):
    register(client)

    uploaded = upload_png(client).json()["added"][0]

    with app_module.SessionLocal.begin() as db:
        shot = db.get(
            app_module.Screenshot,
            uploaded["id"],
        )
        shot.status = "failed"
        shot.processing_error = "OCR failed"
        shot.vision_status = "failed"
        shot.vision_error = "Vision failed"
        shot.embedding_status = "failed"
        shot.embedding_error = "Embedding failed"

    response = client.post(
        f"/api/screenshots/{uploaded['id']}/retry"
    )

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "queued"
    assert body["vision_status"] == "queued"
    assert body["embedding_status"] == "queued"

    with app_module.SessionLocal() as db:
        shot = db.get(
            app_module.Screenshot,
            uploaded["id"],
        )
        assert shot.processing_error is None
        assert shot.vision_error is None
        assert shot.embedding_error is None


def test_retry_rejects_screenshot_with_no_failures(
    client,
    register,
    upload_png,
):
    register(client)

    uploaded = upload_png(client).json()["added"][0]

    response = client.post(
        f"/api/screenshots/{uploaded['id']}/retry"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "No failed processing to retry"


def test_users_cannot_access_each_others_screenshots(
    client,
    register,
    upload_png,
):
    register(
        client,
        email="first@example.com",
    )

    uploaded = upload_png(
        client,
        filename="private.png",
    ).json()["added"][0]

    client.post("/api/auth/logout")

    register(
        client,
        email="second@example.com",
    )

    listing = client.get("/api/screenshots")
    assert listing.status_code == 200
    assert listing.json() == []

    image = client.get(
        f"/api/screenshots/{uploaded['id']}/image"
    )
    assert image.status_code == 404

    deletion = client.delete(
        f"/api/screenshots/{uploaded['id']}"
    )
    assert deletion.status_code == 404


def test_delete_removes_database_record_and_image(
    client,
    register,
    upload_png,
    app_module,
):
    register(client)

    uploaded = upload_png(
        client,
        filename="delete-me.png",
    ).json()["added"][0]

    image_path = (
        app_module.UPLOAD_DIR
        / f"{uploaded['id']}.jpg"
    )

    assert image_path.is_file()

    response = client.delete(
        f"/api/screenshots/{uploaded['id']}"
    )

    assert response.status_code == 204
    assert not image_path.exists()

    with app_module.SessionLocal() as db:
        shot = db.scalar(
            select(app_module.Screenshot).where(
                app_module.Screenshot.id
                == uploaded["id"]
            )
        )
        assert shot is None

    assert client.get("/api/screenshots").json() == []
