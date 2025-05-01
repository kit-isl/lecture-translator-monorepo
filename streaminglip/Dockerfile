FROM python:3.11

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y ffmpeg

COPY StreamLipSync.py ./
COPY config.json ./
#COPY video.mp4 ./

CMD python -u StreamLipSync.py --queue-server rabbitmq --redis-server redis
