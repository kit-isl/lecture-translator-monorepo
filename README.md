# LTpipeline

## Quickstart example

See [example](./examples/whisper+nllb/)

## Download this repo

```
git clone --recursive https://gitlab.kit.edu/kit/isl-ai4lt/lt-middleware/ltpipeline
```

### Check out latest branches
```
git submodule update --remote
```

## Configuration
First set the necessary environment variables, either by running
```
export DOMAIN=...
...
```
in Bash, or alternatively by putting them in a `.env` file in this directory (as described [here](https://docs.docker.com/compose/environment-variables/set-environment-variables/#substitute-with-an-env-file)).
The only required environment variable is `DOMAIN`.
The following environment variables are configurable
* `DOMAIN`: the external pipeline domain
* `DEX_FILES`: location for the dex files (default is /var/local/traefik)
* `FRONTEND_THEME`: either default or kittheme.
* `HTTPS_PORT`, `HTTP_PORT`, `MEDIATOR_PORT`, `REDIS_PORT`: use if you want to use non-default network ports. This is useful if you want to run multiple instances of the pipeline on the same host.
* `DOMAIN_PORT`: Only required when using non-default HTTPS port. Set to `<DOMAIN>:<HTTPS_PORT>`.  
  (Note: you might need to use the `HTTP_PORT` instead of the `HTTPS_PORT` when running locally)
* `PIPELINE_NAME`: set this to some arbitrary value when you want to run multiple instances of the pipeline on the same host.  
  You will also have to set the matching value in [the Traefik config](./traefik/traefik.yaml) under `providers.docker.constraints`, where the default value is set to `main`.

## Run with docker
You will first need to set the configuration variables as described [above](#configuration).
To then start all services run
```
docker-compose up
```

To start in development mode run
```
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml up
```

Running the pipeline locally may cause some issues with TLS. You may run without TLS with
```
docker-compose -f docker-compose.yaml -f docker-compose.notls.yaml up
```

Running the pipeline user authentication disabled might also be useful in a local test setup. You may disable authentication with
```
docker-compose -f docker-compose.yaml -f docker-compose.noauth.yaml up
```
