
export experiment_name=${1:-cascaded+offline} # Change for other experiment

export DEX_FILES=.
ROOT_DIR="../../"
YAML_FILES="-f $ROOT_DIR/docker-compose.yaml -f $ROOT_DIR/docker-compose.noauth.yaml -f $ROOT_DIR/docker-compose.notls.yaml -f $ROOT_DIR/inference/docker-compose.yaml -f docker-compose.override.yaml -f ../../evaluation_client/docker-compose.yaml"

docker-compose $YAML_FILES build evaluation_client

docker-compose $YAML_FILES up -d evaluation_client

docker-compose $YAML_FILES logs -f evaluation_client

