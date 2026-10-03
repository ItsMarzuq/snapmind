"""Shared embeddings for local Ollama or Render/Gemini."""

import math
import os


AI_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "ollama",
).casefold()

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://host.docker.internal:11434",
).rstrip("/")

LOCAL_EMBED_MODEL = os.getenv(
    "OLLAMA_EMBED_MODEL",
    os.getenv(
        "EMBED_MODEL",
        "nomic-embed-text",
    ),
)

GEMINI_EMBED_MODEL = os.getenv(
    "GEMINI_EMBED_MODEL",
    "gemini-embedding-2",
)

GEMINI_EMBED_DIMENSIONS = int(
    os.getenv(
        "GEMINI_EMBED_DIMENSIONS",
        "768",
    )
)

EMBED_MODEL = (
    GEMINI_EMBED_MODEL
    if AI_PROVIDER == "gemini"
    else LOCAL_EMBED_MODEL
)


class EmbeddingError(ValueError):
    """Embedding provider failed."""


def _ollama_embed(
    text: str,
    role: str,
) -> list[float]:
    import httpx

    with httpx.Client(
        timeout=httpx.Timeout(
            90,
            connect=10,
        )
    ) as client:
        response = client.post(
            f"{OLLAMA_URL}/api/embed",
            json={
                "model":
                    LOCAL_EMBED_MODEL,
                "input":
                    f"{role}: {text}",
            },
        )

        response.raise_for_status()

        vector = response.json()[
            "embeddings"
        ][0]

    return vector


def _gemini_embed(
    text: str,
    role: str,
) -> list[float]:
    from google import genai
    from google.genai import types

    api_key = os.getenv(
        "GEMINI_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise EmbeddingError(
            "GEMINI_API_KEY is not set"
        )

    # Gemini Embedding 2 uses prompt prefixes rather than task_type.
    if role == "search_query":
        prepared = (
            "task: search result | "
            f"query: {text}"
        )
    else:
        prepared = (
            "title: screenshot | "
            f"text: {text}"
        )

    try:
        client = genai.Client(
            api_key=api_key
        )

        result = (
            client.models.embed_content(
                model=GEMINI_EMBED_MODEL,
                contents=prepared,
                config=(
                    types.EmbedContentConfig(
                        output_dimensionality=(
                            GEMINI_EMBED_DIMENSIONS
                        )
                    )
                ),
            )
        )

        if not result.embeddings:
            raise ValueError(
                "Gemini returned no embedding"
            )

        vector = (
            result.embeddings[0].values
        )

    except Exception as exc:
        raise EmbeddingError(
            f"Gemini embedding failed: {exc}"
        ) from exc

    return list(vector)


def embed(
    text: str,
    role: str,
) -> list[float]:
    if role not in (
        "search_query",
        "search_document",
    ):
        raise ValueError(
            "Unsupported embedding role"
        )

    try:
        vector = (
            _gemini_embed(
                text,
                role,
            )
            if AI_PROVIDER
            == "gemini"
            else _ollama_embed(
                text,
                role,
            )
        )
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(
            f"Embedding failed: {exc}"
        ) from exc

    if (
        not isinstance(
            vector,
            list,
        )
        or not vector
        or not all(
            isinstance(
                value,
                (
                    int,
                    float,
                ),
            )
            and math.isfinite(
                value
            )
            for value in vector
        )
    ):
        raise EmbeddingError(
            "Embedding provider returned "
            "an invalid vector"
        )

    return [
        float(value)
        for value in vector
    ]


def cosine(
    first: list[float],
    second: list[float],
) -> float:
    if (
        len(first)
        != len(second)
        or not first
    ):
        return 0.0

    first_length = math.sqrt(
        math.fsum(
            value * value
            for value in first
        )
    )

    second_length = math.sqrt(
        math.fsum(
            value * value
            for value in second
        )
    )

    if (
        not first_length
        or not second_length
    ):
        return 0.0

    return math.fsum(
        a * b
        for a, b
        in zip(
            first,
            second,
        )
    ) / (
        first_length
        * second_length
    )
