FROM python:3

WORKDIR /src

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg

RUN apt-get -y install build-essential libpoppler-cpp-dev

COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY dex dex
RUN python -m grpc_tools.protoc -Idex/api/v2 --python_out=. --grpc_python_out=. dex/api/v2/api.proto

COPY ltweb/run_init.py run_init.py
RUN python run_init.py

RUN apt-get install -y nodejs npm
RUN apt-get update \
    && apt-get install -y wget gnupg \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable fonts-ipafont-gothic fonts-wqy-zenhei fonts-thai-tlwg fonts-kacst fonts-freefont-ttf libxss1 \
      --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g puppeteer

ENV NODE_PATH=/usr/local/lib/node_modules

#RUN curl -sL https://deb.nodesource.com/setup_16.x | bash -
#RUN apt-get install -y nodejs

COPY ltweb/ ./ltweb/

HEALTHCHECK --interval=1m --timeout=1m --retries=3 --start-period=5m \
    CMD ["curl", "-f", "http://localhost:5000"]

CMD ["gunicorn", "ltweb:create_app()", "--worker-class", "gevent", "--bind", "0.0.0.0:5000"]
