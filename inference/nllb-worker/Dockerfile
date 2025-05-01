From python:3.10

ARG PLATFORM=cu118

WORKDIR /app
COPY requirements.txt requirements.txt
RUN python3 -m pip install --no-cache-dir -r ./requirements.txt --extra-index-url https://download.pytorch.org/whl/${PLATFORM}

COPY src src

ENV TRANSFORMERS_CACHE="/cache/transformers"

ENTRYPOINT ./src/nllb-service.py
