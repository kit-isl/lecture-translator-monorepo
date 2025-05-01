FROM python:3

WORKDIR /src

COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY db.py ./
COPY server.py ./
COPY templates/ ./
COPY templates/* ./templates/

ENV REDIS_SERVER=redis
CMD uvicorn --workers 4 --host 0.0.0.0 --port 5000 server:app
