FROM python:3.11

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt


COPY StreamMT.py ./
COPY config.json ./

CMD python -u StreamMT.py --queue-server kafka --queue-port 9092 --redis-server redis
