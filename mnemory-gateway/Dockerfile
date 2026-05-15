FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8060
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8060/health')"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8060"]
