FROM python:3.11

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt


COPY logger.py ./

CMD python logger.py --queue-server rabbitmq --redis-server redis --location /logs/
