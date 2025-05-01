#!/usr/bin/env python3

import argparse
import json
import jsons

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
import os
import requests

from qbmediator.Text import get_punctuation

punctuation = get_punctuation()

property_to_default = {
    "display_language": "en",
    "out_lang": "{}",
    "finished_sentences": "-1",
#    "method": "streaming_simple",
    "method": "online_model",
    "StablePrefix": "{}",
    "senderID": "{}",
    "waiting_messages": "{}",
    "languages": "{}",
    "last_chapter_time": "0",
    "leadingStream": "-1",
    "model_parameters": {
        "summarizer": {
            "endpoint": "http://192.168.0.68:4000/summarize",
        },
    },
    "mt_server_default": "http://i13hpc64.cluster:5051/predictions/"
}

class PropertyProvider():
    def __getitem__(self, key):
        property = db.getPropertyValues(key)
        try:
            return jsons.loads(property)
        except json.JSONDecodeError:
            return property
    
    def __setitem__(self, key, value):
        if isinstance(value, (list, dict)):
            value = jsons.dumps(value)
        db.setPropertyValue(key, value)

    def append(self, key, value):
        """Appends a value to a list stored in the database. If the key does not exist, it will be created."""
        properties = db.getPropertyValues(key)
        if properties is None:
            properties = []
        else:
            properties = json.loads(properties)
        properties.append(value)
        db.setPropertyValue(key, jsons.dumps(properties))
        return properties
    
    def set_key(self, key, dict_key, value):
        """Sets the key of a dict stored in the database. If the key does not exist, it will be created."""
        properties = db.getPropertyValues(key)
        if properties is None:
            properties = {}
        else:
            properties = json.loads(properties)
        properties[dict_key] = value
        db.setPropertyValue(key, jsons.dumps(properties))
        return properties
    
pp = PropertyProvider()

