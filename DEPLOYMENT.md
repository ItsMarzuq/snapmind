# SnapMind Render deployment files

These files add a cloud deployment path without requiring you to remove the
existing local Ollama/worker setup.

## Replace/add

backend/app/main.py
backend/app/embeddings.py
backend/app/storage.py              (new)
backend/app/cloud_processing.py     (new)
backend/requirements-cloud.txt      (new)

frontend/next.config.ts

Dockerfile.render                    (project root)
render.yaml                          (project root)
.env.render.example                  (project root)

Your existing backend/app/worker.py can stay unchanged. Render does not start it.

## Cloud flow

Upload -> private Supabase Storage -> FastAPI BackgroundTask
       -> Gemini 3.5 Flash-Lite extracts visible text + metadata
       -> Gemini Embedding 2 builds a 768-dimensional search embedding
       -> Supabase Postgres stores metadata and vectors as JSON

## Local flow

Your normal docker-compose setup can keep APP_MODE=local,
STORAGE_PROVIDER=local and AI_PROVIDER=ollama.

The worker container continues to process local screenshots using your current
Ollama setup.

## Before deploying

1. Put your Supabase Session Pooler URL in DATABASE_URL.
2. Ensure the database URL uses SSL if Supabase's copied string requires it.
3. Generate a strong SECRET_KEY.
4. Keep the screenshots bucket private.
5. Never commit SUPABASE_SECRET_KEY or GEMINI_API_KEY.
