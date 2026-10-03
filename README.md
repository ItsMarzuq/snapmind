# SnapMind

A local-first screenshot library. This first milestone supports account creation, sign-in, bulk upload, a private gallery, image viewing, and deletion. AI processing and search are the next milestones; the interface does not claim they work yet.

## Run with Docker

1. Copy `.env.example` to `.env` and replace `SECRET_KEY` with a random value (for example, `python -c 'import secrets; print(secrets.token_urlsafe(48))'`).
2. Run `docker compose up --build`.
3. Open http://localhost:3000, create an account, and upload PNG, JPEG, or WebP screenshots.

Docker volumes keep images and PostgreSQL records across restarts. `docker compose down -v` removes both volumes and all uploaded data.

## Development

Start Postgres with `docker compose up -d db`. For the API, install `backend/requirements.txt`, set `DATABASE_URL` to `postgresql+psycopg://snapmind:snapmind@localhost:5432/snapmind`, set `SECRET_KEY` and `UPLOAD_DIR`, then run `uvicorn app.main:app --reload` from `backend/`. For the UI, run `npm install && npm run dev` from `frontend/`; `/api/*` is proxied to the local API.

## Current limits

- The first milestone has no OCR, embeddings, search, filters, or background jobs.
- Uploads are limited to 10 MiB per image and 20 images per request. The server validates and normalizes image contents.
- The local demo uses HTTP on localhost. Configure HTTPS and secure cookies before any public deployment.
- Database tables are created at startup for the prototype; add migrations before schema changes.

## Next milestone

Add an asynchronous processing worker and a screenshot state (`queued`, `processing`, `ready`, `failed`). Store OCR and model output independently, then create deterministic embedding text. Keep retrieval and reranking separate so search quality can be evaluated.
