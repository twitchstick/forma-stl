FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FORMA_DATA=/data FORMA_LIBRARY=/library PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /data /library
COPY app.py .
COPY previews.py .
COPY library_services.py .
COPY makerworld_import.py .
COPY website_import.py .
COPY catalog_tools.py model_details.py source_metadata.py source_import.py collection_schedule.py slicers.py account.py account_admin.py ./
COPY companion ./companion
COPY static ./static
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
CMD ["python", "app.py"]
