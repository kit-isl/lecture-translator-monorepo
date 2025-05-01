
# 1) docker compose exec streamingasr bash
# 2) python clean.py (this file)

import redis

r = redis.Redis("redis")

ignore = [b'sessionIDs',b'groups',b'LTArchive',b'facedubbing_videos',b'component_names',b'component_info']

cursor = '0'
while cursor != 0:
    cursor, partial_keys = r.scan(cursor=cursor)

    for key in partial_keys:
        if any(i in key for i in ignore):
            print("Keeping",key)
        else:
            r.delete(key)

