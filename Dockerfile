FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV LLM_BASE_URL=http://host.docker.internal:8000/v1

CMD ["python3", "src/main.py"]
