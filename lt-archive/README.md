# Question - Answering using FLAN


## Run directly for debugging
 *  export LT_ARCHIVE_DIR=
 *  export FLASK_APP=ltarchive
 *  export FLASK_ENV=development
 *  flask run



## Run in docker

 *  docker build -t ltfrontend -f Dockerfile .  
 *  docker run -p 8080:5000 --env API="http://lt2srv.iar.kit.edu" -ti ltfrontend