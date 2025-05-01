#!/usr/bin/env python3

import argparse
import json
import jsons

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
from qbmediator.Session import Session
import os

from ButlerAPI import *

import nltk
from nltk import sent_tokenize
nltk.download('punkt')
import requests

from transformers import AutoTokenizer

from qbmediator.Text import get_punctuation
from huggingface_hub import InferenceClient
from transformers import AutoTokenizer
from dotenv import dotenv_values
import time


punctuation = get_punctuation()

property_to_default = {
    "display_language": "en",
    "output_languages": "[]",
    "out_lang": "[]",
    "finished_sentences": "-1",
#    "method": "streaming_simple",
    "method": "online_model",
    "StablePrefix": "[]",
    "senderID": "{}",
    "waitingMessages": "[]",
    "languages": "[]",
    "leadingStream": "-1",
    "model_parameters": {
        "llm": {
            "endpoint": "http://192.168.0.66:8000/v1/completions",
            "mt_server": "http://192.168.0.68:5455/predictions/",
            "model_name": "meta-llama/Meta-Llama-3-8B-Instruct",
        },
    },
}




class KITMeetingButler():

    def __init__(self,args):
        self.args=args
        try:
            hf_token = os.environ['hf_token']
            assert hf_token[0:2] == "hf_"
        except:
            while True:
                print("Sleeping because the .env file was not loaded")
                time.sleep(10000)
        self.tokenizer = AutoTokenizer.from_pretrained(property_to_default["model_parameters"]["llm"]["model_name"], use_fast=False, token=hf_token)
        self.url = property_to_default["model_parameters"]["llm"]["endpoint"]
        #self.butler_prompt = "<<SYS>>\nYou are a meeting assistant that generates command based on the user's request to start programmes such as Lecture Translator and Zoom. You reply ONLY with a json where you say should the software be on or off. For example, if the user asks lets start the session, you reply with {'zoom': 'on', 'lecture':'on'}. If they say 'Lets not have the meeting offline only with lecture translator, you reply with {'zoom':'off','lt':'on'}. You always reply with the status of zoom and lt in the json.\n<</SYS>>\n[INST]\n"
        #self.client = InferenceClient(model=property_to_default["model_parameters"]["llm"]["endpoint"])
        


    def processAggregate(self,mList):
        for message in mList:
            print(" [x] Received for segmentation %r " % (message))
            data = json.loads(message)

            session = data["session"]
            stream = data["tag"]
            print("Context:",name,session,stream)
            print("Sender:",data["sender"])
            db.setContext(name,session,stream)
            if ("controll" in data):
                print("Data:",data)
                if(data["controll"] == "START"):
                    
                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))  # Send START to further components
                    db.setPropertyValue("context","")
                    db.setPropertyValue("processed_text","")
                    db.setPropertyValue("history",json.dumps([]))
                    
                elif (data["controll"] == "END"):
                    self.finish(session,stream);
                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))
                elif (data["controll"] == "INFORMATION"):
                    properties = db.getPropertyValues();
                    data = {'session': session, 'controll': "INFORMATION", 'sender': name + ":" + stream,
                            name + ":" + stream: properties}
                    data["sender"] = name + ":" + stream
                    con.publish("mediator", session, jsons.dumps(data))

            elif ("controll" not in data):


                if ("unstable" in data and data["unstable"] == True):
                    #do nothing
                    print("Ignore unstable")
                else:
                    if "markup" not in data:
                        print("Append data")
                        data["sender"] = name + ":" + stream
                        data = self.process_query(data, session, stream)

                    elif(data["markup"] == "command"):
                        self.run_command(data,session,stream)


    def finish(self,session,stream):
        print("Finish")




    def append_stable(self,data):
        db.setProperyValue("")
        print("append")

    def run_command(self,data,session,stream):
        input = json.loads(data["seq"])
        result = ""
        if len(input["parameter"]) == 0:
            try:
                result = globals()[input["function"]]()
            except:
                print("Could not run function:",input)
        else:            
            try:
                result = globals()[input["function"]](input["parameter"])
            except:
                print("Could not run function:",input)

        if(result == "forward"):
            print("Forward command: ",data["seq"])
            data["sender"] = name + ":" + stream
            con.publish("mediator", session, jsons.dumps(data))

    def process_query(self, data, session, stream):
        """
        Args:
        Context: The full conversation excluding curr_input
        processed_text: The text that has been processed so far and ignored. Currently, we do not maintain dialog state. We keep track of what has been processed and only check the remaining for keyword ask butler
        curr_input: The new segment recieved that we need to process

        Returns:
        answer from the LLM if the sequence is ready to process, if not we do not send it
        """


        curr_input = data["seq"]
        context = db.getPropertyValues("context")
        processed_text = db.getPropertyValues("processed_text")
        history = json.loads(db.getPropertyValues("history"))
        context = context + curr_input


        curr_dialog = context[len(processed_text):]
        curr_dialog = " ".join(curr_dialog.split())
        curr_dialog = curr_dialog.lower()
        print("Dialog {}".format(curr_dialog))

        if "ask butler" in curr_dialog.lower():
            try:
                if len(curr_dialog.split("ask butler")) == 1:
                    return ""
                else:
                    query = curr_dialog.split("ask butler")[1]
            except:
                print("Received Command, waiting for query")
                return ""
            if len(query) > 4:
                if query[-1] == "." or query[-1] == "?":
                    print("Asking query {}".format(query))
                    processing = {'recieved_query':True}
                    #con.publish("mediator", session, jsons.dumps(processing))

                    #answer = self.client.text_generation(prompt=self.butler_prompt +  query + " [/INST]" + "{'zoom':", max_new_tokens=16)
                    answer = self.send_request(query)
                    print("Received Answer")
                    print(answer)
                    data["llm_command"] = answer
                    data["markup"] = "llm_command"
                    con.publish("mediator", session, jsons.dumps(data))
                    processed_text = context 
                    history.append((query,answer))


        db.setPropertyValue("context", context)
        db.setPropertyValue("processed_text", processed_text)
        db.setPropertyValue("history", json.dumps(history))


        return 


    def send_request(self, query, max_tokens=128, temperature=0, history=[]):

        prompt = self.format_prompt(query, history)

        payload = json.dumps({
          "model": property_to_default["model_parameters"]["llm"]["model_name"],
          "prompt": prompt,
          "max_tokens": 128,
          "temperature": 0,
          "stop": [
            "<|eot_id|>"
          ]
        })
        headers = {
          'Content-Type': 'application/json'
        }
        response = requests.request("POST", self.url, headers=headers, data=payload)

        return response.json()["choices"][0]["text"]

    def format_prompt(self, query, history=[]):

       messages = [
               {"role": "system", "content": "You are a chatbot and a command generator for operating softwares in a computer. You only generate a json command based on the user's request. You also answer general questions in command using the natural_text key."},
	    {"role": "user", "content": ",Can we join the online meeting?"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "Sure, lets do zoom", "function":"controll_zoom","parameter":{"command":"join"}}}'},  
	    {"role": "user", "content": "Let's start the lecture translator"},  
            {"role": "assistant", "content": '{"markup": "command","seq": {"natural_text": "Your wish my command", ""function":"controll_lt","parameter":{"command":"start"}}}'},  
	    {"role": "user", "content": "We do not need the translator anymore, close it"},  
            {"role": "assistant", "content": '{"markup": "command","seq": {"natural_text": "Okay, let me know if you want to start again", "function":"controll_lt","parameter":{"command":"end"}}}'},  
	    {"role": "user", "content": "Close the zoom meeting?"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "I will miss the online people", "function":"controll_zoom","parameter":{"command":"end"}}}'},  
	    {"role": "user", "content": "Close the lecture translator now?"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "No problem, I hope everybody still understands" ,"function":"controll_lt","parameter":{"command":"end"}}}'},  
	    {"role": "user", "content": "Show the whiteboard"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "Cool, It is time to draw", "function":"set_display","parameter":{"page":"whiteboard"}}}'},  
	    {"role": "user", "content": "I want to see sai in zoom now"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "Lets start zoom to see sai", "function":"set_display","parameter":{"page":"video_conference"}}}'},  
	    {"role": "user", "content": "Display the lecture translator"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "Your wish my command", "function":"set_display","parameter":{"page":"lecture_translator"}}}'},  
	    {"role": "user", "content": "I want to see the controls page"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "Okay, lets see the controls", "function":"set_display","parameter":{"page":"controll"}}}'},  
	    {"role": "user", "content": "Who are you?"},  
            {"role": "assistant", "content": '{"markup": "command", "seq": {"natural_text": "I am butler, the virtual meeting assistant"}}'},  
        ]

       #for turn in history[-3:]:
       #    messages.append({"role": "user", "content": turn[0]})
       #    messages.append({"role": "assistant", "content": turn[1]})
       
       messages.append({"role": "user", "content": query}),  
       input_ids = self.tokenizer.apply_chat_template(messages, return_tensors="pt")
       prompt = self.tokenizer.decode(input_ids[0], skip_special_tokens=False)
       prompt += "<|start_header_id|>assistant<|end_header_id|>\n"
       return prompt




        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    args = parser.parse_args()

    name = "kitmeetingbutler"

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    d = {"languages": []}
    con = get_best_connector()(args.queue_server,args.queue_port,db)
    con.register(db, name, d)

    queue = os.getenv('QUEUE_SYSTEM')
    if queue == "KAFKA" or queue == "KAFKA-P":
        butler = KITMeetingButler(args)
        con.consume(name,butler,True)
