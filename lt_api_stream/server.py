
import asyncio
import json
import jsons
import redis.asyncio as redis
from fastapi import FastAPI, Response, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

import uvicorn
import base64

from qbmediator.Session import Session

from db import pool

app = FastAPI()
templates = Jinja2Templates(directory="templates")

async def get_from_db(db, name, session, tag, key):
    context = name+"/"+session+"/"+tag+"/"
    return await db.get(context+key)

async def get_db():
    return await redis.Redis(connection_pool=pool)

async def getgraph(db,session):
    s = await db.get("Session"+session)
    try:
        s = jsons.load(json.loads(s),Session)
        graph = s.get_graph()
    except:
        graph = {}
    return graph

@app.get('/ltapi/stream/data')
async def sse(response: Response, name: str = Query(...)):
    db = await get_db()
    data = await db.get(name)
    await db.aclose()
    if not data:
        return None
    return data

api = "webapi"

@app.get('/ltapi/stream')
async def sse(response: Response, channel: str = Query(...)):
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Cache-Control'] = 'no-cache'
    response.headers["Connection"] = 'keep-alive'

    db = await get_db()

    async def event_stream():
        pubsub = db.pubsub()
        try:
            await pubsub.subscribe(channel)

            graph = await getgraph(db,channel)
            num_components = len(graph)

            num_END = 1
            while True:
                message = await pubsub.get_message()

                if message is not None and message['type'] == 'message':
                    json_data = json.loads(message['data'])['data']
                    data = json.loads(json_data)
                    if "send_link" in data and data["send_link"]:
                        for key, value in data.items():
                            if type(value) is str and value.startswith("Data/"):
                                if "length" in data and "b64_enc_video" in data:
                                    session_id = data["session"]+"_"+(data["sender"].replace(":","_"))
                                    #print(f"{session_id = }, {value = }, {data['length'] = }")
                                    await db.hset(f"videostreaming_{session_id}", value, data["length"])
                                link = "/ltapi/stream/data?name="+value
                                data[key] = link
                        del data["send_link"]
                    elif "linkedData" in data and data["linkedData"]:
                        for key, value in data.items():
                            if type(value) is str and value.startswith("Data/"):
                                try:
                                    value_unlinked = await db.get(value)
                                except:
                                    print("ERROR in retreiving data from database")
                                    continue
                                data[key] = value_unlinked
                                data["linkedData"] = False

                    num_subscribers = await db.get("Subscribers_"+str(channel))
                    data["num_subscribers"] = num_subscribers

                    json_data = json.dumps(data)

                    yield f"data: {json_data}\n\n"

                    if "controll" in data and data["controll"] == "END":
                        num_END += 1
                        if num_END >= num_components:
                            await pubsub.unsubscribe(channel)
                            await pubsub.aclose()
                            print("Ended: Closing connection to stream "+channel,flush=True)
                            break
        except asyncio.CancelledError:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            print("Cancelled: Closing connection to stream "+channel,flush=True)

    async def event_stream_timeout(timeout):
        await db.incr("Subscribers_"+str(channel))

        try:
            iterator = event_stream()
            while True:
                message = await asyncio.wait_for(anext(iterator), timeout)
                yield message
        except asyncio.CancelledError:
            print("Client left: Ending connection",flush=True)
        except asyncio.TimeoutError:
            print("Timeout: Ending connection",flush=True)
        except StopAsyncIteration:
            print("StopAsyncIteration: Ending connection",flush=True)

        await db.decr("Subscribers_"+str(channel))
        await db.aclose()
        print("Redis connection closed",flush=True)

    version = await get_from_db(db, "asr", channel, "0", "version")
    print("VERSION",version,flush=True)
    timeout = 1*60
    if version=="offline":
        print("Setting timeout for offline session.")
        timeout = 60*60

    return StreamingResponse(event_stream_timeout(timeout), media_type="text/event-stream")

@app.get("/ltapi/stream/video/session_{session_id}.html")
async def video_html_file(request: Request, session_id:str):
    return templates.TemplateResponse('video2.html', {"request": request, "m3u8":"/"+api+"/stream/video/m3u8_"+session_id+".m3u8"})

async def get_segments(db, session_id):
    segments = await db.hgetall(f"videostreaming_{session_id}")
    if segments is None:
        return f"ERROR: {session_id = } not found!"
    segments = list(segments.items())
    return segments

@app.get("/ltapi/stream/video/m3u8_{session_id}.m3u8")
async def video_m3u8_file(request: Request, session_id:str):
    db = await get_db()
    segments = await get_segments(db, session_id)
    segments = [(i,s[1]) for i,s in enumerate(segments)]
    await db.aclose()
    return templates.TemplateResponse('video_session.m3u8', {"request": request, "session_id":session_id, "segments":segments, "api":api})

@app.get("/ltapi/stream/video/data_{session_id}_{number}.ts")
async def video_data_file(session_id:str, number:int):
    db = await get_db()
    segments = await get_segments(db, session_id)
    name = segments[number-1][0]
    data = await db.get(name)
    await db.aclose()
    if data is not None:
        file_bytes = base64.b64decode(data)
    else:
        print("WARNING: video with name",name,"is None")
        file_bytes = b''
    return Response(content=file_bytes, media_type="application/pdf")

if __name__ == "__main__":
    print("Running the LT-API-Streaming Server...")

    uvicorn.run(app, host="0.0.0.0", port=5000)
