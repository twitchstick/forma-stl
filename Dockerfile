FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FORMA_DATA=/data FORMA_LIBRARY=/library PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /data /library
COPY app.py .
COPY previews.py .
COPY library_services.py .
COPY static ./static
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import os,urllib.request,base64; r=urllib.request.Request('http://127.0.0.1:8080/health'); p=os.environ.get('FORMA_PASSWORD',''); r.add_header('Authorization','Basic '+base64.b64encode((os.environ.get('FORMA_USERNAME','admin')+':'+p).encode()).decode()) if p else None; urllib.request.urlopen(r,timeout=3)"
CMD ["python", "app.py"]
