FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RUNNING_IN_DOCKER=1 NEXARA_DB=/app/db/nexara.db
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY data ./data
# The app runs as non-root 'app'. /app/db is a volume that may be mounted root-owned (Fly), so the start command
# fixes ownership first and then drops privileges.
RUN useradd -m -u 1000 app && mkdir -p /app/db && chown -R app:app /app/db /home/app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:'+__import__('os').environ.get('PORT','8000')+'/api/health').status==200 else 1)"
CMD ["sh", "-c", "chown -R app:app /app/db && exec setpriv --reuid=app --regid=app --init-groups env HOME=/home/app uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
