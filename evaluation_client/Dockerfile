From python:3.10

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg vim

RUN git clone --recursive https://git.scc.kit.edu/isl/lt-middleware/lt-evaluation.git
RUN python3 -m pip install --no-cache-dir -r lt-evaluation/requirements.txt
RUN python3 -m pip install --no-cache-dir -r lt-evaluation/audioclient/requirements.txt

CMD cd lt-evaluation && bash run_evaluate_pipeline_different_settings.sh http://host.docker.internal:443 $experiment_name

