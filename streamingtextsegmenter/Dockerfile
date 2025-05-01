FROM python:3.11

WORKDIR /src

COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN pip install fasttext
RUN wget https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz

COPY StreamTextSegmenter.py ./

CMD python -u StreamTextSegmenter.py --queue-server rabbitmq --redis-server redis
