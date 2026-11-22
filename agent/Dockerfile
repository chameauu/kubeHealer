FROM python:3.11-slim AS deps
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/deps:/app/src
WORKDIR /app
COPY --from=deps /install/lib/python3.11/site-packages /app/deps
COPY src /app/src
# The authored diagnosis cassette ships with the image so LLM_MODE=cassette
# works without a key; CASSETTE_PATH defaults to this relative path.
COPY fixtures /app/fixtures
RUN useradd --uid 1000 --create-home appuser && chown -R 1000:1000 /app
USER 1000
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/src", "--host", "0.0.0.0", "--port", "8080"]
