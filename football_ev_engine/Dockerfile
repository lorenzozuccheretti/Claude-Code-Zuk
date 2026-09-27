FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# DuckDB (history, odds, sent picks) lives here; mount a volume to keep it.
VOLUME ["/app/data"]

CMD ["python", "main.py", "agent", "schedule"]
