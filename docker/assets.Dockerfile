# The tag must equal the playwright pin in requirements.txt: the browsers in the image belong to it.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    BUNDLES_DIR=/bundles

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install -r requirements.txt

COPY app/ app/

USER pwuser

EXPOSE 8010

# /health answers 503 until Chromium is up, and urlopen raises on it, so the check fails until then.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=4)"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
