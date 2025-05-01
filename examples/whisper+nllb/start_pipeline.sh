
#export PIPELINE_NAME=main3 # Change if you want to run multiple pipelines on one server (see Readme of main repo page)
export DOMAIN=localhost # Change if your server has a domain
export DEX_FILES=.
export FRONTEND_THEME=default

export CUDA_ASR=0 # On which GPU to run the ASR worker
export CUDA_MT=0 # On which GPU to run the MT worker

ROOT_DIR="../../"

touch $ROOT_DIR/dex.env $ROOT_DIR/dex.db $ROOT_DIR/traefik/auth/basicauth.txt

docker-compose -f $ROOT_DIR/docker-compose.yaml -f $ROOT_DIR/docker-compose.noauth.yaml -f $ROOT_DIR/docker-compose.notls.yaml -f $ROOT_DIR/inference/docker-compose.yaml -f docker-compose.override.yaml up -d

# Access website/mlflow from laptop if your server has no public domain to reach it
# ssh -N -L 8080:$server:443 $jump_server
# Then go to http://localhost:8080 or http://localhost:8080/mlflow

