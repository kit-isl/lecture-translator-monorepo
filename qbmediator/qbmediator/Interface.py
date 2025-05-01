
import base64
import jsons
import json
import time
import numpy as np
import math
import subprocess
import tempfile
import threading

from abc import ABC, abstractmethod

from qbmediator.Database import get_from_db, set_in_db, delete_in_db
from qbmediator.Text import get_punctuation

import sacremoses
tokenizer = sacremoses.MosesTokenizer()
detokenizer = sacremoses.MosesDetokenizer()

punctuation = get_punctuation()

def mycapitalize(s):
    return s[0].upper()+s[1:] if len(s)>0 else ""

class MediatorInterface:
    def __init__(self, name, session, tag, mode="SendStable", track_links=False):
        self.name = name
        self.session = session
        self.tag = tag
        self.streams = []
        self.mode = mode

        self.running = True
        self.con = None
        self.db = None

        self.point_last = False
        self.tmp_audio = bytes()

        self.track_links = track_links
        if self.track_links:
            self.links = []

        self.downloading_url = False
        self.got_end = False

    @abstractmethod
    def process(self):
        pass

    def read_audio(self, seconds=0, stream=None, only_one_chunk=False, return_start_time=False, add_to_start_time=True): # seconds<=0 -> read all new audio
        if stream is None:
            if len(self.streams)==0:
                print("WARNING: Did not yet get audio to return for session", self.session)
                if not return_start_time:
                    return bytes()
                else:
                    return bytes(), None
            stream = self.streams[0]

        result = self.tmp_audio
        self.tmp_audio = bytes()

        index_next_chunk = get_from_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunk")
        if index_next_chunk is None:
            if not return_start_time:
                return result
            else:
                return result, None

        index_next_chunk = int(index_next_chunk.decode("utf-8"))
        next_index_within_chunk = int(get_from_db(self.db, self.name, self.session+"/"+self.tag, "NextIndexWithinChunk").decode("utf-8"))
        num_chunks = int(get_from_db(self.db, self.name, self.session+"/"+self.tag, "NumChunks").decode("utf-8"))

        if return_start_time:
            start_time = get_from_db(self.db, self.name, self.session+"/"+self.tag, "StartTime"+str(index_next_chunk))
            try:
                start_time = float(start_time)
                if add_to_start_time:
                    start_time += next_index_within_chunk / 32000
            except:
                start_time = None

        if seconds<=0:
            i = index_next_chunk
            while True:
                if i == num_chunks:
                    break

                next_chunk = get_from_db(self.db, self.name, self.session+"/"+self.tag, "Chunk"+str(i))
                if next_chunk == b"PAUSE":
                    if len(result) > 0:
                        set_in_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunk", i)
                        set_in_db(self.db, self.name, self.session+"/"+self.tag, "NextIndexWithinChunk", next_index_within_chunk)

                        if not return_start_time:
                            return result
                        else:
                            return result, start_time
                    else:
                        only_one_chunk = True

                delete_in_db(self.db, self.name, self.session+"/"+self.tag, "Chunk"+str(i))
                delete_in_db(self.db, self.name, self.session+"/"+self.tag, "StartTime"+str(i))
                result += next_chunk[next_index_within_chunk:]

                next_index_within_chunk = 0
                i += 1

                if only_one_chunk:
                    break

            set_in_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunk", i)
            set_in_db(self.db, self.name, self.session+"/"+self.tag, "NextIndexWithinChunk", next_index_within_chunk)

            if not return_start_time:
                return result
            else:
                return result, start_time
        else:
            frames = max(1,int(2*16000*seconds))

            for i in range(index_next_chunk, num_chunks):
                next_chunk = get_from_db(self.db, self.name, self.session+"/"+self.tag, "Chunk"+str(i))
                if next_chunk == b'PAUSE':
                    if len(result) > 0:
                        set_in_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunk", i)
                        set_in_db(self.db, self.name, self.session+"/"+self.tag, "NextIndexWithinChunk", next_index_within_chunk)

                        if not return_start_time:
                            return result
                        else:
                            return result, start_time
                    else:
                        frames = len(result)+len(next_chunk)

                if len(result)+len(next_chunk)-next_index_within_chunk<frames:
                    result += next_chunk[next_index_within_chunk:]
                    next_index_within_chunk = 0
                else:
                    index = frames-len(result)
                    result += next_chunk[next_index_within_chunk:next_index_within_chunk+index]

                    if next_index_within_chunk+index == len(next_chunk):
                        delete_in_db(self.db, self.name, self.session+"/"+self.tag, "Chunk"+str(i))
                        delete_in_db(self.db, self.name, self.session+"/"+self.tag, "StartTime"+str(i))
                        i += 1
                        next_index_within_chunk = 0
                    else:
                        next_index_within_chunk += index

                    set_in_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunk", i)
                    set_in_db(self.db, self.name, self.session+"/"+self.tag, "NextIndexWithinChunk", next_index_within_chunk)

                    if not return_start_time:
                        return result
                    else:
                        return result, start_time

                delete_in_db(self.db, self.name, self.session+"/"+self.tag, "Chunk"+str(i))
                delete_in_db(self.db, self.name, self.session+"/"+self.tag, "StartTime"+str(i))

            self.tmp_audio = result

            set_in_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunk", num_chunks)
            set_in_db(self.db, self.name, self.session+"/"+self.tag, "NextIndexWithinChunk", next_index_within_chunk)

            if return_start_time:
                return bytes(), None

            return bytes()

    def read_text(self, stream=None):
        if stream is None:
            if len(self.streams)==0:
                print("WARNING: Did not yet get text to return for session",self.session)
                return
            stream = self.streams[0]

        chunk = None

        index_next_chunk = get_from_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunkText")
        if index_next_chunk is None:
            return

        index_next_chunk = int(index_next_chunk.decode("utf-8"))
        num_chunks = int(get_from_db(self.db, self.name, self.session+"/"+self.tag, "NumChunksText").decode("utf-8"))

        if index_next_chunk < num_chunks:
            chunk = get_from_db(self.db, self.name, self.session+"/"+self.tag, "ChunkText"+str(index_next_chunk))
            set_in_db(self.db, self.name, self.session+"/"+self.tag, "IndexNextChunkText", index_next_chunk+1)

        if chunk is None:
            return
        return json.loads(chunk)

    def send_keepalive(self, additional_dict=None):
        data = {"session": self.session, "sender": self.name + ":" + self.tag, "start": 0, "end": 0, "unstable": True, "speech_segment_ends": False, "added_point": False, "speech_segment_begins": False, "confidence":0}

        if additional_dict is not None:
            data.update(additional_dict)

        self.con.publish("mediator", self.session + "/" + self.tag, jsons.dumps(data))

    def send_raw_text(self, start, end, text: str, stable=False, segment_ends=False, segment_begins=False, confidence=None, additional_dict=None):
        data = {"session": self.session, "sender": self.name + ":" + self.tag, "start": start, "end": end, "seq": text, "unstable": not stable, "speech_segment_ends": segment_ends, "added_point": False, "speech_segment_begins": segment_begins, "confidence":confidence}

        if additional_dict is not None:
            data.update(additional_dict)

        self.con.publish("mediator", self.session + "/" + self.tag, jsons.dumps(data))

    def send_text(self, start, end, text: str, stable=False, segment_ends=False, segment_begins=False, confidence=None, additional_dict=None):
        if self.mode != "SendUnstable" and not stable:
            return

        if segment_ends and len(text)>0 and text[-1] == ",":
            text = text[:-1]+"."

        added_point = False
        if segment_ends and not self.point_last and ((len(text)>0 and text[-1] not in [*punctuation, ':']) or not text) and (not segment_begins or len(text)>0):
            text += ". " # punctuate and space at the end of the segment
            added_point = True
        elif segment_ends and len(text)>0 and text[-1] == ":":
            text += " " 
        elif segment_ends and (len(text)==0 or text[-1]!=" "):
            text += " " # space at the end of the segment

        if segment_begins:
            text = " "+mycapitalize(text) # capitalize at begin of segment
        elif self.point_last and len(text)>0 and text[0]==" ":
            text = " "+mycapitalize(text[1:]) # capitalize at the begin of a sentence

        text = "".join(text[i] if not text[i-2:i] == ". " else mycapitalize(text[i]) for i in range(len(text))) # capitalize after point within sentence
        if text != "" and stable:
            self.point_last = text[-1:] in punctuation

        #print("Text:",text," Stable:",stable)
            
        if text == "":
            text = " "

        text_segments = []
        if stable:
            text_ = tokenizer.tokenize(text)

            last = 0
            for i in range(len(text_)):
                if text_[i] in punctuation:
                    text_segments.append(detokenizer.detokenize(text_[last:i+1]))
                    last = i+1
            if last < len(text_):
                text_segments.append(detokenizer.detokenize(text_[last:]))
            text_segments = [(" " if i>0 or (text and text[0] == " ") else "")+t for i,t in enumerate(text_segments)]
            if len(text_segments) == 0:
                text_segments.append("")
        else:
            text_segments.append(text)

        segments = []
        p = 0
        #print("New segments with ",len(text_segments),"parts")
        for t in text_segments:
            new_p= 1.0*len(t)/len(text)
            #print("Part:",new_p,p)
            segments.append((t,start+p*(end-start),start+(p+new_p)*(end-start)))
            #print(segments[-1])
            p += new_p

        for i in range(len(segments)):
            text,start,end = segments[i]
            data = {"session":self.session, "sender": self.name+":"+self.tag, "start": start, "end": end, "seq": text, "unstable": not stable, "speech_segment_ends": segment_ends if(i == len(segments)-1) else False , "added_point": added_point if(i == len(segments)-1) else False , "speech_segment_begins": segment_begins if(i == 0) else False, "confidence":confidence}

            if additional_dict is not None:
                data.update(additional_dict)

            #print("Sending", data)
            self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data))

    def send_data(self, key_name, data, encode=True, chunk_size=-1, force_linking=True, send_link=True, additional_dict=None):
        if encode:
            data = base64.b64encode(data).decode("ascii")

        data_dict = {"session":self.session, "sender": self.name+":"+self.tag, "send_link":send_link}
        if additional_dict is not None:
            for k,v in additional_dict.items():
                data_dict[k] = v

        link = None
        if chunk_size == -1:
            data_dict[key_name] = data
            link = self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data_dict), force_linking=force_linking)
        else:
            for i in range(0, len(data), chunk_size):
                data_dict[key_name] = data[i:i+chunk_size]
                link = self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data_dict), force_linking=force_linking)

        if self.track_links and link is not None:
            self.links.append(link)

    def send_audio(self, audio, encode=True, chunk_size=-1, force_linking=True, send_link=True, additional_dict=None):
        self.send_data("b64_enc_pcm_s16le", audio, encode=encode, chunk_size=chunk_size, force_linking=force_linking, send_link=send_link, additional_dict=additional_dict)

    def send_pause(self, encode=True, chunk_size=-1, force_linking=True, send_link=True, additional_dict=None):
        self.send_audio(b'PAUSE', encode=encode, chunk_size=chunk_size, force_linking=force_linking, send_link=send_link, additional_dict=additional_dict)

    def send_video(self, video, encode=True, chunk_size=-1, force_linking=True, send_link=True, additional_dict=None):
        self.send_data("b64_enc_video", video, encode=encode, chunk_size=chunk_size, force_linking=force_linking, send_link=send_link, additional_dict=additional_dict)

    def send_end(self):
        self.process()

        print("Sending END.", self.session, self.tag)
        data={'session': self.session, 'controll': "END", 'sender': self.name+":"+self.tag}
        self.con.publish("mediator", self.session+"/"+self.tag, jsons.dumps(data))

    def new_stream(self, stream):
        if len(self.streams) > 1:
            print("ERROR: Currently only one stream possible, ignoring request.")
        elif stream not in self.streams:
            self.streams.append(stream)

    def remove_links(self):
        if self.track_links and len(self.links) > 0:
            for link in self.links:
                self.db.decreaseDataCount(link)
            self.links = []

    def send_keepalive(self):
        if self.con is not None:
            self.con.consumer.pause()
            msgList = self.con.consumer.poll()
            if len(msgList) > 0:
                print("ERROR: Keepalive got messages")
            self.con.consumer.resume()

