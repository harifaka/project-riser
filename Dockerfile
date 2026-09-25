FROM python:3.10-slim
RUN apt-get update && apt-get install -y build-essential curl git &&     curl -fsSL https://deb.nodesource.com/setup_22.x | bash - &&     apt-get install -y nodejs
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
