FROM python:3.11

WORKDIR /src


RUN apt-get update && apt-get install -y ffmpeg
RUN pip install numpy
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN pip install torch==2.1.1+cpu torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install onnxruntime

RUN python -c 'import torch; torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", force_reload=False, onnx=True)'

COPY StreamASR.py ./
COPY segmenter.py ./
COPY config.json ./

CMD python -u StreamASR.py --queue-server rabbitmq --redis-server redis
