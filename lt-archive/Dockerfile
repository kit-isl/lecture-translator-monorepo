FROM python:3

RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY ltarchive/ ./ltarchive/

HEALTHCHECK --interval=1m --timeout=1m --retries=3 --start-period=5m \
    CMD ["curl", "-f", "http://localhost:5000/ltarchive/health"]

ENV PYTHONUNBUFFERED=TRUE
CMD ["gunicorn", "ltarchive:create_app()", "--worker-class", "gevent", "--bind", "0.0.0.0:5000"]
