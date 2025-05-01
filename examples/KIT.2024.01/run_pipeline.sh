#!/bin/bash


ROOT_DIR="../../"


docker compose --env-file $ROOT_DIR/.env -f $ROOT_DIR/docker-compose.yaml -f $ROOT_DIR/docker-compose.markup.yaml "$@"


