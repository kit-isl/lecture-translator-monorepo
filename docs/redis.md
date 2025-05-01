# Database
Multiple components use Redis as a kay-value store for various purposes.
Most importantly [sessions](#sessions) and [their properties](#session-properties) are stored in the database and most pipeline components maintain a Redis connection to query session properties and such.

### Python Database Interface
Abstractions to interact with the database are defined in the [qbmediator library](../../../lt-middleware/qbmediator/-/blob/main/qbmediator/Database.py).
For a usage example you may take a look at [the LT-API](../../../lt-middleware/lt_api/-/blob/main/server.py).

### Using the Redis CLI
The Redis connections are usually maintained in Python, but for debugging purposes the database values may also be queried using the [Redis CLI](https://redis.io/docs/connect/cli/), e.g. through
```
docker compose exec redis redis-cli <COMMAND> <ARGS>
```
To list all keys stored in the database you may execute
```
docker compose exec redis redis-cli --scan
```

## Sessions
Sessions are created by the LT-API upon user request (HTTP routes such as `/ltapi/startsession`, `/ltapi/get_default_asr`, ...).
For each session a serialized [qbmediator session object](../../../lt-middleware/qbmediator/-/blob/main/qbmediator/Session.py?ref_type=heads#L3) is stored under the `Session{session-id}` key.

Example of using the Redis CLI to show a session entry:
```
$ docker compose exec redis redis-cli GET Session266865
"{\"graph\": {\"user:0\": [\"asr:0\", \"log\"], \"asr:0\": [\"textseg:asr0\", \"mt:0\", \"mt:1\", \"log\"], \"textseg:asr0\": [\"api\", \"log\"], \"mt:0\": [\"textseg:mt0\", \"log\"], \"textseg:mt0\": [\"api\", \"log\"], \"mt:1\": [\"textseg:mt1\", \"log\"], \"textseg:mt1\": [\"api\", \"log\"]}, \"id\": 266865, \"name\": \"Audio\", \"startDate\": \"2024/02/02/\", \"streams\": [{}], \"type\": \"lecture\"}"
```

You may also query session attributes stored in the database through the LT-API.
Example of listing a session graph using the LT-API:
```
$ curl -XPOST https://lt2srv.iar.kit.edu/webapi/266865/getgraph
{"user:0": ["asr:0", "log"], "asr:0": ["textseg:asr0", "mt:0", "mt:1", "log"], "textseg:asr0": ["api", "log"], "mt:0": ["textseg:mt0", "log"], "textseg:mt0": ["api", "log"], "mt:1": ["textseg:mt1", "log"], "textseg:mt1": ["api", "log"]}
```

## Session Properties
TODO: describe what are properties  
TODO: describe how are properties stored (where? key/value format)  
TODO: how to set/get properties  
TODO: list of example properties (ASR languages, MT languages, stability modes, ...)  

# List of Database Keys
The following documents a list of various database entries stored by the pipeline in various places.
Note that any component may choose to store any additional data in the database, to the following list is non-comprehensive.

| Key         | Description                          |
|-------------|--------------------------------------|
| Session{ID} | Serialized qbmediator session object |
| ...         | ...                                  |

For a more comprehensive list of database keys see [here](./extra/redis-keys.txt)
