#!/usr/bin/env python3

import argparse
from collections import defaultdict
from functools import partial
import json
import time
import traceback
import jsons
from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
import os
import asyncio
import aiohttp
from qbmediator.Text import get_punctuation
from queue import Queue
from threading import Thread

punctuation = get_punctuation()

property_to_default = {
    "display_language": "en",
    "output_languages": "[]",
    "out_lang": "{}",
    "finished_sentences": "0",
    "method": "online_model",
    "StablePrefix": "{}",
    "languages": "{}",
    "leadingStream": "-1",
    "model_parameters": {
        "postproduction": {
            "endpoint": "http://192.168.0.68:4000/summarize",
            "min_words": {
                50: 20,
                70: 16,
                90: 12
            }
        },
    },
    "mt_server_default": "http://i13hpc64.cluster:5051/predictions/",
    "compression_rate": "None",
}

class PropertyProvider():
    def __getitem__(self, key):
        property = db.getPropertyValues(key)
        if isinstance(property, dict):
            return property
        try:
            return jsons.loads(property)
        except json.JSONDecodeError:
            return property

    def __setitem__(self, key, value):
        if isinstance(value, (list, dict)):
            value = jsons.dumps(value)
        db.setPropertyValue(key, value)

    def append(self, key, value):
        properties = db.getPropertyValues(key)
        if properties is None:
            properties = []
        else:
            properties = json.loads(properties)
        properties.append(value)
        db.setPropertyValue(key, jsons.dumps(properties))
        return properties

    def set_key(self, key, dict_key, value):
        properties = db.getPropertyValues(key)
        if properties is None:
            properties = {}
        else:
            properties = json.loads(properties)
        properties[dict_key] = value
        db.setPropertyValue(key, jsons.dumps(properties))
        return properties

pp = PropertyProvider()

