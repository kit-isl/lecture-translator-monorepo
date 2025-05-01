# LTFrontend


## Run directly for debugging

 *  export API="http://lt2srv.iar.kit.edu"
 *  export ARCHIVE="http://lt2srv.iar.kit.edu"
 *  export THEME=default
 *  export FLASK_APP=ltweb 
 *  export FLASK_ENV=development
 *  flask run


## Run in docker

 *  docker build -t ltfrontend -f Dockerfile .  
 *  docker run -p 8080:5000 --env API="http://lt2srv.iar.kit.edu" -ti ltfrontend

## Restart gunicorn in-place
The following can be used to restart gunicorn "in-place", i.e. make it restart without ending the process.

```
kill -HUP <gunicorn PID>
```

In the docker compose workflow you can use this to restart gunicorn (e.g. to serve new content after updating the code or HTML) without restarting the container:

```
docker compose exec frontend sh -c 'kill -HUP $(pidof python)'
```