class ActiveSessionStorage(ABC): # Not the same as the session in the mediator, this instance stores MediatorInterface instances which are active
    def __init__(self, name, con, db, use_callbacks=False):
        self.name = name
        self.con = con
        self.db = db

        self.sessions = {}
        self.access_times = {}

        self.time_update = 60*10 # in seconds
        self.time_delete = 60*60 # in seconds
        self.last_update = time.time()

        if use_callbacks:
            callback = threading.Thread(target=self.send_callbacks)
            callback.daemon = True
            callback.start()

    def processAggregate(self,mList):
        for message in mList:
            data = json.loads(message)
            session = data["session"]
            stream = data["tag"]

            self.process(message)

        key = (self.name, session, stream)
        value = self.get(key)

        if value is not None:
            value.process()

        if time.time() > self.last_update + self.time_update:
            print("Cleaning up possible old sessions")

            del_keys = []
            for k,t in self.access_times.items():
                if t < time.time() - self.time_delete:
                    if k in self.sessions:
                        deletable = self.delete(k, only_return_deletable=True)
                        if deletable:
                            del_keys.append(k)

            for k in del_keys:
                if k in self.sessions:
                    del self.sessions[k]

            self.access_times = {k:v for k,v in self.access_times.items() if k in self.sessions}
            self.last_update = time.time()

    def set(self, key, value):
        value.con = self.con
        value.db = self.db
        self.sessions[key] = value
        self.access_times[key] = time.time()

    def delete(self, key, only_return_deletable=False):
        value = self.get(key)
        if value is None:
            print("WARNING: key "+str(key)+" not found, ignoring end message.")
            return
        res = self.on_end(value)
        if not only_return_deletable:
            if res != False:
                del self.sessions[key]
            if key in self.access_times:
                del self.access_times[key]
        else:
            return True

    def get(self, key):
        if key not in self.sessions:
            return None
        value = self.sessions[key]
        self.access_times[key] = time.time()
        return value

    def save_audio(self, key, data):
        name, session, tag = key
        pcm_s16le = data["pcm_s16le"]

        value = self.get(key)
        if value is None:
            print("WARNING: key not found, starting session.")
            self.on_start(key,data)
            value = self.get(key)
            if value is None:
                print("ERROR: starting session did not work, ignoring request.")
                return

        value.new_stream(tag)

        # Save audio in database
        num_chunks = get_from_db(self.db, name, session+"/"+tag, "NumChunks")
        if num_chunks is None:
            num_chunks = 0

            set_in_db(self.db, name, session+"/"+tag, "IndexNextChunk", 0)
            set_in_db(self.db, name, session+"/"+tag, "NextIndexWithinChunk", 0)
        else:
            num_chunks = int(num_chunks.decode("utf-8"))

        set_in_db(self.db, name, session+"/"+tag, "Chunk"+str(num_chunks), pcm_s16le)
        set_in_db(self.db, name, session+"/"+tag, "StartTime"+str(num_chunks), data["start"] if "start" in data and data["start"] is not None else "None")
        set_in_db(self.db, name, session+"/"+tag, "NumChunks", num_chunks+1)

    def save_text(self, key, data):
        name, session, tag = key

        value = self.get(key)
        if value is None:
            print("WARNING: key not found, starting session.")
            self.on_start(key,data)
            value = self.get(key)
            if value is None:
                print("ERROR: starting session did not work, ignoring request.")
                return

        value.new_stream(tag)

        # Save text in database
        num_chunks = get_from_db(self.db, name, session+"/"+tag, "NumChunksText")
        if num_chunks is None:
            num_chunks = 0

            set_in_db(self.db, name, session+"/"+tag, "IndexNextChunkText", 0)
        else:
            num_chunks = int(num_chunks.decode("utf-8"))

        set_in_db(self.db, name, session+"/"+tag, "ChunkText"+str(num_chunks), json.dumps(data))
        set_in_db(self.db, name, session+"/"+tag, "NumChunksText", num_chunks+1)

    def process_url(self, key, data):
        print("Process url in new thread: ",data["url"])

        process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -vn -i "+data["url"]+" -map 0:a -ac 1 -f s16le -ar 16000 -c:a pcm_s16le -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        if process.returncode != 0:
            print("ERROR: Could not convert input to raw audio")
            return
        raw_wav = process.stdout

        del data["url"]
        data["b64_enc_pcm_s16le"] = base64.b64encode(raw_wav).decode("ascii")

        name, session, tag = key
        body_send = json.dumps(data)
        self.con.publish(name, session+"/"+tag, body_send)

    def process(self,body):
        data = json.loads(body)

        session = data["session"]
        tag = data["tag"]
        name = self.name
        key = (name,session,tag)

        if "controll" in data:
            if data["controll"] == "START":
                value = self.get(key)
                if value is not None:
                    print("WARNING: Session is already started!")
                    return
                self.on_start(key,data)
                value = self.get(key)
                if value is None:
                    print("ERROR: Starting of session did not work")
                    return
            elif data["controll"] == "END":
                value = self.get(key)
                if value is None:
                    print("WARNING: Ignoring end message!")
                    return
                value.process()
                if value.downloading_url:
                    value.got_end = True
                    return
                self.delete(key)
            elif data["controll"] == "INFORMATION":
                self.on_information(key)
            elif data["controll"] == "CLEAR":
                self.on_clear(key)
                data["sender"] = name+":"+tag
                self.con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send CLEAR to further components
            else:
                print("WARNING: Found unknown controll request "+str(data["controll"])+", ignoring.")
            return

        if "b64_enc_pcm_s16le" in data:
            if("linkedData" in data):
                data["pcm_s16le"] = base64.b64decode(self.db.get(data["b64_enc_pcm_s16le"]))
            else:
                data["pcm_s16le"] = base64.b64decode(data["b64_enc_pcm_s16le"])
            self.save_audio(key,data)

            value = self.get(key)
            if value is None:
                print("WARNING: Ignoring b64_enc_pcm_s16le input!")
                return
            value.downloading_url = False
            if value.got_end:
                value.process()
                self.delete(key)

        if "json_audio_float" in data:
            if("linkedData" in data):
                audio = base64.b64decode(self.db.get(data["json_audio_float"]))
            else:
                audio = data["json_audio_float"]["audioData"]
            audio = np.array(audio) * math.pow(2, 15)
            data["pcm_s16le"] = audio.astype(np.int16).tobytes()
            self.save_audio(key,data)

        if "b64_enc_audio" in data:
            if("linkedData" in data):
                audio = base64.b64decode(self.db.get(data["b64_enc_audio"]))
            else:
                audio = base64.b64decode(data["b64_enc_audio"])

            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(audio)
                tmp.flush()
                process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -vn -i "+tmp.name+" -map 0:a -ac 1 -f s16le -ar 16000 -c:a pcm_s16le -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                if process.returncode != 0:
                    print("ERROR: Could not convert input to raw audio")
                    return
                raw_wav = process.stdout
                data["pcm_s16le"] = raw_wav
            self.save_audio(key,data)

        if "url" in data:
            value = self.get(key)
            if value is None:
                print("WARNING: Ignoring url input!")
                return
            value.downloading_url = True
            thread = threading.Thread(target=self.process_url, args=(key, data))
            thread.daemon = True
            thread.start()
            
        if "seq" in data:
            self.save_text(key,data)

        return key, data

    def send_callbacks(self):
        while True:
            next_timestamp = None
            try:
                for key,value in self.sessions.items():
                    if hasattr(value, "next_callback") and value.next_callback is not None:
                        timestamp = value.next_callback
                        if timestamp <= time.time():
                            name, session, tag = key
                            value.next_callback = None
                            self.send_callback(name, session, tag)
                        elif next_timestamp is None or timestamp < next_timestamp:
                            next_timestamp = timestamp
            except RuntimeError as e: # size of self.sessions can change during iteration
                print("WARNING:",e,"ignoring.")
            if next_timestamp is None:
                time.sleep(0.1)
            else:
                seconds = min(next_timestamp-time.time(),0.1)
                if seconds > 0:
                    time.sleep(seconds)

    def send_callback(self, name, session, tag):
        body_send = json.dumps({"session": session, "tag": tag, "callback": True})
        self.con.publish(name, session+"/"+tag, body_send)

    @abstractmethod
    def on_start(self, key):
        pass

    @abstractmethod
    def on_end(self, session):
        pass

    @abstractmethod
    def on_information(self, key):
        pass
    
    def on_clear(self, key):
        pass
