FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --force-reinstall bcrypt==4.3.0

# Set environment variables for better performance
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=2

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--limit-concurrency", "200", \
     "--backlog", "100", \
     "--timeout-keep-alive", "75", \
     "--timeout-graceful-shutdown", "30", \
     "--log-level", "warning"]
