FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Package named mock_erp (not app) so tests can import it next to agent-api's app package
COPY mock_erp ./mock_erp

EXPOSE 8001
CMD ["uvicorn", "mock_erp.main:app", "--host", "0.0.0.0", "--port", "8001"]
