FROM python:3.11

WORKDIR /src

COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY StreamTextStructurer.py ./
COPY config.json ./

CMD python -u StreamTextStructurer.py --queue-server kafka --redis-server redis
