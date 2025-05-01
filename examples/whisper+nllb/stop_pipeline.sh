
export DEX_FILES=.
ROOT_DIR="../../"

docker-compose -f $ROOT_DIR/docker-compose.yaml -f $ROOT_DIR/docker-compose.noauth.yaml -f $ROOT_DIR/docker-compose.notls.yaml -f $ROOT_DIR/inference/docker-compose.yaml -f docker-compose.override.yaml -f ../../evaluation_client/docker-compose.yaml down

