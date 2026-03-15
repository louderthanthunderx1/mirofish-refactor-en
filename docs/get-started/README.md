# Get Started — Run & Setup MiroFish

This guide explains how to set up and run the MiroFish project on your machine (source code or Docker).

---

## Prerequisites

Install the following and ensure they are on your `PATH`:

| Tool | Version | Purpose | Check |
|------|---------|---------|--------|
| **Node.js** | 18 or higher | Frontend (Vue, Vite) and root scripts | `node -v` |
| **npm** | Comes with Node | Install frontend and root dependencies | `npm -v` |
| **Python** | 3.11 or 3.12 | Backend (Flask) | `python --version` or `python3 --version` |
| **uv** | Latest | Backend Python packages and virtualenv | `uv --version` |

**Install uv (if needed):**

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

---

## 1. Get the project

Clone or open the repo and go to the project root:

```bash
cd /path/to/MiroFish
```

All commands below are run from this root directory unless noted.

---

## 2. Environment variables

The backend reads configuration from a **`.env`** file at the **project root**. Create it from the example:

```bash
cp .env.example .env
```

Edit **`.env`** and set at least:

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_API_KEY` | Yes | API key for the LLM (OpenAI or compatible). |
| `LLM_BASE_URL` | Yes | Base URL (e.g. `https://api.openai.com/v1` for OpenAI). |
| `LLM_MODEL_NAME` | Yes | Model name (e.g. `gpt-4o-mini` or `qwen-plus`). |
| `ZEP_API_KEY` | Yes | Zep Cloud API key for graph and memory. |

**LLM options (see `.env.example`):**

- **OpenAI (default in example):** `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL_NAME=gpt-4o-mini`. Get keys at [platform.openai.com](https://platform.openai.com/).
- **Alibaba Bailian:** Use `qwen-plus` and the DashScope URL; see the commented “Option B” block in `.env.example`. Higher usage; try simulations with fewer than 40 rounds first.

**Optional:** `LLM_BOOST_*` — only if you use a separate “boost” LLM; otherwise omit.

The backend will not start if `LLM_API_KEY` or `ZEP_API_KEY` is missing (it runs `Config.validate()` on startup).

---

## 3. Install dependencies

From the **project root**:

**Option A — One command (recommended):**

```bash
npm run setup:all
```

This runs:

1. `npm install` (root)
2. `npm install` in `frontend/`
3. `uv sync` in `backend/` (creates venv and installs Python deps)

**Option B — Step by step:**

```bash
# Root + frontend
npm run setup

# Backend (Python, creates venv automatically)
npm run setup:backend
```

---

## 4. Run the project

### Run both frontend and backend (development)

From the **project root**:

```bash
npm run dev
```

This starts:

- **Backend:** Flask on **http://localhost:5001**
- **Frontend:** Vite on **http://localhost:3000**

The frontend proxies `/api` to the backend in dev. Open **http://localhost:3000** in your browser to use the app.

### Run only backend or only frontend

```bash
# Backend only (Flask, port 5001)
npm run backend

# Frontend only (Vite, port 3000; API calls go to baseURL, e.g. localhost:5001)
npm run frontend
```

If you run frontend alone, ensure the backend is running on the URL configured in the frontend (default `http://localhost:5001`), or set `VITE_API_BASE_URL` in `.env` for the frontend build.

---

## 5. Verify

- **Frontend:** Open [http://localhost:3000](http://localhost:3000) — you should see the MiroFish landing page.
- **Backend:** Open [http://localhost:5001/health](http://localhost:5001/health) — response should be `{"status":"ok","service":"MiroFish Backend"}`.

If either fails, check the terminal for errors and that the ports 3000 and 5001 are not in use by another process.

---

## 6. Run with Docker (optional)

If you prefer to run the whole stack in Docker:

```bash
# 1. Create .env (same as above)
cp .env.example .env
# Edit .env and set LLM_API_KEY, ZEP_API_KEY, etc.

# 2. Start containers
docker compose up -d
```

The compose file uses the image `ghcr.io/666ghj/mirofish:latest`, maps ports **3000** (frontend) and **5001** (backend), and mounts `./backend/uploads` for persistence. It reads `.env` from the project root.

To use an alternative image (e.g. mirror), edit `docker-compose.yml` and uncomment or change the `image` line.

---

## 7. Production build (frontend)

To build the frontend for production (static assets):

```bash
npm run build
```

Output is in `frontend/dist/`. Serve these files with any static server and point the app’s API requests to your backend URL (e.g. via `VITE_API_BASE_URL` at build time or your reverse proxy).

---

## Quick reference

| Task | Command (from project root) |
|------|-----------------------------|
| Install all deps | `npm run setup:all` |
| Run dev (backend + frontend) | `npm run dev` |
| Run backend only | `npm run backend` |
| Run frontend only | `npm run frontend` |
| Build frontend | `npm run build` |
| Docker up | `docker compose up -d` |

| URL | Service |
|-----|---------|
| http://localhost:3000 | Frontend (Vite dev) |
| http://localhost:5001 | Backend (Flask API) |
| http://localhost:5001/health | Backend health check |

---

## Troubleshooting

- **“LLM_API_KEY / ZEP_API_KEY 未配置” (or “not configured”)**  
  Create `.env` from `.env.example` and set both keys. Backend loads `.env` from the **project root**, not from `backend/`.

- **Port 3000 or 5001 already in use**  
  Stop the process using that port or change the port (e.g. in `vite.config.js` for frontend, or `FLASK_PORT` for backend).

- **Backend fails with Python/uv errors**  
  Ensure Python 3.11 or 3.12 and run from project root: `cd backend && uv sync`.

- **Frontend cannot reach API**  
  In dev, the frontend uses Vite’s proxy to `/api` → `http://localhost:5001`. Ensure the backend is running and nothing is blocking the proxy (e.g. wrong `baseURL` in `frontend/src/api/index.js` if you changed it).

- **Docker: container exits or cannot connect**  
  Ensure `.env` exists at project root and required variables are set. Check logs: `docker compose logs -f`.

For more on the stack and config, see [Tech Stack](../tech-stack/README.md) and [Backend](../backend/README.md).