class Postproduction():
    def __init__(self, args):
        self.args = args
        self.mt_server_index = 0
        self.session = None
        self.running = True
        self.loop = None
        self.task_queue = Queue()

    async def initialize(self):
        self.session = aiohttp.ClientSession()
        print("aiohttp ClientSession initialized")

    async def cleanup(self):
        if self.session:
            await self.session.close()
        print("Cleanup completed")

    async def get_sender_info(self, data):
        if "sender" in data:
            sender_component, sender_id = data["sender"].split(":")[:2]
            return f"{sender_component}:{sender_id}", sender_component, str(sender_id)
        return None, None, None

    async def get_display_language(self, sender_component, session, sender_id):
        return db.getPropertyValues("display_language", context=[sender_component, session, str(sender_id)])
        
    async def handle_control_message(self, data, session, stream, sender_component, sender_id):
        id = f"{sender_component}:{sender_id}"
        l = await self.get_display_language(sender_component, session, sender_id)
        match data["controll"]:
            case "START":
                languages = db.getPropertyValues("languages", context=[name, session, stream.split("_")[0]])
                if languages is None:
                    languages = {}
                languages = jsons.loads(languages)
                languages[id] = l
                db.setPropertyValue("languages", jsons.dumps(languages), context=[name, session, stream.split("_")[0]])

                pp.set_key("StablePrefix", id, [])
                pp.set_key("out_lang", id, l)
                if l == "en":
                    pp["leadingStream"] = id
                data['sender'] = f"{name}:{stream}"
                con.publish("mediator", session, jsons.dumps(data))
                
            case "END":
                lead = pp["leadingStream"]
                if lead == id:
                    await self.finish(session, stream)
                languages = db.getPropertyValues("languages", context=[name, session, stream.split("_")[0]])
                for id in languages:
                    data["sender"] = f"{name}:{stream}"
                    con.publish("mediator", session, jsons.dumps(data))

            case "CLEAR":
                data["sender"] = f"{name}:{stream}_{l}"
                con.publish("mediator", session, jsons.dumps(data))
                        
            case "INFORMATION":
                properties = db.getPropertyValues()
                sender = f"{name}:{stream}_{l}"
                data = {'session': session, 'controll': "INFORMATION", 'sender': sender, sender: properties}
                data["sender"] = sender
                con.publish("mediator", session, jsons.dumps(data))

    async def send_waiting_messages(self, waiting_messages, session, stream, prefix):        
        for sender in waiting_messages:
            sender_waiting_messages = waiting_messages[sender]
            while sender_waiting_messages:
                msg = sender_waiting_messages[0]
                data = {
                    "session": session,
                    "sender": msg["sender"],
                    "start": msg["start"],
                    "end": msg["end"],
                    "seq": msg["seq"],
                    "markup": msg["markup"],
                    "unstable": False,
                    "compression_rate": pp["compression_rate"],
                    "speakerName": msg.get("speakerName"),
                }
                con.publish("mediator", session, jsons.dumps(data))
                print("Send message:", data)
                sender_waiting_messages = sender_waiting_messages[1:]
            waiting_messages[sender] = sender_waiting_messages

    async def finish(self, session, stream):
        lead = pp["leadingStream"]
        prefix = pp["StablePrefix"]
        if prefix[lead]:            
            await self.postproduction(session, stream, prefix, force=True)

    def get_min_words(self, percentage):
        min_words = pp["model_parameters"]["postproduction"]["min_words"]
        for thresh, words in sorted(min_words.items(), reverse=True):
            print(percentage, thresh, percentage >= thresh)
            if percentage >= thresh:
                return words
        return min(min_words.values())  # Default to the smallest value if percentage is lower than all thresholds

    async def postproduction(self, session, stream, prefix, force=False):
        print("Running Postproduction")
        finished_sentences = max(0, int(pp["finished_sentences"]))
        lead = pp["leadingStream"]
        languages = json.loads(db.getPropertyValues("languages", context=[name, session, stream.split("_")[0]]))
        compression_rate = pp["compression_rate"]
        if compression_rate == 'None':
            print("No compression rate set")
            return
        shorten_percentage = int(compression_rate)
        min_words_required = self.get_min_words(shorten_percentage)
        print("Min Words Required: ", min_words_required, shorten_percentage)
        
        languages_no_translation = [(sender, lang) for sender, lang in languages.items() if lang == "en" or len(lang) != 2]
        languages_need_translation = [(sender, lang) for sender, lang in languages.items() if lang != "en" and len(lang) == 2]
        
        if not prefix or lead not in prefix or not prefix[lead]:
            print("No sentences to process")
            return  # No sentences to process
        
        text = ""
        start_time = None
        end_time = None
        word_count = 0
        sentences_in_batch = 0
        waiting_messages = defaultdict(list)
        
        while finished_sentences + sentences_in_batch < len(prefix[lead]):
            print(f"Processing sentence {finished_sentences + sentences_in_batch + 1}/{len(prefix[lead])}")
            current_sentence = finished_sentences + sentences_in_batch
            if not start_time:
                start_time = prefix[lead][current_sentence]["start"]

            speaker_name = prefix[lead][current_sentence].get("speakerName")
            
            next_sentence = prefix[lead][current_sentence]["seq"].strip()
            text += " " + next_sentence
            end_time = prefix[lead][current_sentence]["end"]
            word_count += len(next_sentence.split())
            sentences_in_batch += 1

            print("Current Words: ", word_count, shorten_percentage)
            
            if word_count >= min_words_required:
                await self.process_batch(text, start_time, end_time, languages_no_translation, languages_need_translation, session, stream, shorten_percentage, waiting_messages, speaker_name, prefix)
                
                print(f"Processed batch: {sentences_in_batch} sentences, {word_count} words")
                finished_sentences += sentences_in_batch
                
                text = ""
                start_time = None
                end_time = None
                word_count = 0
                sentences_in_batch = 0

        if force and word_count > 0:
            print(f"Force processing remaining sentences: {sentences_in_batch} sentences, {word_count} words")
            await self.process_batch(text, start_time, end_time, languages_no_translation, languages_need_translation, session, stream, shorten_percentage, waiting_messages, speaker_name, prefix)
            
            print(f"Processed remaining batch: {sentences_in_batch} sentences, {word_count} words")
            finished_sentences += sentences_in_batch

        pp["finished_sentences"] = finished_sentences

    async def process_batch(self, text, start_time, end_time, languages_no_translation, languages_need_translation, session, stream, shorten_percentage, waiting_messages, speaker_name, prefix):
        text = text.strip()
        shortened_text = await self.shorten(text, shorten_percentage)
        
        # Ensure the shortened text ends with punctuation
        if shortened_text and shortened_text[-1] not in punctuation:
            shortened_text += "."
        
        # Immediately send shortened text for languages that don't need translation
        for sender, language in languages_no_translation:
            msg = {
                "seq": shortened_text,
                "markup": "postedited",
                "start": start_time,
                "end": end_time,
                "sender": f"{name}:{shorten_percentage}_{language}",
                "speakerName": speaker_name
            }
            waiting_messages[sender].append(msg)
        
        asyncio.create_task(self.send_waiting_messages(waiting_messages, session, stream, prefix))
        waiting_messages = defaultdict(list)
        
        # Translations
        translation_tasks = []
        for sender, language in languages_need_translation:
            mt_server = self.get_mt_server(language, session, stream)
            translation_tasks.append(self.translate(shortened_text, language, mt_server))

        translated_texts = await asyncio.gather(*translation_tasks)

        for (sender, language), translated_text in zip(languages_need_translation, translated_texts):
            msg = {
                "seq": translated_text,
                "markup": "postedited",
                "start": start_time,
                "end": end_time,
                "sender": f"{name}:{shorten_percentage}_{language}",
                "speakerName": speaker_name
            }
            waiting_messages[sender].append(msg)
        
        asyncio.create_task(self.send_waiting_messages(waiting_messages, session, stream, prefix))


    async def shorten(self, text, percentage):
        param = pp["model_parameters"]["postproduction"]
        payload = {
            'text': text,
            'summary_type': "rephrase_and_shorten",
            'summary_length': f"{percentage}%"
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
        
        try:
            async with self.session.post(param["endpoint"], json=payload, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['summary']
        except aiohttp.ClientError as e:
            print(f"Error calling shortening API: {e}")
        return text  # Return original text if shortening fails

    async def translate(self, text, language, mt_server):
        data = {"text": text, "priority": 1}
        try:
            async with self.session.post(mt_server, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if "hypo" in result:
                        return result["hypo"]
        except aiohttp.ClientError as e:
            print(f"Error translating to {language}: {e}")
        return text  # Return original text if translation fails
    
    async def append_stable(self, data):
        id, _, _ = await self.get_sender_info(data)
        if id is None:
            return
        prefix = pp["StablePrefix"]
        if id not in prefix:
            prefix[id] = []
        if len(prefix[id]) == 0:
            prefix[id].append({
                "seq": data["seq"],
                "start": data["start"],
                "end": data["end"],
                "speakerName": data.get("speakerName")
            })
        else:
            if len(prefix[id][-1]["seq"].strip()) > 0 and prefix[id][-1]["seq"].strip()[-1] in punctuation:
                prev = prefix[id][-1]
                if prev["seq"] == data["seq"] and prev["start"] == data["start"] and prev["end"] == data["end"]:
                    return prefix
                prefix[id].append({
                    "seq": data["seq"],
                    "start": data["start"],
                    "end": data["end"],
                    "speakerName": data.get("speakerName")
                })
            else:
                prefix[id][-1] = {
                    "seq": prefix[id][-1]["seq"] + " " + data["seq"],
                    "start": prefix[id][-1]["start"],
                    "end": data["end"],
                    "speakerName": prefix[id][-1].get("speakerName")
                }
                
        pp["StablePrefix"] = prefix
        return prefix

    def get_mt_server(self, language, session, stream, version=False):
        try:
            id_ = "mt_server_" + language
            mt_server = db.getPropertyValues(id_, context=[name, session, stream.split("_")[0]])
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
    
    async def process_message(self, message):
        print("Received message")
        try:
            data = json.loads(message)
            session = data["session"]
            stream = data.get("tag", "")
            
            print(name, session, stream)
            db.setContext(name, session, stream)
            
            sender_info, sender_component, sender_id = await self.get_sender_info(data)
            lead = pp["leadingStream"]
            l = await self.get_display_language(sender_component, session, sender_id)

            if "controll" in data:
                print(f"Handling control message: {data['controll']}")
                await self.handle_control_message(data, session, stream, sender_component, sender_id)
            elif lead == sender_info or l == "en":
                print("Received message from leading stream or English message")
                if "unstable" not in data or not data["unstable"]:
                    print("Appending stable message")
                    prefix = await self.append_stable(data)
                    await self.postproduction(session, stream, prefix)
        except Exception as e:
            print(f"Error processing message: {e}")
            print(traceback.format_exc())

    def message_handler(self, message):
        self.task_queue.put(message)

    async def process_task_queue(self):
        while self.running:
            try:
                while not self.task_queue.empty():
                    message = self.task_queue.get_nowait()
                    await self.process_message(message)
                    self.task_queue.task_done()
                await asyncio.sleep(0.025)
            except Exception as e:
                print(f"Error processing task queue: {e}")
                print(traceback.format_exc())

    def start_kafka_consumer(self):
        class MessageProcessor:
            def __init__(self, handler):
                self.handler = handler
            
            def process(self, message):
                self.handler(message)

        processor = MessageProcessor(self.message_handler)
        print("Starting Kafka consumer")
        con.consume(name, processor, blocking=True)

    async def run(self):
        try:
            print("Starting Postproduction run")
            self.loop = asyncio.get_event_loop()
            await self.initialize()

            kafka_thread = Thread(target=self.start_kafka_consumer)
            kafka_thread.start()

            print("Entering main processing loop")
            await self.process_task_queue()

        except Exception as e:
            print(f"Error in run method: {e}")
            print(traceback.format_exc())
        finally:
            print("Exiting run method")
            self.running = False
            kafka_thread.join()
            await self.cleanup()

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='Queue Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='Queue Server port', type=str, default='5672')
    parser.add_argument('--redis-server', help='Redis Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='Redis Server port', type=str, default='6379')
    args = parser.parse_args()

    global name, db, con
    name = "postproduction"

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    d = {"languages": []}
    con = get_best_connector()(args.queue_server, args.queue_port, db)
    con.register(db, name, d)

    queue = os.getenv('QUEUE_SYSTEM')
    if queue == "KAFKA" or queue == "KAFKA-P":
        postproduction = Postproduction(args)
        try:
            await postproduction.run()
        except KeyboardInterrupt:
            print("Shutting down due to KeyboardInterrupt")
        except Exception as e:
            print(f"Error in main: {e}")
            print(traceback.format_exc())
        finally:
            postproduction.running = False
            await postproduction.cleanup()
    else:
        print(f"Unsupported queue system: {queue}")

if __name__ == "__main__":
    asyncio.run(main())
