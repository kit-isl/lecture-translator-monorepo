#!/usr/bin/env python3
import argparse
import jsons
import json


from Session import Session
from Connection import get_best_connector, KafkaConnector
from Database import get_best_database

import redis

import time

def load_session(session):
    s = db.db.get("Session" + session)
    if s is None:
        return
    return jsons.load(json.loads(s.decode("utf-8")), Session)

class MediatorRequest:
    def process(self, body):
        data = json.loads(body)
        if "b64_enc_pcm_s16le" not in data and "b64_enc_audio" not in data and "json_audio_float" not in data and "b64_enc_video" not in data:
            print(" [x] Received %r " % (body))
        else:
            print(" [x] Received audio")

        session = data["session"]
        s = load_session(session)
        if s is None:
            print("WARNING: Session not found, ignoring")
            return

        sender = data["sender"]
        receivers = s.get_receivers(sender)

        id=db.db.llen("MessageList"+session+":"+sender)
        data["message_id"] = id
        db.db.rpush("MessageList"+session+":"+sender, json.dumps(data))

        if("controll" in data):
            if data["controll"] == "START":
                if("name" in data):
                    print("Set name")
                    p = db.db.pipeline()
                    p.watch("Session" + session)
                    p.multi();
                    s = load_session(session)
                    s.name = data["name"]
                    # print("Trying to add steam", id, " to session ", session)
                    p.set("Session" + session, json.dumps((jsons.dump(s))).encode("utf-8"));
                    p.execute();
                if("host" in data):
                    print("Set host")
                    p = db.db.pipeline()
                    p.watch("Session" + session)
                    p.multi();
                    s = load_session(session)
                    s.host = data["host"]
                    # print("Trying to add steam", id, " to session ", session)
                    p.set("Session" + session, json.dumps((jsons.dump(s))).encode("utf-8"));
                    p.execute();
                if("type" in data):
                    print("Set type")
                    p = db.db.pipeline()
                    p.watch("Session" + session)
                    p.multi();
                    s = load_session(session)
                    s.type = data["type"]
                    # print("Trying to add steam", id, " to session ", session)
                    p.set("Session" + session, json.dumps((jsons.dump(s))).encode("utf-8"));
                    p.execute();

                    #Add List to
                    p = db.db.pipeline()
                    p.watch("List" + data["type"])
                    p.multi();
                    if(db.db.exists("List" + data["type"])):
                        sessionList = set(json.loads(db.db.get("List" + data["type"]).decode("utf-8")))
                    else:
                        sessionList = set()
                    sessionList.add(s.id)
                    # print("Trying to add steam", id, " to session ", session)
                    p.set("List" + data["type"], json.dumps(list(sessionList)).encode("utf-8"));
                    p.execute();
                    print("Sessions of type "+s.type + ": "+db.db.get("List" + s.type).decode("utf-8"))
                if ("password" in data):
                    print("Set password")
                    p = db.db.pipeline()
                    p.watch("Session" + session)
                    p.multi();
                    s = load_session(session)
                    s.password = data["password"]
                    # print("Trying to add steam", id, " to session ", session)
                    p.set("Session" + session, json.dumps((jsons.dump(s))).encode("utf-8"));
                    p.execute();

            elif(data["controll"] == "END"):
                if(s.type != ""):
                    print("Type:",s.type)
                    p = db.db.pipeline()
                    p.watch("List" + s.type)
                    p.multi();
                    sessionList = set(json.loads(db.db.get("List" + s.type).decode("utf-8")))
                    if(s.id in sessionList):
                        sessionList.remove(s.id)
                    p.set("List" + s.type, json.dumps(list(sessionList)).encode("utf-8"));
                    p.execute();
                db.db.delete("MessageList"+session+":"+sender)

                db.db.hincrby('active_session_components', session, -1)
                active_components = db.db.hget('active_session_components', session)
                if int(active_components) == 0:
                    db.db.hdel('active_sessions', session)

        for receiver in receivers:
            # when you have a component (e.g. asr) which sends to multiple components (e.g. mt and voice conversion) but you want to send a message (e.g. audio of a segment) to only one of those components (e.g. voice conversion ; maybe because of resources)
            # you have to set send_to e.g. in the asr component
            if "send_to" in data and data["send_to"]!=receiver:
                continue

            receiver = receiver.split(":")
            if len(receiver) == 2:
                receiver, tag = receiver
                data["tag"] = tag
            elif len(receiver) == 1:
                receiver = receiver[0]
                tag = "All"
                if "tag" in data:
                    del data["tag"]
            else:
                print("ERROR in mediator process")
                continue

            body_send = json.dumps(data)
            if(receiver == "api"):
                print("Send to api: ",body_send)
                db.db.publish(session,json.dumps({"data": body_send}))
            else:
                if len(body_send) < 1000:
                    print("Send to ",receiver," text: ",body_send)

                version = db.getPropertyValues("version", context=[receiver,session,tag])
                con.publish(receiver, session+"/"+receiver+"/"+tag, body_send)

        if not "api" in receivers and "controll" in data and "sender" in data and not data["sender"].startswith("user"): # Send all controll messages to api
            if "tag" in data:
                del data["tag"]
            body_send = json.dumps(data)
            print("Send to api: ",body_send)
            db.db.publish(session,json.dumps({"data": body_send}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    args = parser.parse_args()
    print("Initialize the mediator...")
    print("Connection to ",args.queue_server,":",args.queue_port)

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties({"version":"online"})

    con = KafkaConnector(args.queue_server,args.queue_port,db)

    mediatorRequest = MediatorRequest()
    con.consume("mediator", mediatorRequest, blocking=False)

