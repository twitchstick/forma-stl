FROM python:3.13-slim
# MALLOC_ARENA_MAX stops glibc keeping a separate memory pool per server thread.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FORMA_DATA=/data FORMA_LIBRARY=/library PORT=8080 \
    MALLOC_ARENA_MAX=2 FORMA_WORKER_MEMORY_MB=2048
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /data /library
COPY *.py ./
COPY companion ./companion
COPY static ./static
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
CMD ["python", "app.py"]
