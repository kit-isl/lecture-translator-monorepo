
PIPELINE=${1:-ltpipeline}
DOCKER_CMD=${2:-docker compose}
ASR=20
MT=20
SEG=5
TTS=5

echo Restarting pipeline $PIPELINE with docker command $DOCKER_CMD

cd /home/mtasr/LT2.0/$PIPELINE/examples/KIT.2024.02.Dialog

ROOT_DIR="../../"

echo Stopping pipeline
$DOCKER_CMD --env-file $ROOT_DIR/.env -f $ROOT_DIR/docker-compose.yaml -f $ROOT_DIR/docker-compose.markup.yaml -f $ROOT_DIR/docker-compose.dialog.yaml down || true
echo Pipeline stopped

echo Starting pipeline
$DOCKER_CMD --env-file $ROOT_DIR/.env -f $ROOT_DIR/docker-compose.yaml -f $ROOT_DIR/docker-compose.markup.yaml -f $ROOT_DIR/docker-compose.dialog.yaml up -d --scale streamingasr=$ASR --scale streamingmt=$MT --scale streamingtextstructurer=$SEG --scale streamingtts=$TTS
echo Pipeline started

