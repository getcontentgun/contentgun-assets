# Pinned by digest as well, because slim tags are rebuilt in place. Python 3.12 is what the
# Playwright image of the service and the tests in CI run.
FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

LABEL org.opencontainers.image.source="https://github.com/getcontentgun/contentgun-assets"

WORKDIR /app

COPY requirements-mcp.txt .
RUN python -m pip install -r requirements-mcp.txt

COPY mcp/ mcp/

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin contentgun
USER contentgun

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/health', timeout=4)"]

# A script start puts mcp/ on the import path ahead of site-packages, which the flat imports
# need; the SDK package of the same name still wins, because mcp/ has no __init__.py.
CMD ["python", "mcp/remote_server.py", "--host", "0.0.0.0", "--port", "8020"]