class Summarizer():
    def __init__(self,args):
        self.args=args
        self.mt_server_index = 0

    def get_sender_info(self, data):
        if "sender" in data:
            sender_component, sender_id = data["sender"].split(":")[:2]
            return f"{sender_component}:{sender_id}", sender_component, str(sender_id)
        return None, None
    
    def get_display_language(self, sender_component, session, sender_id):
        return db.getPropertyValues("display_language", context=[sender_component, session, str(sender_id)])

    def processAggregate(self, messages):
        for message in messages:
            data = json.loads(message)

            session = data["session"]
            stream = data["tag"]
            db.setContext(name, session, stream)

            sender_info, sender_component, sender_id = self.get_sender_info(data)
            lead = pp["leadingStream"]

            if "controll" in data:
                self.handle_control_message(data, session, stream, sender_component, sender_id)
            else:
                if "unstable" not in data or not data["unstable"]:
                    if data["sender"].startswith("textstructurer"):
                        l = self.get_display_language(sender_component, session, sender_id)
                        if "markup" in data and data["markup"] == "chapterBreak" and l == "en":
                            time = data["start"]
                            self.add_summary(float(time))

                            prefix = pp["StablePrefix"]
                            waiting_messages = pp["waiting_messages"]

                            self.send_waiting_messages(session, stream, prefix, waiting_messages)
                    elif lead == sender_info:
                        #print(sender_id, data["seq"])
                        prefix = self.append_stable(data)
                        print("---")
                        print("Prefix", prefix)

    def handle_control_message(self, data, session, stream, sender_component, sender_id):
        id = f"{sender_component}:{sender_id}"
        is_from_textstructurer = data["sender"].startswith("textstructurer")  
        l = self.get_display_language(sender_component, session, sender_id)

        if is_from_textstructurer:
            data["sender"] = f"{name}:{stream}_{l}"

            match data["controll"]:
                case "START":
                    con.publish("mediator", session, jsons.dumps(data))

                case "END":
                    if(l == "en"):
                        self.finish(session,stream)
                    con.publish("mediator", session, jsons.dumps(data))

                case "INFORMATION":
                    properties = db.getPropertyValues()
                    sender = f"{name}:{stream}_{l}"
                    data = {'session': session, 'controll': "INFORMATION", 'sender': sender, sender: properties}
                    data["sender"] = sender
                    con.publish("mediator", session, jsons.dumps(data))

                case "CLEAR":
                    con.publish("mediator", session, jsons.dumps(data))

        elif data["controll"] == "START":
            pp.set_key("languages", id, l)
            pp.set_key("StablePrefix", id, [])
            pp.set_key("waiting_messages", id, [])
            pp.set_key("out_lang", id, l)

            if l == "en":
                pp["leadingStream"] = f"{sender_component}:{sender_id}"

    def send_waiting_messages(self, session, stream, prefix, waiting_messages):
        out_lang = pp["out_lang"]
        for sender in prefix:
            sender_waiting_messages = waiting_messages[sender]
            while len(sender_waiting_messages) > 0:
                data = {}
                data["session"] = session
                data["sender"] = f"{name}:{stream}_{out_lang[sender]}"
                data["start"] = sender_waiting_messages[0][2]
                data["end"] = sender_waiting_messages[0][3]
                data["seq"] = sender_waiting_messages[0][0]
                data["markup"] = sender_waiting_messages[0][1]
                data["unstable"] = False
                con.publish("mediator", session, jsons.dumps(data))
                print("Send message:", data)
                sender_waiting_messages = sender_waiting_messages[1:]
            waiting_messages[sender] = sender_waiting_messages
        pp["waiting_messages"] = waiting_messages

    def finish(self, session, stream):
        prefix = pp["StablePrefix"]
        lead = pp["leadingStream"]

        if lead not in prefix or len(prefix[lead]) == 0 or len(prefix[lead][-1]) == 0:
            pass # No text in last chapter
        else:
            time = float(prefix[lead][-1]["end"])
            text, summary_time = self.extract_chapter_text(time)
            self.summarize(text, summary_time)

        waiting_messages = pp["waiting_messages"]
        self.send_waiting_messages(session, stream, prefix, waiting_messages)

    def add_summary(self, time):
        text, summary_time = self.extract_chapter_text(time)
        pp['last_chapter_time'] = time
        print("Text:", text)
        self.summarize(text, summary_time)

    def extract_chapter_text(self, time):
        prefix = pp["StablePrefix"]
        lead = pp["leadingStream"]
        finished_sentences = int(pp["finished_sentences"])
        i = finished_sentences + 1
        text = ""

        last_chapter_time = float(pp['last_chapter_time'])

        for i in range(len(prefix[lead])):
            if float(prefix[lead][i]["start"]) > time:
                break
            elif float(prefix[lead][i]["start"]) < last_chapter_time:
                continue
            else:
                text += " " + prefix[lead][i]["seq"]

        new_time_id = finished_sentences if finished_sentences != -1 else 0
        new_time = (float(prefix[lead][i-1]["start"]) + float(prefix[lead][new_time_id]["start"]))/2

        pp["finished_sentences"] = i - 1
        return text, new_time
            
    def summarize(self, text, time):
        if len(text.strip()) == 0:
            return
        
        j = 0
        languages = pp["languages"]
        waiting_messages = pp["waiting_messages"]
        prefix = pp["StablePrefix"]
        generated_text = self.generate_summary(text)

        for k in range(len(generated_text)):
            if len(generated_text[k].strip()) > 0:
                for sender in prefix:
                    summary = generated_text[k]
                    language = languages[sender]
                    if language != "en" and len(language) == 2:
                        data = {"text":summary,"priority":1}
                        mt_server = self.get_mt_server(language)
                        
                        rep = requests.post(mt_server, data=data)

                        if "hypo" in rep.json():
                            summary = rep.json()["hypo"]
                    msg = (summary, "summary", str(time+0.01*j), str(time+0.01*(j+1)))
                    waiting_messages[sender].append(msg)
        pp["waiting_messages"] = waiting_messages

    def generate_summary(self, text):
        payload = { 'text': text }
        param = db.getPropertyValues("model_parameters")["summarizer"]
        headers = { 'Content-Type': 'application/json' }
        response = requests.post(param["endpoint"], data=json.dumps(payload), headers=headers).json()
        summary = response['summary']
        return [s.removeprefix("• ") for s in summary.split("\n")]
    
    def append_stable(self, data):
        id, _, _ = self.get_sender_info(data)
        if id is None or "seq" not in data:
            return
        prefix = pp["StablePrefix"]
        lid = data["lid"] if "lid" in data and data["lid"] is not None else 0
        if len(prefix[id]) == 0:
            prefix[id].append({"seq": data["seq"], "start": data["start"], "end": data["end"], "lid": lid})
        else:
            if len(prefix[id][-1]["seq"].strip()) > 0 and prefix[id][-1]["seq"].strip()[-1] in punctuation:
                prev = prefix[id][-1]
                if prev["seq"] == data["seq"] and prev["start"] == data["start"] and prev["end"] == data["end"]:
                    return prefix
                prefix[id].append({"seq": data["seq"], "start": data["start"], "end": data["end"], "lid": lid})
            else:
                prefix[id][-1] = {"seq": prefix[id][-1]["seq"] + " " + data["seq"], "start": prefix[id][-1]["start"], "end": data["end"], "lid": lid}
                
        pp["StablePrefix"] = prefix
        return prefix

    def get_mt_server(self, language, version=False):
        try:
            mt_server = pp["mt_server_" + language]
        except Exception as e:
            print(f"ERROR: {e}")
            mt_server = None
        if mt_server is None:
            print("WARNING: Unknown language, using default mt_server")
            mt_server = pp["mt_server_default"]

        mt_server = mt_server.split("|")
        self.mt_server_index %= len(mt_server) # Necessary if mt_server changes during session

        s = mt_server[self.mt_server_index]
        self.mt_server_index += 1
        self.mt_server_index %= len(mt_server)

        if version:
            url = s.replace("predictions", "models")
            return url

        return s

        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    parser.add_argument('--property_provider_url', type=str, default='http://192.168.0.72:5000')

    args = parser.parse_args()

    name = "summarizer"

    property_provider_url = args.property_provider_url

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    d = {"languages": {}}
    con = get_best_connector()(args.queue_server,args.queue_port,db)
    con.register(db, name, d)

    queue = os.getenv('QUEUE_SYSTEM')
    if queue == "KAFKA" or queue == "KAFKA-P":
        summarizer = Summarizer(args)
        con.consume(name,summarizer,True)
