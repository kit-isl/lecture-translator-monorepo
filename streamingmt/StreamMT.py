#!/usr/bin/env python3
import argparse
import jsons
import json


import threading
import requests


import sacremoses

import redis
import time

import os
import sys

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database, get_from_db, set_in_db, delete_in_db, get_used_property

from qbmediator.Text import get_punctuation

punctuation = get_punctuation()

property_to_default = {
    "mode": "SendStable", # Options: SendStable, SendUnstable, SendPartial,SendPartialAndUnstable
    "version": "online", # Options: online, offline
    "mt_server_default": "http://i13hpc51.ira.uka.de:8080/mt/en-de/infer",
    "language": "en-de",
    "display_language": "de",
    "minPrefixExtension": "2",
    "split_on_speechsegmentends": "False",
    "LastStablePrefix": ""
    }

class Segmenter():
    def __init__(self,args,translator=None):
        self.args = args
        self.translator = translator

    def processAggregate(self,mList):
        do = False
        data_last = None
        for message in mList:
            print(" [x] Received for segmentation %r " % (message))
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                print("WARNING: received invalid JSON", file=sys.stderr)
                continue

            if not self.validate_message(data):
                continue

            session = data["session"]
            stream = data["tag"]
            db.setContext(name,session,stream)

            if "lid" in data:
                asr_pass_through = db.getPropertyValues("asr_pass_through")
                out_lang = db.getPropertyValues("language").split("-")[1]

                if asr_pass_through and out_lang == data["lid"]:
                    print("Pass through")
                    data["sender"] = name + ":" + stream
                    data["asr_pass_through"] = True
                    con.publish("mediator", session, jsons.dumps(data))
                    continue

                del data["lid"] # Bug fix for now

            mode = db.getPropertyValues("mode")
            if "unstable" in data and data["unstable"] == True and mode.startswith("SendPartial"):
                continue

            data_last = data

            if ("controll" in data):
                if (data["controll"] == "START"):
                    db.setPropertyValue("StablePrefix", "")

                    self.translator.get_mt_server(version=True)
                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))  # Send START to further components
                elif (data["controll"] == "END"):
                    self.finishSegment(session,stream);

                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))
                elif (data["controll"] == "INFORMATION"):
                    # Do MT server version request
                    url = self.translator.get_mt_server(version=True)

                    properties = db.getPropertyValues();

                    try:
                        res = requests.get(url)
                        if res.status_code != 200:
                            raise requests.exceptions.ConnectionError
                    except requests.exceptions.ConnectionError:
                        print("WARNING: Could not request mt_server version")
                        version = "Request failed"
                    else:
                        version = res.text

                    properties["mt_server_version"] = version

                    data = {'session': session, 'controll': "INFORMATION", 'sender': name + ":" + stream,
                            name + ":" + stream: properties}

                    con.publish("mediator", session, jsons.dumps(data))
                elif data["controll"] == "CLEAR":
                    self.finishSegment(session,stream);

                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))
                elif data["controll"] == "PAUSE":
                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))
                else:
                    print("Unkonw controll message", body);
            else:
                endTime = data["end"]

                stableEndTime = db.getPropertyValues("StableEndTime")
                if(stableEndTime and float(data["start"]) > float(stableEndTime) + 1):
                    print("Finish segment",session,stream)
                    self.finishSegment(session,stream);

                if ("unstable" in data and data["unstable"] == True):
                    self.replace_unstable(data)
                else:
                    self.append_stable(data);

                do = True

        if do and data_last:
            if (mode == "SendUnstable" or mode == "SendPartial" or mode == "SendPartialAndUnstable"):
                print("Translate unfinished")
                self.translate_unstable(data_last, endTime);


    def finishSegment(self,session,stream):
        text = db.getPropertyValues("StablePrefix");
        if text != None and text.strip() != "":
            lid = db.getPropertyValues("PrefixLID")
            translation = self.translator.translate(text.strip(), session, stream, unstable=False, lid=lid)
            data2 = {'seq': translation};
            data2["start"] = db.getPropertyValues("StableStartTime")
            data2["end"] = db.getPropertyValues("StableEndTime")
            data2["unstable"] = False;
            data2["session"] = session
            data2["tag"] = stream
            data2["sender"] = name + ":" + stream
            data2["seq"] = data2["seq"]
            con.publish("mediator", session, jsons.dumps(data2))
        db.setPropertyValue("PrefixLID", None)
        db.setPropertyValue("UnstableLID", None)
        db.setPropertyValue("LastStablePrefix","")
        db.setPropertyValue("lastTranslation","")
        db.setPropertyValue("StableTargetPrefix","")
        db.setPropertyValue("StablePrefix","")
        db.setPropertyValue("StableStartTime",None)
        db.setPropertyValue("StableEndTime",None)
        db.setPropertyValue("UnstablePrefix", "")

    def translate_unstable(self,data,endTime):
        session=data["session"]
        stream = data["tag"]

        mode = db.getPropertyValues("mode");
        if(mode == "SendUnstable"):
            data["seq"] = db.getPropertyValues("StablePrefix") + db.getPropertyValues("UnstablePrefix")
        elif(mode == "SendPartial" or mode == "SendPartialAndUnstable"):
            data["seq"] = db.getPropertyValues("StablePrefix")
            #only translate if at least k new source words
            newPrefix = data["seq"].strip().split()
            oldPrefix = db.getPropertyValues("LastStablePrefix").split()
            print("New Prefix:",newPrefix)
            print("Old prefix:",oldPrefix)
            print("Min Prefix Extension:",db.getPropertyValues("minPrefixExtension") );
            if(len(oldPrefix) + int(db.getPropertyValues("minPrefixExtension")) >  len(newPrefix)):
                #ignore this step
                print("Ignore")
                data["seq"] = ""
        print("Translate partial:",data["seq"])
        if(db.getPropertyValues("StableStartTime") != None):
            data["start"] = db.getPropertyValues("StableStartTime")
            print("Change start time to:",data["start"])
        data["end"] = endTime
        data["unstable"] = True;
        # send to translation
        data["sender"] = name + ":" + stream

        prefix_lid = db.getPropertyValues("PrefixLID") or None
        unstable_lid = db.getPropertyValues("UnstableLID") or None
        #lid = prefix_lid if (prefix_lid == unstable_lid or not unstable_lid) else "mixed"
        if prefix_lid == unstable_lid or not unstable_lid:
            lid = prefix_lid
        elif not prefix_lid:
            lid = unstable_lid
        else:
            lid = "mixed"

        if data["seq"]:
            # why set `data["seq"]`??
            data["seq"] = " "+self.translator.translate(data["seq"], session, stream, unstable=True, lid=lid)
        else:
            data["seq"] = ""

        if(mode == "SendPartial" or mode == "SendPartialAndUnstable"):
            target_prefix = self.selectPrefix(data,session,stream)
            print("Selected Target Prefix:",target_prefix,"from:",data["seq"])
            if target_prefix != "":
                data["unstable"] = False;
                precent=1.0*len(target_prefix)/len(data["seq"])
                end_time = float(data["end"])
                seq = data["seq"]
                data["end"] = float(data["start"])+precent*(end_time - float(data["start"]))
                db.setPropertyValue("LastStablePrefix",seq)
                db.setPropertyValue("StableStartTime",data["end"])
                data["seq"] = target_prefix
                print("Send Partial:",data["seq"],data["start"],data["end"])
                con.publish("mediator", session, jsons.dumps(data))

                data["start"] = data["end"]
                data["end"] = end_time
                data["unstable"] = True;
                data["seq"] = seq
            if mode == "SendPartialAndUnstable":
                stored_prefix = db.getPropertyValues("StableTargetPrefix")
                data["seq"] = data["seq"][len(stored_prefix):]
                #print("Send Unstable:",data["seq"],data["start"],data["end"])
                con.publish("mediator",session,jsons.dumps(data))
        else:
            con.publish("mediator", session, jsons.dumps(data))

    def get_stable_prefix(self, last_text, text, old_prefix, max_words_unstable=100):
        prefix = os.path.commonprefix([last_text, text])

        if not ((len(last_text)>len(prefix) and not last_text[len(prefix)].isalpha()) and (len(text)>len(prefix) and not text[len(prefix)].isalpha())):
            while prefix and prefix[-1].isalpha():
                prefix = prefix[:-1]
            prefix = prefix.rstrip()

        if len(prefix) < len(old_prefix):
            prefix = old_prefix

        num_space = len([c for c in text[len(prefix):] if c==" "]) - max_words_unstable
        if num_space > 0:
            print("WARNING: Forcing words to be stable")
            while len(prefix) < len(text):
                c = text[len(prefix)]
                prefix += c
                if c == " ":
                    num_space -= 1
                    if num_space == -1:
                        break
            prefix = prefix.rstrip()

        new_prefix = text[len(old_prefix):len(prefix)]
        #print(f"SEARCH FOR STABLE OUTPUT:\n{last_text     = }\n{text           = }\n{prefix         = }\n{new_prefix     = }")
        return prefix, new_prefix

    def selectPrefix(self,data,session,stream):
        text = data["seq"].rstrip()
        for p in punctuation:
            text = text.rstrip(p)
        last_text = db.getPropertyValues("lastTranslation")
        if not last_text:
            last_text = ""
        old_prefix = db.getPropertyValues("StableTargetPrefix")
        if not old_prefix:
            old_prefix = ""

        prefix, new_prefix = self.get_stable_prefix(last_text, text, old_prefix)

        db.setPropertyValue("StableTargetPrefix", prefix)
        db.setPropertyValue("lastTranslation", data["seq"])
        return new_prefix

    def append_stable(self,data):
        if not "seq" in data:
            print("WARNING: Got corrupted input without seq")
            return
        
        session = data["session"]
        stream = data["tag"]
        text = tokenizer.tokenize(data["seq"])
        origStartTime = data["start"]
        origEndTime = data["end"]
        mode = db.getPropertyValues("mode")

        db.setPropertyValue("UnstableLID", None)
        lid = data["lid"] if "lid" in data else None
        prefix_lid = db.getPropertyValues("PrefixLID")
        if prefix_lid and prefix_lid != lid:
            lid = "mixed"
        
        prefix = db.getPropertyValues("StablePrefix")
        if(prefix == None):
            prefix = ""
        startTime=origStartTime
        if(prefix != ""):
            startTime = db.getPropertyValues("StableStartTime")
        if data["seq"].startswith(" "):
            prefix += " "

        split_on_speechsegmentends = db.getPropertyValues("split_on_speechsegmentends") == "True"
        speechsegmentends = data.get("speech_segment_ends", False)

        additional_prefix = []
        for i in range(len(text)):
            w = text[i]
            additional_prefix.append(w)
            if(w in punctuation or (i==len(text)-1 and (data["seq"][-1] in punctuation or (split_on_speechsegmentends and speechsegmentends)))):
                data["seq"] = prefix+detokenizer.detokenize(additional_prefix)
                data["start"] = startTime
                endTime = float(origStartTime)+(i+1)*(float(origEndTime)-float(origStartTime))/len(text)
                data["end"] = str(endTime)
                data["unstable"] = False;
                #send to translation
                data["seq"] = " "+self.translator.translate(data["seq"], session, stream, unstable=False, lid=lid)
                data["sender"] = name + ":" + stream
                if(mode == "SendPartial" or mode == "SendPartialAndUnstable"):
                    #Remove part that has been send already
                    target_prefix = db.getPropertyValues("StableTargetPrefix")
                    if(target_prefix != None):
                        print("Remove ",target_prefix," from",data["seq"])
                        data["seq"] = data["seq"][len(target_prefix):]
                        print("New message:",data["seq"])

                    print("Reset prefix information")
                    db.setPropertyValue("LastStablePrefix","")
                    db.setPropertyValue("lastTranslation","")
                    db.setPropertyValue("StableTargetPrefix","")

                con.publish("mediator", session, jsons.dumps(data))

                startTime = str(endTime)
                # reset lid once sentence ends in case it was "mixed"
                # TODO don't save lid on empty new sentence?
                lid = data.get("lid", None)
                prefix = ""
                additional_prefix = []

        db.setPropertyValue("StablePrefix",prefix+detokenizer.detokenize(additional_prefix))
        db.setPropertyValue("StableStartTime",str(startTime))
        db.setPropertyValue("StableEndTime",str(origEndTime))
        db.setPropertyValue("UnstablePrefix", "")
        db.setPropertyValue("PrefixLID", lid)

    def replace_unstable(self, data):
        text = data["seq"]
        db.setPropertyValue("UnstablePrefix", text)
        db.setPropertyValue("UnstableLID", data.get("lid"))

    def validate_message(self, data):
        if 'controll' in data:
            required_keys = ["controll", "session", "tag", "sender"]
        else:
            required_keys = ["start", "end", "session", "tag", "sender", "seq"]
        if any(key not in data for key in required_keys):
            print("WARNING: Got corrupted input: message is missing", [key for key in required_keys if key not in data])
            return False
        return True

