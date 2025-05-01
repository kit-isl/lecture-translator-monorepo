
import redis.asyncio as redis

import os
import time

if "REDIS_SERVER" in os.environ:
    redis_server = os.environ["REDIS_SERVER"]
else:
    redis_server="localhost"
if "REDIS_PORT" in os.environ:
    redis_port = os.environ["REDIS_PORT"]
else:
    redis_port="6379"

cR = False
while not cR:
    try:
        pool = redis.ConnectionPool(
            host=redis_server,
            port=redis_port,
            db=0,
            decode_responses=True)
        #db.ping()
        cR = True
    except:
        print("Wait for redis")
        time.sleep(5)

print("Connected to redis")

