FROM python:3.11

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN apt update && apt install -y ffmpeg

COPY server.py api.yaml ./

ENV QUEUE_SERVER=rabbitmq
ENV REDIS_SERVER=redis
ENV PYTHONUNBUFFERED=TRUE
CMD gunicorn server:app --worker-class gevent --bind 0.0.0.0:5000
