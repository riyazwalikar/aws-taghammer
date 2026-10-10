FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# DB and fixtures are mounted volumes; created at runtime.
ENV DB_PATH=/data/demo.sqlite3 FIXTURES_DIR=/fixtures

EXPOSE 8000
# Bind loopback inside the container network; compose maps it behind the frontend.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
