FROM python:3.8.10

WORKDIR /src

COPY requirements.txt requirements.txt
COPY pretrained ./pretrained

RUN python -m pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
RUN python -m pip install --no-cache-dir ./pretrained/deepfilternet-0.4.0-py3-none-any.whl

RUN mkdir -p /src/cache
# RUN chmod 777 /src/cache

RUN apt-get -y update
RUN apt-get -y upgrade
RUN apt-get install -y ffmpeg

COPY src ./src
COPY pretrained ./pretrained
COPY StreamPrep.py se_infer_utils.py voice_filter_utils.py ./

CMD python -u StreamPrep.py --queue-server kafka --queue-port 9092 --redis-server redis

