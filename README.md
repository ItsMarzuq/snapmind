# SnapMind

**Turn your screenshots into a searchable personal memory.**

SnapMind is a privacy-friendly screenshot memory app that lets you upload screenshots and find them later using natural-language-style searches. Instead of manually scrolling through hundreds of images, SnapMind extracts useful information from screenshots and makes that information searchable.

The project is designed around a simple constraint: **the core app should be usable without paid AI APIs, paid vector databases, or paid GPU infrastructure.**

---

## What SnapMind Does

SnapMind helps you find screenshots based on what they contain rather than only their file names.

Example searches:

- `Find the headphones I wanted`
- `Show me screenshots about recipes`
- `Find screenshots with upcoming deadlines`
- `Show screenshots from my London trip`
- `Find the screenshot mentioning Docker`
- `Show me screenshots related to job applications`

---

## Features

- **User authentication** — register, log in, and log out
- **Screenshot uploads** — add screenshots to your personal library
- **Automatic OCR** — extract text from uploaded screenshots
- **Natural-language-style search** — search screenshots using everyday queries
- **Search ranking** — return the most relevant screenshots first
- **Filters and metadata** — narrow results using available screenshot information
- **Screenshot library** — browse previously uploaded screenshots
- **Persistent database storage** — PostgreSQL-compatible application data
- **Docker support** — run the project consistently across environments
- **Responsive web interface** — designed for desktop and mobile use
- **Screenshot Q&A** — experimental conversational retrieval over stored screenshot information

> The screenshot Q&A feature is still experimental. The core upload, OCR, storage, and search workflow is the primary stable experience.

---

## How It Works

When a screenshot is uploaded, SnapMind processes it and stores searchable information about the image.

```text
Screenshot
    |
    v
Upload
    |
    v
OCR / information extraction
    |
    v
Text + metadata processing
    |
    v
Database storage
    |
    v
Query matching and ranking
    |
    v
Relevant screenshots
```

This allows searches to focus on the **content of the screenshot**, not just the original image filename.

---

## Tech Stack

### Backend

- Python
- FastAPI
- Uvicorn
- PostgreSQL
- OCR-based screenshot text extraction

### Frontend

- HTML
- CSS
- JavaScript
- Responsive web UI

### Infrastructure

- Docker
- Docker Compose
- Environment-based configuration
- PostgreSQL-compatible deployment configuration

### Testing

- Pytest

---

## Why I Built It

Screenshots are one of the easiest ways to save information, but they quickly become difficult to organize.

A screenshot might contain:

- a product you want to buy,
- an event date,
- a restaurant recommendation,
- a job posting,
- a recipe,
- an error message,
- a travel plan,
- or something you simply want to remember later.

Traditional photo galleries usually expect you to remember **when** you took the screenshot.

SnapMind instead tries to answer:

> **“What was in the screenshot I am looking for?”**

---

## Built With a €0-First Approach

One of the main goals of SnapMind is to avoid requiring expensive AI infrastructure for the core product.

The architecture is designed to work without depending on:

- paid LLM APIs,
- paid OCR APIs,
- paid vector databases,
- dedicated GPU hosting,
- or proprietary cloud AI services.

This makes SnapMind suitable as a lightweight personal tool while keeping the architecture open to more advanced retrieval models in the future.

---

## Running Locally

### Prerequisites

Install:

- Git
- Docker
- Docker Compose

### 1. Clone the repository

```bash
git clone <repository-url>
cd snapmind
```

### 2. Configure environment variables

Create or configure the environment file used by the application.

At minimum, the deployed PostgreSQL configuration expects a database URL in the standard form:

```env
DATABASE_URL=postgresql://username:password@host:port/database
```

Do not commit real credentials or production secrets to GitHub.

### 3. Start SnapMind

```bash
docker compose up --build
```

Docker will build and start the services defined in the project.

Use the local URL exposed by the web service in `docker-compose.yml` to open SnapMind.

### 4. Stop the application

```bash
docker compose down
```

---

## Running Tests

Run the automated tests with:

```bash
pytest
```

or, when running tests inside the Docker environment:

```bash
docker compose exec api pytest
```

---

## Example Workflow

1. Create an account.
2. Log in.
3. Upload several screenshots.
4. SnapMind extracts searchable information from them.
5. Search using a phrase such as:

```text
find the screenshot about headphones
```

6. SnapMind ranks and returns the most relevant matching screenshots.

---

## Privacy

Screenshots can contain highly personal information, so privacy is an important part of SnapMind's design direction.

The project is intentionally built so its core intelligence can run without sending screenshot content to paid third-party AI APIs.

For production deployments, users should additionally configure:

- secure secrets,
- HTTPS,
- appropriate database access controls,
- private file storage,
- and regular backups.

---

## Roadmap

Potential future improvements include:

- stronger semantic and multimodal retrieval
- improved screenshot Q&A accuracy
- automatic screenshot categories and tags
- duplicate screenshot detection
- better date, location, and entity extraction
- browser and mobile upload integrations
- local embedding-based search
- smarter result explanations
- improved privacy controls
- production deployment and monitoring improvements

---

## Project Status

SnapMind currently supports the main end-to-end MVP workflow:

**authentication → screenshot upload → OCR processing → storage → search → retrieval**

The project is actively being improved, particularly around search quality, UI polish, and conversational Q&A.

---

## Author

**Marzuq Islam**

MSc Computer Science graduate from University College Dublin, with interests in artificial intelligence, machine learning, cloud systems, and building practical AI-powered products.

---

## About the Project

SnapMind started from a simple idea:

> **Screenshots should be searchable by meaning, not just by date.**

If you find the project useful, consider starring the repository.