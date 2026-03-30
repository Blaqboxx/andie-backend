FROM python:3.11-slim

WORKDIR /app

RUN useradd -m sandboxuser
USER sandboxuser

COPY runner.py /app/runner.py

CMD ["python", "runner.py"]
