FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# Placeholder until the FastAPI app exists (day 2)
CMD ["python", "-c", "print('agent-api: app pending (day 2)')"]
