FROM python:3.11

WORKDIR /src
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y ffmpeg

COPY StreamTTS.py ./
COPY config.json ./
# COPY Alex_voice.wav ./

CMD python -u StreamTTS.py --queue-server rabbitmq --redis-server redis
