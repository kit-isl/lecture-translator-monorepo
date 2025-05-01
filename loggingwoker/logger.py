#!/usr/bin/env python3
import argparse
import base64
import bisect
import jsons
import json
import magic

import time;

from qbmediator.Session import Session
from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database

import redis

import pika
import mimetypes
import os
from qbmediator.Database import get_used_property, set_in_db



def load_session(session):
    string = db.get("Session" + session).decode("utf-8");
    s = jsons.load(json.loads(db.get("Session" + session).decode("utf-8")), Session)
    return s


#def set_properties(session, properties, external=False):
#    for component,property_dict in properties.items():
#        component = component.split(":")
#        print("Set properties for ",component)
#        if len(component) == 2:
#            component, tag = component
#        elif len(component) == 1:
#            component = component[0]
#            tag = "All"
#        else:
#            print("ERROR in set properties")
#            continue
#        for prop, value in property_dict.items():
#            set_in_db(db, component+":"+tag, session+"/"+tag, prop, value, external=external)

wav_header = b"RIFF\xf0\xb2\x1e\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT\x0e\x00\x00\x00Lavf58.29.100\x00data\xaa\xb2\x1e\x00"

class Logging:

    def send_audio(self, pcm_s16le, start_time, session, tag):
       data_dict = {"session": session, "sender": self.name + ":" + tag, "send_link": True, "start": -1}
       data_dict['b64_enc_pcm_s16le'] = base64.b64encode(pcm_s16le).decode("ascii")
       self.con.publish("mediator", session + "/" + tag, jsons.dumps(data_dict), force_linking=True)


    def delete_logs(self, directory):
        # Loop through all files in the specified directory
        if not os.path.exists(directory):
            return
        for file_name in os.listdir(directory):
            file_path = os.path.join(directory, file_name)
        
            # Ensure it's a file and not a directory
            if os.path.isfile(file_path):
                # Guess the MIME type of the file
                mime_type = magic.detect_from_filename(file_path).mime_type
                #mime_type, _ = mimetypes.guess_type(file_path)
                # If the MIME type starts with 'text/', it's a text file
                if mime_type and (mime_type.startswith('text/') or mime_type.startswith('application/')):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"Error deleting {file_path}: {e}")




    def store_session(self,s, filename):
        print("Store session to:",filename)
        os.makedirs(filename+"/", exist_ok=True)
        f = open(filename+"/sessionGraph","w")
        d = []
        d.append(jsons.dump(s))
        for c in s.graph:
            db.setContext(c.split(":")[0], str(s.id), c.split(":")[1])
            prop = db.getPropertyValues("display_language")
            d.append({"component":c,"display_language":prop})
        f.write(json.dumps(d))
        f.close()
    def __init__(self, name, con):
        self.name = name
        self.con = con

    def process(self, body):
        data = json.loads(body)
        if "b64_enc_pcm_s16le" not in data and "b64_enc_audio" not in data and "json_audio_float" not in data and "b64_enc_video" not in data:
            print(" [x] Received %r " % (body))

        session=data["session"]
        s = load_session(session)
        if "stream" in data:
            stream = data["stream"]
        elif "tag" in data:
            stream = data["tag"]
        else:
            stream = ""
        sender = data["sender"]

        date_string = s.get_start_date();
        default_dir =  location+"/"+date_string+"/"+session

        db.setContext(self.name, session, stream)

        if("controll" in data):
            if data["controll"] == "START":
                directory = default_dir
                if "directory" in data:
                    directory = data["directory"]
                if("replace_logs" in data and sender == "user:0"):
                    self.delete_logs(data["directory"])

                db.setPropertyValue("directory",directory)
                self.store_session(s,directory)
                db.setContext(self.name, session, stream)

        dir = db.getPropertyValues("directory")
        if(dir == None):
            dir = default_dir
        print("Create directory",dir)
        os.makedirs(dir+"/", exist_ok=True)
        body = self.unlinkedData(body)


        if (False and "action" in data and data["action"] == "selection" and os.path.isfile(dir+"/audio_offsets.jsonl") and
            (data['component'].endswith('Reference') or data['component'] in ('German', 'English', 'Multilingual-7'))):
            with open(dir+"/audio_offsets.jsonl", 'r') as f:
                recorded_audios = f.read().splitlines()
            audio_index = bisect.bisect_right(recorded_audios, data["start"],
                                              key=lambda recorded_audio: json.loads(recorded_audio)["end"])

            if audio_index < len(recorded_audios):
                recorded_audio = json.loads(recorded_audios[audio_index])
                start_offset = recorded_audio["offset"] + int(max(0, data["start"] - recorded_audio["start"]) * 32000 // 2) * 2
                number_bytes = 0

                while recorded_audio is not None and data["end"] > recorded_audio["start"]:
                    if recorded_audio["end"] <= data["end"]:
                        number_bytes += recorded_audio["audio_len"]
                    else:
                        number_bytes += int((data["end"] - recorded_audio["start"]) * 32000 // 2) * 2

                    audio_index += 1
                    recorded_audio = json.loads(recorded_audios[audio_index]) if audio_index < len(recorded_audios) else None

                print("Offset and len in bytes:", start_offset, number_bytes)

                with open(dir+"/audio.wav", 'rb') as f:
                    f.seek(start_offset)
                    audio_to_send = f.read(number_bytes)
                    self.send_audio(audio_to_send, data["start"], session, 'v1')
                    '''
                    with open(dir+"/selection.wav", 'wb') as f1:
                        f1.write(wav_header)
                        f1.write(audio_to_send)
                   '''
        if not os.path.isfile(dir+"/audio.wav"):
            with open(dir+"/audio.wav", 'wb') as f:
                f.write(wav_header)


        if "b64_enc_pcm_s16le" in data:
            #print("sgc", data["b64_enc_pcm_s16le"][-10:], len(data["b64_enc_pcm_s16le"]))
            try:
                pcm_s16le = base64.b64decode(data["b64_enc_pcm_s16le"])
            except Exception as e:
                print(e)
                pcm_s16le = b''
            with open(dir+"/audio.wav", 'ab') as f:
                f.write(pcm_s16le)
            if "start" in data:
                audio_end = data["start"] + len(data["b64_enc_pcm_s16le"]) / 2 / 16000
                if os.path.isfile(dir+"/audio_offsets.jsonl"):
                    with open(dir+"/audio_offsets.jsonl", 'r') as f:
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        bytes_read = 0
                        f.seek(0)
                        file_content_tail = ""
                        while "\n" not in file_content_tail[:512] and file_size > bytes_read:
                            number_bytes_to_read = min(512, file_size - bytes_read)
                            f.seek(file_size - bytes_read - number_bytes_to_read)
                            file_content_tail = f.read(number_bytes_to_read) + file_content_tail
                            bytes_read += number_bytes_to_read
                        file_content_tail_splitted = file_content_tail.rsplit('\n', 2)
                        if len(file_content_tail_splitted) > 2:
                            last_recorded_audio_infos = json.loads(file_content_tail_splitted[-2])
                            offset = last_recorded_audio_infos["offset"] + last_recorded_audio_infos["audio_len"]
                        else:
                            offset = len(wav_header)
                else:
                    offset = len(wav_header)
                recorded_audio = {"start": data["start"], "end": audio_end, "offset": offset, "audio_len": len(pcm_s16le)}
                with open(dir+"/audio_offsets.jsonl", 'a') as f:
                    json.dump(recorded_audio, f)
                    f.write('\n')


        with open(dir+"/"+sender, 'a') as f:
            if type(body) == bytes:
                body = body.decode("utf-8")
            f.write(body+"\n");
        #print("Message:",body)
        #print("Send variable:","LTArchive_"+os.path.abspath(dir).removeprefix(dirprefix)+"_version")
        db.set("LTArchive_"+os.path.abspath(dir).removeprefix(dirprefix)+"_version",-1) #archive needs to be build

    def unlinkedData(self,body):
        data = json.loads(body)
        if ("linkedData" in data and data["linkedData"] == True):
            for tag in ["b64_enc_pcm_s16le","json_audio_float","b64_enc_audio"]:
                if tag in data:
                    #print(db.get(data[tag]))
                    content = db.get(data[tag])
                    data[tag] = content.decode('utf-8')
                    data["linkedData"] = False
                    body = jsons.dumps(data)
                    #print(body)
        return body


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    parser.add_argument('--location', help='Location for database', type=str, default='./')
    parser.add_argument('--dirprefix', help='Location for database', type=str, default='/logs/archive')
    args = parser.parse_args()

    name = "log"
    location = args.location
    dirprefix=args.dirprefix

    print("Initialize the logger...")


    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties({"display_language": None, "directory" : None})
    con = get_best_connector()(args.queue_server,args.queue_port,db)

    logger = Logging(name, con)
    con.consume(name, logger)
