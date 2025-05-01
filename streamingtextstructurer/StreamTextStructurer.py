#!/usr/bin/env python3

import time
import argparse
import json
import jsons
from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
import os
import requests
from qbmediator.Text import get_punctuation

punctuation = get_punctuation()

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

    def get(self, key, default=None):
        """Gets a value from the database. If the key does not exist, returns the default value."""
        property = db.getPropertyValues(key)
        if property is None:
            return default
        try:
            return jsons.loads(property)
        except json.JSONDecodeError:
            return property

pp = PropertyProvider()

class Structurer():

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

            #print(data)
            print(sender_id)

            lead = pp["leadingStream"]

            if "controll" in data:
                self.handle_control_message(data, session, stream, sender_component, sender_id)
            elif lead == sender_info or sender_component == "asr":
                if not "seq" in data:
                    print("Warning: No input seq found, ignoring!")
                    return

                if "unstable" not in data or not data["unstable"]:
                    prefix = self.append_stable(data)

                    #print(prefix)

                    if lead == sender_info:
                        match pp["method"]:
                            case "online_model":
                                self.process_online(prefix, session, stream, lead)
                            case "streaming_simple":
                                self.processSimple(prefix, session, stream)

                    if sender_component == "asr":
                        self.process_asr_lids(prefix, session, stream)

                    self.send_waiting_messages(session, stream, prefix, pp["waiting_messages"])
    
    def set_property(self, key, value):
        db.setPropertyValue(key, jsons.dumps(value))

    def handle_control_message(self, data, session, stream, sender_component, sender_id):
        id = f"{sender_component}:{sender_id}"
        match data["controll"]:
            case "START":  
                print("Start message received", data)             
                l = self.get_display_language(sender_component, session, sender_id)

                pp.set_key("languages", id, l)
                pp.set_key("StablePrefix", id, [])
                pp.set_key("waiting_messages", id, [])
                pp.set_key("out_lang", id, l)

                pp["last_lid"] = str(0)
                if sender_component == "asr":
                    pp["asrStream"] = f"{sender_component}:{sender_id}"
                if l == "en":
                    pp["leadingStream"] = f"{sender_component}:{sender_id}"
                    self.init_structurer(db)
                data['sender'] = f"{name}:{stream}_{l}"
                data["mode"] = pp["method"]
                con.publish("mediator", session, jsons.dumps(data))  # Send START to further components
                
            case "END":
                if id is None:
                    return
                lead = pp["leadingStream"]
                if(lead == id):
                    self.finish(session, stream, lead)
                    languages = pp["languages"]
                    for id in languages:
                        data["sender"] = f"{name}:{stream}_{languages[id]}"
                        con.publish("mediator", session, jsons.dumps(data))

            case "CLEAR":
                print("CLEAR message received")
                self.init_structurer(db, reset_prefixes=True)
                        
            case "INFORMATION":
                properties = db.getPropertyValues()
                l = self.get_display_language(sender_component, session, sender_id)
                sender = f"{name}:{stream}_{l}"
                data = {'session': session, 'controll': "INFORMATION", 'sender': sender, sender: properties}
                data["sender"] = sender
                con.publish("mediator", session, jsons.dumps(data))

            case _:
                print("Unkonw controll message", data)

    def init_structurer(self, db, reset_prefixes=False):
        db.setPropertyValue("nextSend", pp["paragraphLength"])
        db.setPropertyValue("num_finished_sentences", str(0))
        db.setPropertyValue("title_time", str(0.01))
        db.setPropertyValue("title_index", str(0))
        db.setPropertyValue("sentences_since_segment_start", str(0))
        db.setPropertyValue("previous_titles", jsons.dumps([]))

        if reset_prefixes:
            prefix = pp["StablePrefix"]
            for sender in prefix:
                prefix[sender] = []
            pp["StablePrefix"] = prefix
            pp["last_lid"] = str(0)

    def send_waiting_messages(self, session, stream, prefix, waiting_messages):
        out_lang = pp["out_lang"]
        if not prefix:
            return
        for sender in prefix:
            sender_waiting_messages = waiting_messages[sender]
            while len(sender_waiting_messages) > 0:
                data = {}
                data["session"] = session
                data["mode"] = pp["method"]
                data["sender"] = f"{name}:{stream}_{out_lang[sender]}"
                data["start"] = sender_waiting_messages[0][2]
                data["end"] = sender_waiting_messages[0][3]
                data["seq"] = sender_waiting_messages[0][0]
                data["markup"] = sender_waiting_messages[0][1]
                data["unstable"] = False
                con.publish("mediator", session, jsons.dumps(data))
                #print("Send message:", data)
                sender_waiting_messages = sender_waiting_messages[1:]
            waiting_messages[sender] = sender_waiting_messages
        pp["waiting_messages"] = waiting_messages

    def send_end(self, session, stream):
        print("Sending END.", session, stream)
        data={'session': session, 'controll': "END", 'sender': f"{name}:{stream}"}
        con.publish("mediator", f"{session}/{stream}", jsons.dumps(data))

    def finish(self, session, stream, lead):
        method = pp["method"]
        print("Finish with Method:",method)
        if method == "online_model":
            prefix = pp["StablePrefix"]
            self.process_online(prefix, session, stream, lead, True)
        elif method == "offline_model":
            self.process_offline(session, stream, lead)

        
    def processSimple(self, prefix, session, stream):
        return
        #print("processSimple")
        lead = int(db.getPropertyValues("leadingStream"))
        if(lead == "-1"):
            return
        nextSend = int(db.getPropertyValues("nextSend"))
        out_lang = json.loads(db.getPropertyValues("out_lang"))
        waiting_messages = json.loads(db.getPropertyValues("waiting_messages"))
        #print("Lead:",lead)
        #print("Prefix:",len(prefix),prefix)
        #print("Waiting:",len(waiting_messages),waiting_messages)
        #print("out-lang",len(out_lang),out_lang)
        #print("NextSend:",nextSend)
        if(lead == "-1"):
            return
        
        last_lid = pp["last_lid"]
        
        if (len(prefix[lead]) > nextSend):                             
            msg = ("<br>", "paragraphBreak", str(float(prefix[lead][nextSend-1]["end"])-0.02),prefix[lead][nextSend-1]["end"])
            #print("Add message",msg)
            for sender in prefix:
                waiting_messages[sender].append(msg)
                #print("to ", sender)
            nextSend += int(db.getPropertyValues("paragraphLength"))
            db.setPropertyValue("nextSend",nextSend)
        elif prefix[lead][nextSend-1]["lid"] != last_lid:
            msg = ("<br>", "paragraphBreak", str(float(prefix[lead][nextSend-1]["end"])-0.02),prefix[lead][nextSend-1]["end"])
            for sender in prefix:
                waiting_messages[sender].append(msg)
            last_lid = prefix[lead][nextSend-1]["lid"]
            pp["last_lid"] = last_lid

        self.send_waiting_messages(session, stream, prefix, waiting_messages)

    @property
    def parameters(self):
        return db.getPropertyValues("model_parameters")[db.getPropertyValues("method")]

    @property
    def title_index(self):
        return int(db.getPropertyValues("title_index"))
    
    @title_index.setter
    def title_index(self, value):
        db.setPropertyValue("title_index", str(value))

    @property
    def sentences_since_segment_start(self):
        tmp = db.getPropertyValues("sentences_since_segment_start")
        if tmp is None:
            print("ERROR: Could not find sentences_since_segment_start in database!")
            return 0
        return int(tmp)
    
    @sentences_since_segment_start.setter
    def sentences_since_segment_start(self, value):
        db.setPropertyValue("sentences_since_segment_start", str(value))

    @property
    def title_time(self):
        return float(db.getPropertyValues("title_time"))
    
    @title_time.setter
    def title_time(self, value):
        db.setPropertyValue("title_time", str(value))
    
    @property
    def num_finished_sentences(self):
        return int(db.getPropertyValues("num_finished_sentences"))
    
    @num_finished_sentences.setter
    def num_finished_sentences(self, value):
        db.setPropertyValue("num_finished_sentences", str(value))
    
    def get_chapter_break_msg(self, prefix, lead, num_finished_sentences):
        return ("<br /><br />", "chapterBreak", str(float(prefix[lead][num_finished_sentences-1]["end"])-0.03), str(float(prefix[lead][num_finished_sentences-1]["end"])-0.01))
    
    def get_paragraph_break_msg(self, prefix, lead, num_finished_sentences):
        return ("<br /><br />", "paragraphBreak", str(float(prefix[lead][num_finished_sentences-1]["end"])-0.02), str(float(prefix[lead][num_finished_sentences-1]["end"])-0.01))
    
    def request_segmentation_model(self, sentences, previous_titles, param, forced_chapter_idxs=[]):
        payload = {
            'sentences': sentences,
            'captions': None,
            'generate_titles': True,
            'prefix_titles': True,
            'previous_titles': previous_titles,
            'chapter_threshold': param["chapter_threshold"],
            'paragraph_threshold': param["paragraph_threshold"],
            'forced_chapter_idxs': forced_chapter_idxs
        }

        headers = {
            'Content-Type': 'application/json'
        }
        
        #print(payload)
        #print(param["endpoint"])
        try:
            response = requests.post(param["endpoint"], data=json.dumps(payload), headers=headers)
            #print("Response:", response)
            #print(response.text)
            response.raise_for_status()
        except requests.exceptions.HTTPError as err:
            print("ERROR!!!!: No connection to segmenter")
            print(err)
            return None
        return response.json()
    
    def translate_title(self, title, target_language):
        if target_language != "en" and len(target_language) == 2:
            data = {"text": title, "priority": 1}
            mt_server = self.get_mt_server(target_language)
            rep = requests.post(mt_server, data=data)
            if rep.status_code == 200 and "hypo" in rep.json():
                #print("Translation:", rep.json())
                hypo: str = rep.json()["hypo"]
                # Remove periods (if not present in the original title)
                if not title.endswith("."):
                    hypo = hypo.rstrip(".")
                    hypo = hypo.rstrip("。")
                title = hypo
            else:
                print(f"WARNING: Could not translate title, response {rep.text}")
        return title  

    def handle_title(self, titles, title_index, title_time, languages, waiting_messages, previous_titles, prefix):
        title = titles[title_index] if title_index < len(titles) else ""
        previous_titles.append(title)
        for sender in prefix:
            #print("Processing Language", languages[sender], sender)
            # Translating title if necessary
            title_ = self.translate_title(title, languages[sender])
            title_msg = (title_, "heading", title_time, title_time + 0.01)
            #print(f"Sent Message {title_msg} ({sender}, {languages[sender]})")
            waiting_messages[sender].append(title_msg)
        title_index += 1
        self.title_index = title_index

    def process_asr_lids(self, prefix, session, stream):
        #print("Processing ASR LIDs")
        asr_stream_id = pp["asrStream"]
        if asr_stream_id == "-1" or not prefix or asr_stream_id not in prefix:
            return
        
        asr_stream = prefix[asr_stream_id]

        if len(asr_stream) == 0:
            return

        waiting_messages = pp["waiting_messages"]
        
        last_lid = pp["last_lid"]
        recent_lid = asr_stream[-1]["lid"]

        #print("Process ASR Lids: ", asr_stream[-1]["seq"], asr_stream[-1]["lid"], last_lid)

        if str(last_lid) != str(0) and str(recent_lid) != str(last_lid):
            print(f"+++ New Language Paragraph at Index {len(asr_stream)}; switching from {last_lid} to {recent_lid} +++")
            timestamp = str(float(asr_stream[-1]["start"])-0.02)
            msg = ("<br /><br />", "paragraphBreak", timestamp, timestamp)
            for sender in prefix:
                waiting_messages[sender].append(msg)
        pp["last_lid"] = recent_lid

        self.send_waiting_messages(session, stream, prefix, waiting_messages)

        
    def process_online(self, prefix, session, stream, lead, end=False):
        #print(stream, " ", lead)

        waiting_messages = pp["waiting_messages"]
        previous_titles = pp["previous_titles"]
        forced_chapter_idxs = pp.get("forced_chapter_idxs", [])
        pending_title_indices = pp.get("pending_title_indices", [])

        num_finished_sentences, param = self.num_finished_sentences, self.parameters
        chapter_look_ahead = int(param["chapter_look_ahead"])

        time_since_last_chapter = pp.get("time_since_last_chapter", 0)
        original_chapter_threshold = param["chapter_threshold"]
        current_chapter_threshold = pp.get("current_chapter_threshold", original_chapter_threshold)
        max_sentences_without_chapter = 25
        chapter_reduction_factor = 0.95
        min_sentences_for_retroactive_chapter = 5
        last_chapter_index = pp.get("last_chapter_index", 0)

        max_boundary_prob = 0
        max_boundary_index = last_chapter_index

        print("Dynamic Threshold", pp.get("dynamic_threshold"))
        dynamic_threshold = pp.get("dynamic_threshold", True)
        if not dynamic_threshold:
            current_chapter_threshold = original_chapter_threshold

        print(f"~~ -- ++ {len(prefix[lead])} number of sentences in the prefix ++ ~~ -- ", num_finished_sentences + chapter_look_ahead + 1)
        if not end and len(prefix[lead]) <= num_finished_sentences + chapter_look_ahead + 1:
            self.send_waiting_messages(session, stream, prefix, waiting_messages)
            return
        
        sentences = [f["seq"].strip() for f in (prefix[lead] if end else prefix[lead][:-1])]

        if len(sentences) == 0:
            return
        
        response = self.request_segmentation_model(sentences, previous_titles, param, forced_chapter_idxs)

        if response == None:
            return
        
        boundaries, titles = response["boundaries"], response["titles"]
        chapter_boundary_probabilities = response["chapter_boundary_probabilities"]
        #print("Boundaries", boundaries)
        #print("Titles", titles)
        #print("Title Index", self.title_index)

        # Handle pending titles first
        while pending_title_indices and self.title_index < len(titles):
            index = pending_title_indices[0]
            if index < num_finished_sentences:
                title = titles[self.title_index]
                title_time = float(prefix[lead][index]["end"]) - 0.02
                #print(f"Insert pending Title: {title} at index {self.title_index}")
                self.handle_title(titles, self.title_index, title_time, pp['languages'], waiting_messages, previous_titles, prefix)
                pending_title_indices.pop(0)
            else:
                break

        # Handle first chapter title if not already done
        if len(previous_titles) == 0 and self.title_index == 0 and num_finished_sentences >= chapter_look_ahead:
            title = titles[0]
            title_time = float(prefix[lead][0]["end"]) - 0.02
            #print(f"Insert first Title: {title} at index 0")
            self.handle_title(titles, 0, title_time, pp['languages'], waiting_messages, previous_titles, prefix)
            self.title_index = 1
            last_chapter_index = 0

        while (len(prefix[lead]) - 1) >= num_finished_sentences + (0 if end else chapter_look_ahead) + 1:
            title_index, title_time, languages = self.title_index, self.title_time, pp['languages']
            #print("~~ -- Processing Sentence Index -- ~~", num_finished_sentences)
            #print("Current sentences_since_segment_start", self.sentences_since_segment_start)
            
            if dynamic_threshold and time_since_last_chapter > max_sentences_without_chapter:
                current_chapter_threshold *= chapter_reduction_factor
                #print(f"Lowering chapter threshold to {current_chapter_threshold}")

            current_boundary_prob = chapter_boundary_probabilities[num_finished_sentences]

            if num_finished_sentences > last_chapter_index + min_sentences_for_retroactive_chapter:
                if current_boundary_prob > max_boundary_prob:
                    max_boundary_prob = current_boundary_prob
                    max_boundary_index = num_finished_sentences

            is_chapter = boundaries[num_finished_sentences] == 1

            if is_chapter:
                #print(f"+++ New Chapter at Index {num_finished_sentences} +++")
                msg = self.get_chapter_break_msg(prefix, lead, num_finished_sentences)
                for sender in prefix:
                    waiting_messages[sender].append(msg)
                
                if self.title_index == 0:
                    #print(f"--- Insert Title for Chapter: {titles[self.title_index]}")
                    title_time = 0.01
                    self.handle_title(titles, self.title_index, title_time, languages, waiting_messages, previous_titles, prefix)
                    #print("--- Deferring Title Insertion for Chapter")
                    pending_title_indices.append(num_finished_sentences)
                elif self.title_index < len(titles):
                    #print(f"--- Insert Title for Chapter: {titles[self.title_index]}")
                    title_time = float(prefix[lead][num_finished_sentences]["end"]) - 0.02
                    self.handle_title(titles, self.title_index, title_time, languages, waiting_messages, previous_titles, prefix)
                else:
                    #print("--- Deferring Title Insertion for Chapter")
                    pending_title_indices.append(num_finished_sentences)
                
                self.sentences_since_segment_start = 0
                self.title_time = title_time

                time_since_last_chapter = 0
                current_chapter_threshold = original_chapter_threshold
                last_chapter_index = num_finished_sentences
                max_boundary_prob = 0
                max_boundary_index = last_chapter_index

            elif (dynamic_threshold and time_since_last_chapter > max_sentences_without_chapter and 
                max_boundary_prob >= current_chapter_threshold and 
                max_boundary_index > last_chapter_index + min_sentences_for_retroactive_chapter):
                #print(f"+++ Retroactively adding chapter at Index {max_boundary_index} +++")
                msg = self.get_chapter_break_msg(prefix, lead, max_boundary_index)
                for sender in prefix:
                    waiting_messages[sender].append(msg)
                #print("--- Deferring Title Insertion for Retroactive Chapter")
                pending_title_indices.append(max_boundary_index)
                forced_chapter_idxs.append(max_boundary_index)
                
                time_since_last_chapter = num_finished_sentences - max_boundary_index
                current_chapter_threshold = original_chapter_threshold
                last_chapter_index = max_boundary_index
                max_boundary_prob = 0
                max_boundary_index = last_chapter_index
            else:
                time_since_last_chapter += 1

            paragraph_sentence_idx = num_finished_sentences + chapter_look_ahead - int(param["paragraph_look_ahead"])
            #print("Current Paragraph Index", paragraph_sentence_idx)
            
            if paragraph_sentence_idx < len(boundaries):
                if boundaries[paragraph_sentence_idx] == 2:
                    #print(f"+++ New Paragraph at Index {paragraph_sentence_idx} +++")
                    self.send_paragraph_break(prefix, lead, paragraph_sentence_idx, waiting_messages)

            num_finished_sentences += 1

            if self.sentences_since_segment_start != -1:
                print("Increment sentences_since_segment_start")
                self.sentences_since_segment_start = (self.sentences_since_segment_start+1)

        if end and titles != None and self.title_index < len(titles):
            for sender in prefix:
                title = titles[self.title_index]
                title = self.translate_title(title, pp['languages'][sender])
                title_msg = (title, "heading", self.title_time, self.title_time + 0.01)
                #print(f"Sent end message: {title_msg} to {sender}")
                waiting_messages[sender].append(title_msg)
        
        pp["num_finished_sentences"] = num_finished_sentences
        pp["previous_titles"] = previous_titles
        pp["time_since_segment_start"] = self.sentences_since_segment_start
        pp["time_since_last_chapter"] = time_since_last_chapter
        pp["current_chapter_threshold"] = current_chapter_threshold
        pp["max_boundary_prob"] = max_boundary_prob
        pp["max_boundary_index"] = max_boundary_index
        pp["last_chapter_index"] = last_chapter_index
        pp["forced_chapter_idxs"] = forced_chapter_idxs
        pp["pending_title_indices"] = pending_title_indices
        self.send_waiting_messages(session, stream, prefix, waiting_messages)

        if end:
            self.send_end(session, stream)

    def send_paragraph_break(self, prefix, lead, index, waiting_messages):
        #print("Send Paragraph Break at Index", index)
        msg = self.get_paragraph_break_msg(prefix, lead, index)
        for sender in prefix:
            #print("Add paragraph break to ", sender)
            waiting_messages[sender].append(msg)

    def process_offline(self, session, stream, lead):
        #print("## Processing with Offline Segmentation")
        # Fetching necessary properties from the database

        prefix = pp["StablePrefix"]
        waiting_messages = pp["waiting_messages"]
        previous_titles = []
        
        param = self.parameters
        
        sentences = [f["seq"].strip() for f in prefix[lead]]

        # Handling the case when there are no sentences
        if len(sentences) == 0:
            return
        
        # Requesting segmentation model for processing
        response = self.request_segmentation_model(sentences, previous_titles, param)

        if response == None:
            return
        
        # Printing the response and processing boundaries and titles
        #print("Response", response)
        boundaries, titles = response["boundaries"], response["titles"]
        #print("Boundaries", boundaries)
        #print("Titles", titles)

        title_time, languages = self.title_time, pp['languages']

        title_index = 0
        for sentence_index in range(len(prefix[lead])):
            #print("~~ -- Processing Sentence Index -- ~~", sentence_index)
            if boundaries[sentence_index] == 1:
                # Adding chapter before the segment
                #print(f"+++ New Chapter at Index {sentence_index} +++")
                msg = self.get_chapter_break_msg(prefix, lead, sentence_index)
                for sender in prefix:
                    waiting_messages[sender].append(msg)
                #print("--- Insert Title for Chapter")
                self.handle_title(titles, title_index, title_time, languages, waiting_messages, previous_titles, prefix)
                title_index += 1
                self.title_index = title_index
                title_time = float(prefix[lead][sentence_index - 1]["end"]) - 0.02
                self.title_time = title_time

            elif boundaries[sentence_index] == 2:
                #print(f"+++ New Paragraph at Index {sentence_index} +++")
                self.send_paragraph_break(prefix, lead, sentence_index, waiting_messages)

        # Send last title
        if self.title_index < len(titles):
            for sender in prefix:
                title = titles[self.title_index]
                title = self.translate_title(title, pp['languages'][sender])
                title_msg = (title, "heading", self.title_time, self.title_time + 0.01)
                #print("Sent message:", title_msg, " to ", sender)
                waiting_messages[sender].append(title_msg)
        
        self.send_waiting_messages(session, stream, prefix, waiting_messages)
        self.send_end(session, stream)

    def append_stable(self, data):
        id, _, _ = self.get_sender_info(data)
        prefix = pp["StablePrefix"]
        if id is None or not id in prefix:
            return
        lid = data["lid"] if "lid" in data and data["lid"] is not None else 0
        if len(prefix[id]) == 0:
            prefix[id].append({"seq": data["seq"], "start": data["start"], "end": data["end"], "lid": lid})
        else:
            if len (prefix[id][-1]["seq"].strip()) > 0 and prefix[id][-1]["seq"].strip()[-1] in punctuation:
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
    parser.add_argument('--queue-server', help='Queue Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='Queue Server port', type=str, default='5672')
    parser.add_argument('--redis-server', help='Redis Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='Redis Server port', type=str, default='6379')
    args = parser.parse_args()

    name = "textstructurer"

    with open('config.json', 'r') as f:
        property_to_default = json.load(f)

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    d = {"languages": {}}
    con = get_best_connector()(args.queue_server,args.queue_port,db)
    con.register(db, name, d)

    queue = os.getenv('QUEUE_SYSTEM')
    if queue == "KAFKA" or queue == "KAFKA-P":
        structurer = Structurer(args)
        con.consume(name, structurer, True)
