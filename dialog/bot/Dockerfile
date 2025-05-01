FROM python:3.11

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt


COPY apps.json ./
COPY kitcat/ai4lt.content.txt ./kitcat/bot
COPY childRobot/content.txt ./childRobot/bot
COPY bot ./bot/

COPY server.py ./

CMD python -u server.py --queue-server rabbitmq --redis-server redis