class Translator():
    def __init__(self,args):
        self.args = args

        self.mt_server_index = 0

    def translate(self, text, session, stream, *args, unstable=True, **kwargs):
        mode = db.getPropertyValues("mode");
        mt_server = self.get_mt_server()
        print(f"Got server {mt_server}")

        d = {'text': text, "unstable":unstable};
        if(mode == "SendPartial" or mode == "SendPartialAndUnstable"):
            prefix = db.getPropertyValues("StableTargetPrefix")
            if(prefix == None):
                prefix = ""
            d["prefix"] = prefix

        version = db.getPropertyValues("version")
        priority = 0
        if version == "online":
            priority = 1

        d['priority'] = priority

        print("Translating:",d)

        lang_pair = db.getPropertyValues("language")
        assert '-' in lang_pair
        source_lang, _, target_lang = lang_pair.partition('-')

        lid = kwargs.get("lid")
        if ("lid" in kwargs and kwargs["lid"] == target_lang) or not text.strip():
            translation = text
            print(f"[INFO] detected {lid=} == {target_lang=}. Passing through!")
        else:
            print(f"[INFO] detected {lid=} != {target_lang=}. Running translation!")
            try:
                translation = requests.post(mt_server, data=d)
                if translation.status_code != 200:
                    raise requests.exceptions.ConnectionError
            except requests.exceptions.ConnectionError:
                print("WARNING: Could not execute translation with mt_server, sent data",d)
                translation = ""
            else:
                try:
                    translation = translation.json()
                    if "status" in translation and translation["status"] != 200:
                        print("WARNING: Got non 200 status code.")
                        translation = ""
                    else:
                        translation = translation["hypo"]
                except requests.exceptions.JSONDecodeError:
                    translation = translation.text
                except KeyError:
                    print("WARNING: No hypo sent")
                    translation = ""

        print("Translation:",translation)
        return translation

    def get_mt_server(self,version=False):
        language = db.getPropertyValues("language")
        mt_server = db.getPropertyValues("mt_server_"+language)
        if mt_server is None:
            print("WARNING: Unknown language, using default mt_server")
            mt_server = db.getPropertyValues("mt_server_default")

        mt_server = mt_server.split("|")
        self.mt_server_index %= len(mt_server) # Necessary if mt_server changes during session

        s = mt_server[self.mt_server_index]
        self.mt_server_index += 1
        self.mt_server_index %= len(mt_server)

        if version:
            url = s.replace("predictions","models")
            return url

        return s

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    parser.add_argument('--partitions', help='Number of worker partitions', type=int, default=25)
    args = parser.parse_args()

    name = "mt"

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)
    tokenizer = sacremoses.MosesTokenizer()
    detokenizer = sacremoses.MosesDetokenizer()

    with open('config.json', 'r') as f:
        config = json.load(f)

    d = {"languages": []}
    for k,v in config.items():
        property_to_default[k] = v
        if k.startswith("mt_server_"):
            language = k[len("mt_server_"):]
            d["languages"].append(language)

    # FIXME `partitions` argument doesn't exist for connectors other than Kafka
    con = get_best_connector()(args.queue_server,args.queue_port,db, partitions=args.partitions)
    con.register(db.db, name, d)  # TODO: Change-> Very good but should be done with DB

    translator = Translator(args)
    segmenter = Segmenter(args,translator)
    con.consume(name,segmenter,True)

