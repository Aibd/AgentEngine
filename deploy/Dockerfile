# syntax=docker/dockerfile:1
#
# AgentEngine backend — FastAPI web API + sandbox orchestrator.
# It drives the host Docker daemon over the mounted socket (docker SDK), so it
# needs the Python `docker` package but NOT the docker CLI.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# 1) Dependencies first, so this layer is cached unless pyproject / the engine
#    sources change. The web API imports the web + sandbox + report stacks, so
#    all three extras are required. (If a `report` wheel ever needs a system lib
#    on your platform, add an `apt-get install ...` line above this step.)
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[web,sandbox,report]"

# 2) Application code. `app.backend.*` is not part of the installed wheel
#    (only `agentengine*` is), so it ships as plain source on PYTHONPATH=/app.
COPY app ./app

# 3) Seed skills shipped with the repo. The installed-skills list is loaded from
#    .agent/skills and .agents/skills (relative to /app); without these a fresh
#    container shows an empty skills list.
COPY .agent ./.agent
COPY .agents ./.agents

EXPOSE 8000
CMD ["uvicorn", "app.backend.services.web_api:app", "--host", "0.0.0.0", "--port", "8000"]
