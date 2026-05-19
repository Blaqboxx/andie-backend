FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy backend source into a package directory so imports like
# andie_backend.interfaces... resolve correctly in container.
COPY . /app/andie_backend

EXPOSE 8000

CMD ["uvicorn", "andie_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
