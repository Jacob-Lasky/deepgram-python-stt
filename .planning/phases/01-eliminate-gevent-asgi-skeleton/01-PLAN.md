---
phase: 01-eliminate-gevent-asgi-skeleton
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - pyproject.toml
  - uv.lock
  - Dockerfile
autonomous: true
requirements:
  - STACK-01
  - STACK-02

must_haves:
  truths:
    - "flask, flask-socketio, gevent, gevent-websocket, gunicorn, websocket-client, and requests are absent from pyproject.toml"
    - "fastapi, python-socketio[asyncio], uvicorn, and httpx are present in pyproject.toml"
    - "uv.lock is in sync with the new pyproject.toml (uv sync exits 0)"
    - "Dockerfile CMD runs uvicorn, not gunicorn"
  artifacts:
    - path: "pyproject.toml"
      provides: "dependency declarations for the new stack"
      contains: "fastapi"
    - path: "uv.lock"
      provides: "regenerated lock file matching new dependencies"
    - path: "Dockerfile"
      provides: "updated CMD using uvicorn"
      contains: "uvicorn"
  key_links:
    - from: "pyproject.toml"
      to: "uv.lock"
      via: "uv sync"
      pattern: "uv sync"
    - from: "Dockerfile CMD"
      to: "app:app"
      via: "uvicorn"
      pattern: "uvicorn app:app"
---

<objective>
Swap the seven removed packages out of pyproject.toml and the four new packages in, run `uv sync` to regenerate uv.lock, and update the Dockerfile CMD from gunicorn to uvicorn.

Purpose: The new stack packages (fastapi, python-socketio, uvicorn, httpx) must be installed before app.py can import them. The Dockerfile must reflect the correct startup command so Docker builds and Fly.io deployments do not regress.
Output: Updated pyproject.toml, regenerated uv.lock, updated Dockerfile.
</objective>

<execution_context>
@./.claude/get-shit-done/workflows/execute-plan.md
@./.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/REQUIREMENTS.md
@.planning/STATE.md
@.planning/phases/01-eliminate-gevent-asgi-skeleton/01-RESEARCH.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Swap dependencies in pyproject.toml and run uv sync</name>
  <files>pyproject.toml, uv.lock</files>
  <action>
Replace the entire `dependencies` block in pyproject.toml with the following exact content (preserving all other sections unchanged):

```toml
dependencies = [
    "deepgram-sdk==6.0.1",
    "fastapi>=0.115.0,<1",
    "python-socketio[asyncio]>=5.11.0,<6",
    "uvicorn>=0.30.0,<1",
    "httpx>=0.27.0,<1",
    "python-dotenv==1.0.0",
    "pydub>=0.25.1,<0.26",
    "sounddevice>=0.5.2,<0.6",
]
```

Packages removed: flask==3.0.0, flask-socketio==5.3.6, gevent>=23.0.0, gevent-websocket>=0.10.1, gunicorn>=21.0.0, websocket-client>=1.8.0, requests>=2.32.3,<3

Packages added: fastapi>=0.115.0,<1, python-socketio[asyncio]>=5.11.0,<6, uvicorn>=0.30.0,<1, httpx>=0.27.0,<1

After editing pyproject.toml, run from /coding/deepgram-python-stt:
```
uv sync
```

This regenerates uv.lock. Both files must be committed together.

Do NOT run `uv sync --frozen` — that would reject the changed pyproject.toml. Plain `uv sync` regenerates the lock.
  </action>
  <verify>
    <automated>cd /coding/deepgram-python-stt && uv run python -c "import fastapi, socketio, uvicorn, httpx; print('deps OK')"</automated>
  </verify>
  <done>All four new packages import without error; uv.lock is updated and committed alongside pyproject.toml</done>
</task>

<task type="auto">
  <name>Task 2: Update Dockerfile CMD to uvicorn</name>
  <files>Dockerfile</files>
  <action>
Replace the entire CMD instruction (lines 41-47) in the Dockerfile with:

```dockerfile
# Single worker required: python-socketio uses in-memory session state per process
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
```

Remove the old comment about gunicorn/geventwebsocket. Replace it with the new comment above.

The EXPOSE, ENV, and all other Dockerfile lines are unchanged.

CRITICAL: The target must be `app:app` — the `socketio.ASGIApp` instance named `app` in app.py. Do NOT use `app:fastapi_app` — that would cause all SocketIO connections to 404.
  </action>
  <verify>
    <automated>cd /coding/deepgram-python-stt && grep -n "uvicorn" Dockerfile && ! grep -n "gunicorn" Dockerfile</automated>
  </verify>
  <done>Dockerfile CMD contains uvicorn with app:app target; gunicorn is absent from the Dockerfile</done>
</task>

</tasks>

<verification>
```bash
cd /coding/deepgram-python-stt

# 1. Removed packages are gone
! grep -E "flask|gevent|gunicorn|websocket-client|requests" pyproject.toml

# 2. New packages present
grep -E "fastapi|python-socketio|uvicorn|httpx" pyproject.toml

# 3. uv.lock in sync
uv sync --check

# 4. Dockerfile uses uvicorn
grep "uvicorn" Dockerfile && ! grep "gunicorn" Dockerfile
```
</verification>

<success_criteria>
- pyproject.toml dependencies block contains exactly the 8 packages listed above — no more, no less
- uv.lock regenerated and consistent with pyproject.toml (uv sync --check exits 0)
- Dockerfile CMD runs uvicorn with app:app, --host 0.0.0.0, --port 8080, --workers 1
- `uv run python -c "import fastapi, socketio, uvicorn, httpx"` exits 0
</success_criteria>

<output>
After completion, create `.planning/phases/01-eliminate-gevent-asgi-skeleton/01-01-SUMMARY.md` with:
- What was changed (exact packages removed/added)
- uv sync result (any notable resolution conflicts)
- Dockerfile CMD before/after
</output>
