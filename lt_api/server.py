#!/usr/bin/env python3
import argparse

from flask import Flask, request, jsonify, Response
#from flasgger import Swagger

from flask_cors import CORS

import jsons
import json

from qbmediator.Session import Session
from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database

from qbmediator.Database import get_from_db, set_in_db, get_component_names

import redis
import time
import os

import requests
import threading

import langcodes
import numpy as np

import base64
import numpy as np
from scipy.signal import resample

import subprocess

app = Flask(__name__)
app.config['SWAGGER'] = {
    'openapi': '3.0.0',
    'title': 'LT API',
    'uiversion': 3,
    'specs_route': '/ltapi/apidocs/',
    'specs': [
        {
            'endpoint': 'apispec_1',
            'route': '/ltapi/apispec_1.json',
        }
    ],
}
#swagger = Swagger(app, template_file='api.yaml')
CORS(app)


lang_names = {"ende":"English/German","7eu":"Transcript","mult57":"Transcript","mult8":"Transcript"}


def lang_name(lang):
    if lang.endswith("_audio"):
        return lang_name(lang[:-len("_audio")])+" Audio"
    elif lang.endswith("_video"):
        return lang_name(lang[:-len("_video")])+" Video"
    elif lang.endswith("_memory"):
        return lang_name(lang[:-len("_memory")])+" context biasing"
    elif lang.endswith("_en_diarization"):
        return lang_name(lang[:-len("_en_diarization")])+" Diarization"
    elif lang.endswith(" Error Corrector"):
        return lang_name(lang[:-len(" Error Corrector")]) + " Error Corrector"
    elif lang.endswith(" Error Correction Questions"):
        return lang_name(lang[:-len(" Error Correction Questions")]) + " Error Correction Questions"
    elif lang.endswith(" Automatic Transcript with Errors"):
        return lang_name(lang[:-len(" Automatic Transcript with Errors")]) + " Automatic Transcript with Errors"
    elif lang.endswith(" Reference"):
        return lang_name(lang[:-len(" Reference")]) + " Reference"
    elif lang.endswith(" Confidences"):
        return lang_name(lang[:-len(" Confidences")]) + " Confidences"
    elif lang.endswith(" Summary"):
        return lang_name(lang[:-len(" Summary")])+" Summary"
    elif lang in lang_names:
        return lang_names[lang]
    elif len(lang) <= 3:
        return langcodes.Language.get(lang).display_name()
    else:
        return lang

def get_input_language(l):
    return l.split("-")[0] if "-" in l else l

def get_output_language(l):
    return l.split("-")[1] if "-" in l else l

def langs_to_display_langs(langs):
    return [lang_name(l) for l in langs]

@app.route('/ltapi/save_agreement_to_error_correction_eval', methods=['POST'])
def save_agreement_to_error_correction_eval():
    data = request.get_json()
    user = data['user']
    db.set(f'{user}_agreed_error_correction_eval', 'agreed')
    return 'ok'

@app.route('/ltapi/set_text_as_done', methods=['POST'])
def set_text_as_done():
    data = request.get_json()
    user = data['user']
    task = data['task']

    datasets_to_do = json.loads(db.db.get(f'error_correction_datasets_to_do_for_{user}'))
    if datasets_to_do and datasets_to_do[0] == task:
        del datasets_to_do[0]

    db.db.set(f'error_correction_datasets_to_do_for_{user}', json.dumps(datasets_to_do))

    return 'ok'

@app.route('/ltapi/langs_to_display_langs', methods=['GET'])
def langs_to_display_langs_():
    data = request.get_json()
    if not data or "langs" not in data:
        return "ERROR: No langs found", 400
    langs = data["langs"]
    return langs_to_display_langs(langs)

def is_available(url):
    try:
        res = requests.post(url, verify=False)
        available = True
    except requests.exceptions.ConnectionError:
        available = False
        print("Not available:", url)
    return available

def update_availability_():
    for component_name, d in db.db.hgetall('available_languages').items():
        d = json.loads(d)
        s = d["server"].split("|")[0]
        d["available"] = is_available(s)
        if d["available"]:
            resp = None
            if component_name.decode().startswith("asr"):
                resp = requests.get(s+"/available_languages")
            elif component_name.decode().startswith("mt"):
                resp = requests.get(s+"/models/mt/available_languages")
            else:
                print("ERROR:",component_name)
            if resp and resp.status_code == 200:
                d["available_languages"] = resp.json()
            else:
                print(f"Server {d['server']} available but not available languages found")
        print(f"{component_name = } ,{d}")
        db.db.hset("available_languages", component_name, json.dumps(d))
    print("Updating availability successful")

def update_availability(minutes=10):
    while True:
        update_availability_()
        time.sleep(minutes*60)

def stop_zombiesessions_(data, remove_after_hours):
    types = ['meeting', 'kitallmeeting', 'kitemployeemeeting', 'lecture', 'kitalllecture', 'kitemployeelecture']
    sessions = []
    for type in types:
        sessions += load_sessionids(type)

    for sid in sessions:
        if sid not in data:
            data[sid] = time.time()
        elif time.time() > data[sid] + remove_after_hours*60*60:
            data_ = {'controll': "END"}
            append_(str(sid),"0",data_)
            print(f"Send {data} to end session with id {sid}")

    data = {k:v for k,v in data.items() if k in sessions}
    print(f"Open sessions: {data}, sessions are removed after {remove_after_hours} hours")

    return data

def stop_zombiesessions(evaluate_every_hours=1,remove_after_hours=24):
    data = {}
    while True:
        data = stop_zombiesessions_(data, remove_after_hours)
        time.sleep(evaluate_every_hours*60*60)

def register_name(component, name, server):
    data = {"server":server}
    db.db.hset("available_languages", f"{component}_{name}", json.dumps(data))
    if component in ["asr","mt"]:
        update_availability_()

# Register workers:
# 1) docker compose exec streamingasr bash
# 2) register asr: curl -H "Content-Type: application/json" http://ltapi:5000/ltapi/register_worker -d '{"component": "asr", "name": "mult57", "server": "http://192.168.0.60:5008/asr"}'
#               or curl -H "Content-Type: application/json" http://ltapi:5000/ltapi/register_worker -d '{"component": "asr", "name": "mult12", "server": "http://192.168.0.60:5055/asr"}'
# 3) register mt: curl -H "Content-Type: application/json" http://ltapi:5000/ltapi/register_worker -d '{"component": "mt", "name": "mult16", "server": "http://192.168.0.64:5051"}'
#              or curl -H "Content-Type: application/json" http://ltapi:5000/ltapi/register_worker -d '{"component": "mt", "name": "mult7", "server": "http://192.168.0.60:5455"}'

@app.route('/ltapi/register_worker', methods=['POST'])
def register():
    data = request.get_json()
    component, name, server = data["component"], data["name"], data["server"]

    if component not in ["asr","mt"]:
        return f"Currently only available for component asr or mt, but got component {component}", 500

    register_name(component, name, server)
    return f"{component = } {name = } {server = } registered\n"

@app.route('/ltapi/unregister_worker', methods=['POST'])
def unregister():
    data = request.get_json()
    component, name = data["component"], data["name"]
    db.db.hdel("available_languages", f"{component}_{name}")
    return f"{component = } {name = } unregistered\n"

@app.route('/ltapi/list_available_languages', methods=['GET', 'POST'])
def list_available_languages():
    res = list_available_languages_()
    print(res)
    return res, 200

def list_available_languages_(return_set=False):
    res = {}

    component_names = get_component_names(db.db)
    for name in component_names:
        if name == "asr":
            languages = []
            for component_name, d in db.db.hgetall('available_languages').items():
                if not component_name.decode().startswith("asr"):
                    continue
                d = json.loads(d)
                if "available" in d and d["available"]:
                    if "available_languages" in d:
                        languages += d["available_languages"]
        elif name == "mt":
            languages = []
            for component_name, d in db.db.hgetall('available_languages').items():
                if not component_name.decode().startswith("mt"):
                    continue
                d = json.loads(d)
                if "available" in d and d["available"]:
                    if "available_languages" in d:
                        for l_in in d["available_languages"][0]:
                            for l_out in d["available_languages"][1]:
                                languages.append(f"{l_in}-{l_out}")
        else:
            component_info = get_from_db(db.db, name, "All", "component_info")
            if component_info is None:
                continue
            component_info = json.loads(component_info)
            
            if "languages" in component_info:
                languages = component_info["languages"]

        languages2 = [] if not return_set else set()
        for l in languages:
            if not return_set:
                if name=="asr" and "-" in l:
                    input_language = get_input_language(l)
                elif name=="mt" and "-" in l:
                    input_language = get_output_language(l)
                else:
                    input_language = l
                name_lang = lang_name(input_language)
                key = (input_language,name_lang)
                if not key in languages2:
                    languages2.append(key)
            else:
                languages2.add(l)
        res[name] = sorted(languages2, key=lambda t:t[1])

    return res

def load_sessionids(type):
    if not db.db.exists("List" + type):
        return []
    else:
        sessions = json.loads(db.db.get("List" + type).decode("utf-8"))
        return sessions
    
@app.route('/ltapi/list_sessions/<type>', methods=['GET', 'POST'])
def list_sessions(type):
    sessions = load_sessionids(type)
    result = []
    for sid in sessions:
        s = jsons.load(json.loads(db.db.get("Session" + str(sid)).decode("utf-8")), Session)
        host = ""
        if hasattr(s, 'host'):
            host = s.host
        if(s.name != ""):
            result.append((s.name,sid,host))
        else:
            result.append((sid,sid,host))
    return jsons.dumps(result), 200

@app.route('/ltapi/session_name/<sid>', methods=['GET', 'POST'])
def session_name(sid):
        s = jsons.load(json.loads(db.db.get("Session" + str(sid)).decode("utf-8")), Session)
        if(s.name != ""):
            return s.name, 200
        else:
            return sid, 200

@app.route('/ltapi/session_host/<sid>', methods=['GET', 'POST'])
def session_host(sid):
        s = jsons.load(json.loads(db.db.get("Session" + str(sid)).decode("utf-8")), Session)
        if hasattr(s,"host"):
            return s.host, 200
        else:
            return "", 200
@app.route('/ltapi/session_type/<sid>', methods=['GET', 'POST'])
def session_type(sid):
        s = jsons.load(json.loads(db.db.get("Session" + str(sid)).decode("utf-8")), Session)
        if hasattr(s,"type"):
            return s.type, 200
        else:
            return "", 200


@app.route('/ltapi/check_password/<sid>', methods=['GET', 'POST'])
def check_password(sid):
    data = json.loads(request.json)
    password = ""
    name = ""
    if "password" in data:
        password = data["password"]
    if "name" in data:
        name = data["name"]
    
    s = jsons.load(json.loads(db.db.get("Session" + str(sid)).decode("utf-8")), Session)
    print("Session:",s)
    if hasattr(s,"password"):
        if hasattr(s,"name"):
            if password == s.password and name == s.name:
                return "OK", 200
            else:
                print("Names:",name,s.name,"Passwords:",password,s.password)
                return "No permission",200
        else:
            "No name given", 200
    else:
        return "No password given", 200


@app.route('/ltapi/get_default_asr', methods=['POST'])
def get_default_asr():
    print("Requested default asr session")
    data = json.loads(request.json)

    if not "session" in data:
        session = request_session()
    else:
        session = data["session"]
    if not "streamID" in data:
        streamID = request_stream(session)
    else:
        streamID = data["streamID"]

    db.db.set("SessionCreationArguments"+str(session),json.dumps(data).encode("utf-8"));

    g = create_graph(session, streamID, data)
    set_graph(session, g)

    return session+" "+streamID, 200



@app.route('/ltapi/start_dialog', methods=['POST'])
def start_dialog():
    print("Requested start dialog")
    data = json.loads(request.json)

    if not "bot" in data:
        bot = "kitmeetingbutler"
    else:
        bot = data["bot"]

    if not "bot_stream" in data:
        bot_stream = "1"
    else:
        bot_stream = data["bot_stream"]

    
    if not "session" in data:
        session = request_session()
    else:
        session = data["session"]

    if not "streamID" in data:
        streamID = request_stream(session)
    else:
        streamID = data["streamID"]

    streamIDText = request_stream(session)
    streamIDCommand = request_stream(session)
    db.db.set("SessionCreationArguments"+str(session),json.dumps(data).encode("utf-8"));

    g = {}
    
    user_voice = "user:"+streamID
    user_text = "user:"+streamIDText
    user_command = "user:"+streamIDCommand
    g[user_voice] = ["asr:0","log:v1"]
    g[user_text] = [bot+":"+bot_stream,"log:v1"]
    g[user_command] = [bot+":0","log:v1"]

    g["asr:0"] = [bot+":"+bot_stream,"log:v1","api"]

    g[bot+":"+bot_stream] = ["api","log:v1"]
    g[bot+":0"] = ["api","log:v1"]


    if "tts" in data:
        g[bot+":"+bot_stream].append("tts:0")
        g["tts:0"] = ["api"]
    if "video" in data:
        g["tts:0"] = ["lip:0"]
        g["lip:0"] = ["api"]
            
    properties = {"asr:0":{"language":"mult57","mode":"SendStable","stability_detection":"False"},
                 }
    if "bot_properties" in data:
        properties[bot+":"+bot_stream] = data["bot_properties"]
    
    set_graph(session, g)
    set_properties(session, properties)
    
    db.setPropertyValue(f"asr_server_mult57", "http://192.168.0.60:5008/asr/infer/None,None", context=["asr",session,"0"])

    if "tts" in data:
        db.setPropertyValue("language", "en", context=["tts",session,"0"])
        db.setPropertyValue("display_language", "en_audio", context=["tts",session,"0"])
        db.setPropertyValue("mode", "SendBot", context=["tts",session,"0"])
        db.setPropertyValue("version", "offline", context=["tts",session,"0"])


    return session+" "+streamID+" "+" "+streamIDText+" "+streamIDCommand, 200

def input_languages_to_available_languages(data):
    language = data["language"]
    available_languages = list_available_languages_(return_set=True)
    if type(language) == str:
        language = language.split("+")

    all_lang = "+".join(language)

    override_asr_server = None
    append_lang = None
    if "use_memory" in data and data["use_memory"]:
        port = 4999
        if data["profile_id"] == "profile_10":
            port = 4998
            #data["user"] = None
        hpc_server = 60
        override_asr_server = f"http://192.168.0.{hpc_server}:{port}/asr/infer/{all_lang},{all_lang}"
        append_lang = "_memory"

    asr_info = []
    for component_name, d in db.db.hgetall('available_languages').items():
        d = json.loads(d)
        if not component_name.decode().startswith("asr"):
            continue
        if "available" not in d or not d["available"]:
            continue
        if not "available_languages" in d:
            continue
        al = d["available_languages"]

        if all(l in al for l in language):
            lang = component_name.decode().split("_")[1] if len(language) > 1 else language[0]
            if append_lang is not None:
                lang += append_lang
            if override_asr_server:
                server = override_asr_server
            else:
                server = "|".join(s+f"/infer/{all_lang},{all_lang}" for s in d["server"].split("|"))
            asr_info.append((lang,server,len(al)))

    available_languages["asr"] = [min(asr_info, key=lambda x: x[2])]

    mt_info = []
    if "mt" in data and data["mt"]:
        for component_name, d in db.db.hgetall('available_languages').items():
            d = json.loads(d)
            if not component_name.decode().startswith("mt"):
                continue
            if "available" not in d or not d["available"]:
                continue
            if not "available_languages" in d:
                continue
            al = d["available_languages"]

            #if all(l in al[0] for l in language):
            if True: # also start mt workers where the asr output language can not be handeled by the mt worker -> this case is handeled in streamingmt
                mt_langs = json.loads(data["mt"])
                mt_all = mt_langs == "ALL"

                for l in al[1] if mt_all else mt_langs:
                    if l in al[1]:
                        l = get_output_language(l)
                        mt_info.append((f"{all_lang}-{l}",d["server"]+f"/predictions/mt-{all_lang},{l}"))

    available_languages["mt"] = mt_info

    return available_languages

def get_mt_server(out_lang, in_lang="en"):
    mt_info = []
    for component_name, d in db.db.hgetall('available_languages').items():
        d = json.loads(d)
        if not component_name.decode().startswith("mt"):
            continue
        if "available" not in d or not d["available"]:
            continue
        if not "available_languages" in d:
            continue
        al = d["available_languages"]

        if in_lang in al[0] and out_lang in al[1]:
            return d["server"]+f"/predictions/mt-{in_lang},{out_lang}"    

def create_graph(session, streamID, data):
    if "prep" not in data:
        data["prep"] = False
    if "language" not in data:
        data["language"] = "en"
    if "textseg" not in data:
        data["textseg"] = True
    if "summarize" not in data:
        data["summarize"] = False
    if "error_correction" not in data:
        data["error_correction"] = 'None'
    if "error_correction_init" not in data:
        data["error_correction_init"] = None
    if "postproduction" not in data:
        data["postproduction"] = []
    if "user" not in data:
        data["user"] = None
    if "profile_id" not in data:
        data["profile_id"] = "profile_1"
    if data ["summarize"]:
        data["textseg"] = True
    if "compute_hyperarticulation" not in data:
        data["compute_hyperarticulation"] = False
    if "aiassistant" not in data:
        data["aiassistant"] = False

    available_languages = input_languages_to_available_languages(data)

    if "asr_prop" in data:
        for k in data["asr_prop"]:
            if k.startswith("asr_server_"):
                available_languages["asr"].append(k[len("asr_server_"):])
    if "mt_prop" in data:
        for k in data["mt_prop"]:
            if k.startswith("mt_server_"):
                available_languages["mt"].append(k[len("mt_server_"):])

    g = {}
    if not data["prep"]:
        start = "user:"+streamID
    else:
        g["user:"+streamID] = ["prep:0"]
        start = "prep:0"
    output_language_to_key = []
    g[start] = []

    if "speaker_diarization" in data and data["speaker_diarization"]:
        g["user:"+streamID].append("textstructurer:"+streamID)

    db.setPropertyValue("output_languages",jsons.dumps([]),context=["textstructurer",session,streamID])
    #print("Set value:","textstructurer",session,streamID," to:",db.getPropertyValues("output_languages",context=["textstructurer",session,streamID]))

    i = 0
    for l in available_languages["asr"]:
        if type(l) == tuple:
            l, server, _ = l
        else:
            server = None
        in_lang = get_input_language(l)
        out_lang = get_output_language(l)

        if server is None and data["language"] != in_lang:
            continue

        print("Using asr", l)

        g[start].append("asr:"+str(i))
        # g[start].append("log:v1")
        if data["textseg"]:
            g["asr:"+str(i)] = ["textstructurer:"+streamID,"api"]
            if data["summarize"]:
                g["textstructurer:"+streamID+"_"+out_lang] = ["api", "summarizer:"+streamID]
                g["asr:"+str(i)].append("summarizer:"+streamID)
                g["summarizer:"+streamID+"_"+out_lang] = ["api"]

                db.setPropertyValue("display_language", out_lang, context=["summarizer",session,streamID+"_"+out_lang])
                db.setPropertyValue(f"mt_server_{out_lang}",get_mt_server(out_lang),context=["summarizer",session,streamID])
            else:
                g["textstructurer:"+streamID+"_"+out_lang] = ["api"]
            ol = json.loads(db.getPropertyValues("output_languages",context=["textstructurer",session,streamID]))
            ol.append(out_lang)
            db.setPropertyValue("output_languages",jsons.dumps(ol),context=["textstructurer",session,streamID])
            db.setPropertyValue(f"mt_server_{out_lang}",get_mt_server(out_lang),context=["textstructurer",session,streamID])
            print(f"Set mt_server_{out_lang} to {get_mt_server(out_lang)}")
            db.setPropertyValue("language", l, context=["textstructurer",session,streamID+"_"+out_lang])
            db.setPropertyValue("display_language", out_lang, context=["textstructurer",session,streamID+"_"+out_lang])
            if "textseg_prop" in data:
                if "method" in data["textseg_prop"]:
                    print("Set method of textstructure:",data["textseg_prop"]["method"])
                    db.setPropertyValue("method",data["textseg_prop"]["method"],context=["textstructurer",session,streamID])
                if "dynamic_threshold" in data["textseg_prop"]:
                    print("Set dynamic_threshold of textstructure:",data["textseg_prop"]["dynamic_threshold"])
                    db.setPropertyValue("dynamic_threshold",int(data["textseg_prop"]["dynamic_threshold"]),context=["textstructurer",session,streamID])
                
        else:
            g["asr:"+str(i)] = ["api"]
        for percentage in data["postproduction"]:
            id_ = f"postproduction:{percentage}_{out_lang}"
            tag = f"{percentage}_{out_lang}"
            context = ["postproduction", session, tag]

            g["asr:"+str(i)].append(id_)
            g[id_] = ["api"]

            ol = db.getPropertyValues("output_languages", context=["postproduction", session, str(percentage)])
            ol = [] if ol is None else json.loads(ol)
            ol.append(out_lang)

            db.setPropertyValue("output_languages", jsons.dumps(ol), context=["postproduction", session, str(percentage)])
            db.setPropertyValue("display_language", out_lang, context=context)
            db.setPropertyValue("compression_rate", percentage, context=context)

            db.setPropertyValue(f"mt_server_{out_lang}", get_mt_server(out_lang), context=["postproduction", session, str(percentage)])

        db.setPropertyValue("language", l, context=["asr",session,str(i)])
        if server is not None:
            db.setPropertyValue(f"asr_server_{l}", server, context=["asr",session,str(i)])
        db.setPropertyValue("display_language", out_lang, context=["asr",session,str(i)])

        if data["error_correction"] != 'None':
            confidences_needed = data["error_correction"] in ("confidences", "dialog", "dialog2")
            if confidences_needed:
                g["asr:" + str(i)].append("asrConfidences:asrConfidences" + str(i))
                g["asr:" + str(i)].append("correction:asrErrorCorrection" + str(i))
                db.setPropertyValue("forward_audio", "True", context=["asr", session, str(i)])
                db.setPropertyValue("output_exact_word_boundaries", "True", context=["asr", session, str(i)])
                if data["compute_hyperarticulation"]:
                    db.setPropertyValue("compute_hyperarticulation", "True", context=["asr", session, str(i)])
                g["asrConfidences:asrConfidences" + str(i)] = ["api"]
                db.setPropertyValue("method", "streaming_confidences", context=[
                                    "asrConfidences", session, "asrConfidences" + str(i)])

                db.setPropertyValue("language", l, context=["correction", session, "asrConfidences:asrConfidences" + str(i)])
                db.setPropertyValue("display_language", out_lang + " Confidences",
                                    context=["asrConfidences", session, "asrConfidences" + str(i)])

                g["asrConfidences:asrConfidences" + str(i)].append("correction:asrErrorCorrection" + str(i))

                if data["error_correction"] == "dialog":
                    db.setPropertyValue("method", "error_correction_with_confidences", context=[
                        "correction", session, "asrErrorCorrection" + str(i)])
                else:
                    db.setPropertyValue("method", "error_correction_with_confidences_and_buttons", context=[
                        "correction", session, "asrErrorCorrection" + str(i)])
            else:
                g["asr:" + str(i)].append("correction:asrErrorCorrection" + str(i))

                db.setPropertyValue("method", "error_correction", context=[
                                    "correction", session, "asrErrorCorrection" + str(i)])

            if data["error_correction"] in ("dialog", "dialog2"):
                g["correction:asrErrorCorrection" + str(i)] = ["api"]
                g["correction:asrErrorCorrection" + str(i)].append("tts:0")
                g["tts:0"] = ["api"]
                g["log:v1"] = ["tts:0"]
                db.setPropertyValue("language", l, context=["tts", session, "0"])
                db.setPropertyValue("display_language", out_lang + "_audio",
                                    context=["tts", session, "0"])

                db.setPropertyValue("generate_silence", "False", context=["tts", session, "0"])
                db.setPropertyValue("version", "online_with_playback", context=["tts", session, "0"])
                # db.setPropertyValue("language", "de", context=["tts", session, "0"])

                db.setPropertyValue("language", l, context=["correction", session, "asrErrorCorrection" + str(i)])
                db.setPropertyValue("display_language", out_lang + " Error Correction",
                                    context=["correction", session, "asrErrorCorrection" + str(i)])
                db.setPropertyValue("llm_output_only_changed_part", "True", context=["correction", session, "asrErrorCorrection" + str(i)])

                db.setPropertyValue("user", data["user"], context=["correction", session, "asrErrorCorrection" + str(i)])
                db.setPropertyValue("languages", ' '.join(data["language"]), context=["correction", session, "asrErrorCorrection" + str(i)])

                g["correction:asrErrorCorrection" + str(i)] += ["correctionTranscript:ErrorCorrectionTranscriptViewer" + str(i)]

                if data["error_correction_init"]:
                    g["correction:asrErrorCorrection" + str(i)].append("correctionGroundTruth:ErrorCorrectionGroundTruthViewer" + str(i))

                    db.setPropertyValue("init_text", data["error_correction_init"], context=["correction", session, "asrErrorCorrection" + str(i)])
                    db.setPropertyValue("correction_mode_deactivated", "True", context=["correction", session, "asrErrorCorrection" + str(i)])

                g["correctionGroundTruth:ErrorCorrectionGroundTruthViewer" + str(i)] = ["api"]

                db.setPropertyValue("method", "error_correction_transcript_viewer", context=[
                    "correctionTranscript", session, "ErrorCorrectionTranscriptViewer" + str(i)])
                db.setPropertyValue("method", "error_correction_ground_thruth_viewer", context=[
                    "correctionGroundTruth", session, "ErrorCorrectionGroundTruthViewer" + str(i)])

                db.setPropertyValue("language", l, context=["correctionGroundTruth", session,
                                    "ErrorCorrectionGroundTruthViewer" + str(i)])
                db.setPropertyValue("display_language", out_lang + " Reference",
                                    context=["correctionGroundTruth", session, "ErrorCorrectionGroundTruthViewer" + str(i)])

            if data["textseg"]:
                if data["error_correction"] in ("dialog", "dialog2"):
                    g["correctionTranscript:ErrorCorrectionTranscriptViewer" + str(i)] = ["api"]

                    db.setPropertyValue("language", l, context=["correctionTranscript",
                                        session, "ErrorCorrectionTranscriptViewer" + str(i)])
                    db.setPropertyValue("display_language", out_lang + " Automatic Transcript with Errors",
                                        context=["correctionTranscript", session, "ErrorCorrectionTranscriptViewer" + str(i)])
                else:
                    g["textseg:asrErrorCorrection" + str(i)] = ["textseg:ErrorCorrection" + str(i)]
                    g["textseg:ErrorCorrection" + str(i)] = ["api"]

                    db.setPropertyValue("language", l, context=["textseg",
                                        session, "textseg:ErrorCorrection" + str(i)])
                    db.setPropertyValue("display_language", out_lang + " Error Correction",
                                        context=["textseg", session, "ErrorCorrection" + str(i)])
            else:
                if data["error_correction"] in ("dialog", "dialog2"):
                    g["correctionTranscript:ErrorCorrectionTranscriptViewer" + str(i)] = ["api"]

                    db.setPropertyValue("language", l, context=["correctionTranscript",
                                                                session, "ErrorCorrectionTranscriptViewer" + str(i)])
                    db.setPropertyValue("display_language", out_lang + " Automatic Transcript with Errors",
                                        context=["correctionTranscript", session, "ErrorCorrectionTranscriptViewer" + str(i)])
                else:
                    g["correction:asrErrorCorrection" + str(i)] = ["api"]

                    db.setPropertyValue("language", l, context=["correction", session, "asrErrorCorrection" + str(i)])
                    db.setPropertyValue("display_language", out_lang + " Error Correction",
                                        context=["correction", session, "asrErrorCorrection" + str(i)])

        output_language_to_key.append((out_lang,"asr:"+str(i)))
        i += 1

    if len(g[start]) == 0:
        return "Given ASR language does not exist", 400

    if "mt" in data and data["mt"]:
        mt_langs = available_languages["mt"]

        i = 0
        while True:
            worked = False
            for out_lang, key in output_language_to_key:
                for mt_lang in mt_langs:
                    if type(mt_lang) == tuple:
                        mt_lang, server = mt_lang
                    else:
                        server = None

                    mt_input_lang = get_input_language(mt_lang)
                    if server is None and mt_input_lang != out_lang:
                        continue
                    mt_out_lang = get_output_language(mt_lang)
                    if any(mt_out_lang==out_lang2 for out_lang2, _ in output_language_to_key):
                        continue
                    print("Using mt",mt_lang,server)
                    g[key].append("mt:"+str(i))
                    if data["textseg"]:
                        g["mt:"+str(i)] = ["textstructurer:"+streamID,"api"]
                        if data["summarize"]:
                            g["textstructurer:"+streamID+"_"+mt_out_lang] = ["api", "summarizer:"+streamID]
                            g["mt:"+str(i)].append("summarizer:"+streamID)
                            g["summarizer:"+streamID+"_"+mt_out_lang] = ["api"]

                            db.setPropertyValue("display_language", mt_out_lang, context=["summarizer",session,streamID+"_"+mt_out_lang])
                            db.setPropertyValue(f"mt_server_{mt_out_lang}",get_mt_server(mt_out_lang),context=["summarizer",session,streamID])
                        else:
                            g["textstructurer:"+streamID+"_"+mt_out_lang] = ["api"]
                        ol = json.loads(db.getPropertyValues("output_languages",context=["textstructurer",session,streamID]))
                        ol.append(mt_out_lang)
                        db.setPropertyValue("output_languages",jsons.dumps(ol),context=["textstructurer",session,streamID])
                        db.setPropertyValue(f"mt_server_{mt_out_lang}",get_mt_server(mt_out_lang),context=["textstructurer",session,streamID])

                        db.setPropertyValue("language", mt_lang, context=["textstructurer",session,streamID+"_"+mt_out_lang])
                        db.setPropertyValue("display_language", mt_out_lang, context=["textstructurer",session,streamID+"_"+mt_out_lang])

                    else:
                        g["mt:"+str(i)] = ["api"]

                    for percentage in data["postproduction"]:
                        id_ = f"postproduction:{percentage}_{mt_out_lang}"
                        tag = f"{percentage}_{mt_out_lang}"
                        context = ["postproduction", session, tag]

                        g[f"mt:{i}"].append(id_)
                        g[id_] = ["api"]

                        ol = db.getPropertyValues("output_languages",context=["postproduction", session, str(percentage)])
                        ol = [] if ol is None else json.loads(ol)
                        ol.append(mt_out_lang)


                        db.setPropertyValue("output_languages",jsons.dumps(ol), context=["postproduction", session, str(percentage)])
                        db.setPropertyValue("language", mt_lang, context=context)
                        db.setPropertyValue("display_language", mt_out_lang, context=context)
                        db.setPropertyValue("compression_rate", percentage, context=context)
                        db.setPropertyValue(f"mt_server_{mt_out_lang}", get_mt_server(mt_out_lang), context=["postproduction", session, str(percentage)])

                    if data["aiassistant"]:
                        if(mt_out_lang == "en"):
                            g["mt:"+str(i)].append("bot:0")

                        
                    db.setPropertyValue("language", mt_lang, context=["mt",session,str(i)])
                    if server is not None:
                        db.setPropertyValue(f"mt_server_{mt_lang}", server, context=["mt",session,str(i)])
                    db.setPropertyValue("display_language", mt_out_lang, context=["mt",session,str(i)])

                    output_language_to_key.append((mt_out_lang,"mt:"+str(i)))
                    i += 1
                    worked = True
                    break
                if worked:
                    break
            if not worked:
                break

    tts_output_language_to_key = []
    if "tts" in data and data["tts"]:
        tts_all = data["tts"] == "ALL"
        if tts_all:
            tts_langs = available_languages["tts"] if "tts" in available_languages else set()
        else:
            tts_languages = data["tts"].split(",")

        i = 0
        for out_lang, key in output_language_to_key:
            if not tts_all and out_lang not in tts_languages:
                continue

            g["user:"+streamID].append("tts:"+str(i))
            g[key].append("tts:"+str(i))
            
            # if out_lang == "th":
            #     g[key].append("tts:"+str(i))
            # else:
            #     g[key].append("textseg:tts"+str(i))
            #     g["textseg:tts"+str(i)] = ["tts:"+str(i)]
            # 
            g["tts:"+str(i)] = ["api"]
            
            db.setPropertyValue("method", "streaming_tts", context=["textseg",session, "tts"+str(i)])
            db.setPropertyValue("language", out_lang, context=["tts",session,str(i)])
            db.setPropertyValue("display_language", out_lang+"_audio", context=["tts",session,str(i)])

            if not "mt_prop" in data or not "mode" in data["mt_prop"] or data["mt_prop"]["mode"] == 'SendUnstable':
                db.setPropertyValue("mode", "SendRevision", context=["tts",session,str(i)])
            elif data["mt_prop"]["mode"] == 'SendPartial':
                db.setPropertyValue("mode", "SendPartial", context=["tts",session,str(i)])
            else:
                db.setPropertyValue("mode", "SendBot", context=["tts",session,str(i)])

            tts_output_language_to_key.append((out_lang,"tts:"+str(i)))
            i += 1

        if "de" in output_language_to_key and (tts_all or "schwabish" in tts_languages):
            out_lang = "schwabish"
            key = output_language_to_key["de"]
            # print("Using tts",key)

            g["user:"+streamID].append("tts:"+str(i))
            # g[key].append("textseg:tts"+str(i))
            g[key].append("tts:"+str(i))
            # g["textseg:tts"+str(i)] = ["tts:"+str(i)]
            g["tts:"+str(i)] = ["api"]
            
            db.setPropertyValue("method", "streaming_tts", context=["textseg",session, "tts"+str(i)])
            db.setPropertyValue("language", out_lang, context=["tts",session,str(i)])
            db.setPropertyValue("display_language", out_lang+"_audio", context=["tts",session,str(i)])

            if not "mt_prop" in data or not "mode" in data["mt_prop"] or data["mt_prop"]["mode"] == 'SendUnstable':
                db.setPropertyValue("mode", "SendRevision", context=["tts",session,str(i)])
            elif data["mt_prop"]["mode"] == 'SendPartial':
                db.setPropertyValue("mode", "SendPartial", context=["tts",session,str(i)])
            else:
                db.setPropertyValue("mode", "SendBot", context=["tts",session,str(i)])

            tts_output_language_to_key.append((out_lang,"tts:"+str(i)))
            i += 1

        video = "video" in data and data["video"]
        if video:
            video_all = data["video"] == "ALL"
            if not video_all:
                video_languages = data["video"].split(",")

            i = 0
            for out_lang, key in tts_output_language_to_key:
                if not video_all and out_lang not in video_languages:
                    continue
                print("Using video",key)

                g["user:"+streamID].append("lip:"+str(i))
                g[key] = ["lip:"+str(i)]
                g["lip:"+str(i)] = ["api"]

                db.setPropertyValue("language", out_lang, context=["lip",session,str(i)])
                db.setPropertyValue("display_language", out_lang+"_video", context=["lip",session,str(i)])

    if "speakerdiarization" in data and data["speakerdiarization"]:
        out = g["asr:0"]
        g["asr:0"] = ["asr:speakerdiarization"]
        g["asr:speakerdiarization"] = out
        
        for i, (out_lang, key) in enumerate(tts_output_language_to_key):
            g["asr:speakerdiarization"].append(key)
            g["asr:0"].append(key)
            # g["user:"+streamID].remove("tts:"+str(i))
            db.setPropertyValue("voice_profile", "True", context=["tts",session,str(i)])
            
        db.setPropertyValue("speakerdiarization_following", "True", context=["asr",session,"0"])
        db.setPropertyValue("saasr", "1", context=["asr",session,"speakerdiarization"])
        language = db.getPropertyValues("language", context=["asr",session,"0"])
        db.setPropertyValue("language", language, context=["asr",session,"speakerdiarization"])
        display_language = db.getPropertyValues("display_language", context=["asr",session,"0"])
        db.setPropertyValue("display_language", display_language, context=["asr",session,"speakerdiarization"])

        user = data["user"]
        profile_id = data["profile_id"]

        # retrieve user spk-cluster
        try:
            saved_cluster_data = json.loads(db.db.get(f'{user}_{profile_id}/spk-cluster').decode('utf-8'))
        except:
            saved_cluster_data = {}
            db.db.set(f'{user}_{profile_id}/spk-cluster', json.dumps(saved_cluster_data))
            
        print(f"Retrieved cluster data of user {user}:", saved_cluster_data)
        
        result_session_update = str(db.db.set(f'asr/{session}/speakerdiarization/spk-cluster', json.dumps(saved_cluster_data)))
        
        print(f"Session update result with key asr/{session}/speakerdiarization/spk-cluster:", result_session_update)

    if "use_memory" in data and data["use_memory"]:
        out = g["asr:0"]
        g["asr:0"] = ["asr:numbers"]
        g["asr:numbers"] = out

        if "speakerdiarization" in data and data["speakerdiarization"]:
            db.setPropertyValue("speakerdiarization_following", "True", context=["asr",session,"numbers"])
        db.setPropertyValue("numbers_reformatting", "True", context=["asr",session,"numbers"])
        language = db.getPropertyValues("language", context=["asr",session,"0"])
        db.setPropertyValue("language", language, context=["asr",session,"numbers"])
        server = f"http://192.168.0.72:4998/predictions/asr-numbers"
        db.setPropertyValue(f"asr_server_{language}", server, context=["asr",session,"numbers"])
        display_language = db.getPropertyValues("display_language", context=["asr",session,"0"])
        db.setPropertyValue("display_language", display_language, context=["asr",session,"numbers"])

        compare = False

        if compare:
            g["user:0"].append("asr:1")
            language = "mult57"
            db.setPropertyValue("language", language, context=["asr",session,"1"])
            db.setPropertyValue("display_language", language, context=["asr",session,"1"])
            server = "http://192.168.0.60:5008/asr/infer/en+de,en+de"
            db.setPropertyValue(f"asr_server_{language}", server, context=["asr",session,"1"])
            g["asr:1"] = ["textstructurer:1","api"]
            g[f"textstructurer:1_{language}"] = ["api"]
            db.setPropertyValue("output_languages",jsons.dumps([language]),context=["textstructurer",session,"1"])
            db.setPropertyValue("language", language, context=["textstructurer",session,"1_"+language])

    if "log" not in data or data["log"]=="True":
        for k,v in g.items():
            g[k].append("log:v1")


    if "num_speakers" in data and "prep" in data:
        num_speakers = data["num_speakers"]

        if num_speakers > 0 and data["prep"]:
            nodes_reachable = set()
            nodes_to_explore = set(["prep:0"])

            while len(nodes_to_explore) > 0:
                node = nodes_to_explore.pop()
                nodes_reachable.add(node)
                if node not in g:
                    continue

                for node2 in g[node]:
                    if node2 not in nodes_reachable:
                        nodes_to_explore.add(node2)

            g2 = {}
            for i in range(1,num_speakers):
                for node in nodes_reachable:
                    if node in ["log:v1", "api"]:
                        continue

                    if node != "prep:0":
                        node_new = node+"_speaker"+str(i)
                    else:
                        node_new = node

                    if node not in g:
                        continue
                    
                    for child in g[node]:
                        if child in ["log:v1","api"]:
                            child_new = child
                        else:
                            child_new = child+"_speaker"+str(i)

                        if node_new in g2:
                            g2[node_new].append(child_new)
                        else:
                            g2[node_new] = [child_new]

                    comp, tag = node.split(":")
                    res = db.getPropertyValues(context=[comp,session,tag])

                    comp, tag = node_new.split(":")

                    for k,v in res.items():
                        db.setPropertyValue(k, v, context=[comp,session,tag])

            for k,v in g2.items():
                if k in g:
                    g[k] += v
                else:
                    g[k] = v

    prop = {}
    for k,v in data.items():
        if k.endswith("_prop"):
            for n in g:
                if n.startswith(k[:-len("_prop")]):
                    prop[n] = v

    for n,d in prop.items():
        comp, tag = n.split(":")
        for k,v in d.items():
            # print("k", k)
            # print("v", v)
            db.setPropertyValue(k, str(v), context=[comp,session,tag])

    return g

@app.route('/ltapi/get_demo_asr', methods=['POST'])
def get_demo_asr():
    print("Requested demo asr session")
    data = json.loads(request.json)

    session = request_session()
    streamID = request_stream(session)

    g = {}
    g["user:"+streamID] = ["asr:0","asr:1","asr:2"]
    g["asr:0"] = ["textseg:asr0", "mt:0"]
    g["asr:1"] = ["textseg:asr1", "mt:1"]
    g["asr:2"] = ["textseg:asr2","mt:2"]

    properties = {"asr:0":{"language":"en","mode":"SendStable","stability_detection":"False","display_language":"English Offline Mode"},
                  "asr:1":{"language":"en","mode":"SendStable","stability_detection":"True","display_language":"English Fixed Mode"},
                  "asr:2":{"language":"en","mode":"SendUnstable","stability_detection":"True","display_language":"English Revision Mode"},
                  "mt:0":{"language":"en-de","mode":"SendStable","display_language":"German Offline Mode"},
                  "mt:1":{"language":"en-de","mode":"SendPrefix","display_language":"German Fixed Mode"},
                  "mt:2":{"language":"en-de","mode":"SendUnstable","display_language":"German Revision Mode"},
                 }

    for i in range(3):
        g["textseg:asr"+str(i)] = ["api"]
        properties["textseg:asr"+str(i)] = {"display_language":properties["asr:"+str(i)]["display_language"]}
    for i in range(3):
        g["mt:"+str(i)] = ["textseg:mt"+str(i)]
        g["textseg:mt"+str(i)] = ["api"]
        properties["textseg:mt"+str(i)] = {"display_language":properties["mt:"+str(i)]["display_language"]}

    set_graph(session, g)
    set_properties(session, properties)

    return session+" "+streamID, 200

@app.route('/ltapi/get_default_mt', methods=['POST'])
def get_default_mt():
    print("Requested default mt session")
    data = json.loads(request.json)

    session = request_session()
    streamID = request_stream(session)

    g = {}
    g["user:"+streamID] = ["mt:0"]
    g["mt:0"] = ["api"]

    if "language" not in data:
        data["language"] = "en-de"

    if type(data["language"]) is str:
        set_properties(session, {"mt:0":{"language":data["language"]}})

        # TODO: check if mt language is available

    if "log" not in data or data["log"]=="True":
        for k,v in g.items():
            g[k].append("log:v1")

    set_graph(session, g)

    return session+" "+streamID, 200


@app.route('/ltapi/<session>/<stream>/append', methods=['POST'])
def append(session,stream):
    data = json.loads(request.json);
    return append_(session,stream,data)

def append_(session,stream,data):
    data["session"] = session
    data["sender"] = "user:"+stream
    if "controll" in data and data["controll"] == "START":
        db.db.hset('active_sessions', session, json.dumps(data))
        graph = getgraph_(session)
        db.db.hset('active_session_components', session, len(graph))
    if "controll" in data and data["controll"] == "END":
        db.db.hdel('active_sessions', session)
    body = json.dumps(data)
    print("Send data to mediator")
    #if(len(request.json) < 10000):
    #    print(request.json)
    con.publish("mediator", session+"/"+stream, body)
    return "OK"

@app.route('/ltapi/get_active_sessions', methods=['GET'])
def get_active_sessions():
    sessions = db.db.hkeys('active_sessions')    
    data = []
    for s in sessions:
        data.append(db.db.hget('active_sessions', s).decode("utf-8"))
    return data

def set_properties(session, properties, external=False):
    for component,property_dict in properties.items():
        component = component.split(":")
        print("Set properties for ",component)
        if len(component) == 2:
            component, tag = component
        elif len(component) == 1:
            component = component[0]
            tag = "All"
        else:
            print("ERROR in set properties")
            continue
        for prop, value in property_dict.items():
            db.setPropertyValue(prop, value, context=[component,session,tag])
            if external:
                db.addPropertyKey(prop, context=[component,session,tag])

@app.route('/ltapi/<session>/setproperties', methods=['POST'])
def setproperties(session):
    data = json.loads(request.json);
    print("Got properties for session",session,data)

    set_properties(session, data, external=True)

    return "OK"

@app.route('/ltapi/<session>/<stream>/get_output_language_component', methods=['POST'])
def get_output_language_component(session,stream):
    print("Get output language")
    print("Request:",request)
    print("Json:",request.json)
    data = json.loads(request.json);
    comp = data["component"]
    print("Component:",comp)
    print("Stream",stream)
    lang = db.getPropertyValues("display_language", context=[comp.split(":")[0],session,stream])
    if "-" in lang:
        lang = lang.split("-")[1]
    print("Language of "+comp+": "+lang)
    return lang_name(lang)

@app.route('/ltapi/<session>/get_previous_messages', methods=['POST'])
def get_previous_messages(session):
    print("Get previous messages")
    print("Request:",request)
    print("Json:",request.json)
    data = json.loads(request.json);
    list = []
    if "component" in data:
        comp = data["component"]
        begin = data["begin"]
        if("end" in data):
            end = data["end"]
        else:
            end = -1
        print("Resquest to redis: ","MessageList" + session + ":" + comp,begin,end)
        list = db.db.lrange("MessageList" + session + ":" + comp,begin,end)
        list = [l.decode("utf-8") for l in list]
    elif ("language" in data):
        
        s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
        graph = s.get_graph()
        for c in graph:
            stream = c.split(":")
            print("Stream:",stream)
            if(len(stream) == 2):
                print("Display language:",db.getPropertyValues("display_language", context=[stream[0],session,stream[1]]))
            if(len(stream) == 2 and "api" in graph[c]):
                lang = db.getPropertyValues("display_language", context=[stream[0],session,stream[1]])
                if(lang in lang_names):
                    lang = lang_names[lang]
                if(lang == data["language"]):
                    print("Add component:",c)
                    addlist = db.db.lrange("MessageList" + session + ":" + c, 0, -1)
                    list += [l.decode("utf-8") for l in addlist]

    print(list)
    return json.dumps(list)



def set_graph(session, graph):
    p = db.db.pipeline()
    p.watch("Session"+session)
    p.multi();
    string = db.db.get("Session"+session).decode("utf-8");
    s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
    print("Use graph:", graph)
    s.set_graph(graph)
    p.set("Session"+session,json.dumps((jsons.dump(s))).encode("utf-8"));
    p.execute();

@app.route('/ltapi/<session>/setgraph', methods=['POST'])
def setgraph(session):
    print("Got graph for session",session)
    data = json.loads(request.json);

    set_graph(session, data)
    return "OK"

@app.route('/ltapi/<session>/getgraph', methods=['GET', 'POST'])
def getgraph(session):
    graph = getgraph_(session)
    return json.dumps(graph), 200

def getgraph_(session):
    string = db.db.get("Session"+session).decode("utf-8")
    s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
    graph = s.get_graph()
    return graph

def request_stream(session, log=False):
    p = db.db.pipeline()
    p.watch("Session"+session)
    p.multi()
    string = db.db.get("Session"+session).decode("utf-8")
    s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
    streamID = s.add_stream()
    print("Trying to add stream", streamID, "to session", session)
    p.set("Session"+session,json.dumps((jsons.dump(s))).encode("utf-8"))
    p.execute()

    if log:
        graph = getgraph_(session)
        key = "user:"+str(streamID)
        if not key in graph:
            graph[key] = ["log:v1"]
        elif not "log:v1" in graph[key]:
            graph[key].append("log:v1")
        set_graph(session, graph)

    return str(streamID)

@app.route('/ltapi/<session>/addstream', methods=['GET', 'POST'])
def addstream(session):
    return addstream_(session)

@app.route('/ltapi/<session>/addstreamlog', methods=['GET', 'POST'])
def addstreamlog(session):
    return addstream_(session, log=True)

def addstream_(session, log=False):
    print("Starting stream for session",session)
    stream_id = None
    try:
        stream_id = request_stream(session, log=log)
    except redis.exceptions.WatchError:
        print("Redo adding stream", stream_id," to session ", session)
        return addstream_(session, log=log)
    else:
        print("Done adding stream", stream_id, " to session ", session)
        return stream_id

@app.route('/ltapi/<session>/addBotStream', methods=['GET', 'POST'])
def addBotstream(session):
    stream_id = addstream(session)

    addBot(session, stream_id)
    return stream_id

@app.route('/ltapi/<session>/addSelectionToolStream', methods=['GET', 'POST'])
def addSelectionToolStream(session):
    stream_id = addstream(session)

    connection_list = addAsrErrorCorrectionToBotConnection(session) + ['log:v1']
    addTool(session, stream_id, 'SelectionTool', connection_list=connection_list)
    return stream_id

@app.route('/ltapi/<session>/addErrorCorrectionToolsStream', methods=['GET', 'POST'])
def addErrorCorrectionToolsStream(session):
    stream_id = addstream(session)

    connection_list = addAsrErrorCorrectionToBotConnection(session)
    addTool(session, stream_id, 'ErrorCorrectionTools', connection_list=connection_list)
    return stream_id

def addAsrErrorCorrectionToBotConnection(session):
    connection_list = []
    s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")), Session)
    graph = s.get_graph()
    for key in graph:
        if key.startswith('correction:asrErrorCorrection'):
            connection_list.append(key)

    return connection_list

def addTool(session, streamID, botName="bot", connection_list=[]):
    try:
        p = db.db.pipeline()
        p.watch("Session"+session)
        p.multi();
        string = db.db.get("Session"+session).decode("utf-8");
        s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
        graph=s.get_graph()

        if connection_list:
            graph["user:"+streamID] = connection_list
            graph["asr:0"].append(f"{botName}:{0}")
        else:
            graph["user:"+streamID] = [f"{botName}:{streamID}"]
            graph["asr:0"].append(f"{botName}:{0}")
            graph[f"{botName}:{streamID}"] = ["api"]

        print("Use graph:", graph)
        s.set_graph(graph) #
        #print("Trying to add steam", streamID, "to session", session)
        p.set("Session"+session,json.dumps((jsons.dump(s))).encode("utf-8"));
        p.execute();
    except redis.exceptions.WatchError:
        print("Redo adding bot", streamID, "to session", session)
        addTool(session, streamID, botName=botName, connection_list=connection_list)



@app.route('/ltapi/createBotSession', methods=['POST'])
def createBotSession():
    in_data = json.loads(request.json);

    session = request_session()

    controll_stream = request_stream(session)

    db.db.set("SessionCreationArguments"+str(session),json.dumps(in_data).encode("utf-8"));

    g = {}
    g["user:"+str(controll_stream)] = ["bot:0","log:v1"]
    
    
    

    stream_id = addstream(session)

    g["user:"+str(stream_id)] = ["bot:"+stream_id,"log:v1"]
    g["bot:"+stream_id] = ["api","log:v1"]

    properties = {};
    set_graph(session, g)
    set_properties(session, properties)

    
    data = {'controll': "START",'content_directory': in_data["content_dir"]}
    
    append_(session,controll_stream,data)

    result = {"sessionID":session,"streamID":stream_id}
    return jsons.dumps(result), 200

        

def addBot(session, streamID, botName="bot"):
    try:
        p = db.db.pipeline()
        p.watch("Session"+session)
        p.multi();
        string = db.db.get("Session"+session).decode("utf-8");
        s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
        graph=s.get_graph()

        graph["user:"+streamID] = [f"{botName}:{streamID}","log:v1"]
        graph[f"{botName}:{streamID}"] = ["api","log:v1"]

        print("Use graph:", graph)
        s.set_graph(graph) #
        #print("Trying to add steam", streamID, "to session", session)
        p.set("Session"+session,json.dumps((jsons.dump(s))).encode("utf-8"));
        p.execute();
    except redis.exceptions.WatchError:
        print("Redo adding bot", streamID, "to session", session)
        addTool(session, streamID, botName=botName, connection_list=connection_list)


@app.route('/ltapi/addSpeaker', methods=['POST'])
def addSpeaker():
    data = json.loads(request.json);
    session = data["session"]
    #lang = data["language"]
    #num_speakers = data["num_speakers"]
    #audio_samples = data["audio_samples"]

    streamID = addstream(session)

    data = json.loads(db.db.get("SessionCreationArguments"+str(session)))
    print("Session start arguments",data)
    #data["language"] = lang
    #data["num_speakers"] = num_speakers
    #data["prep_prop"]["sample_names"] = audio_samples

    session_new = request_session()

    string = db.db.get("Session"+session).decode("utf-8")
    s = jsons.load(json.loads(db.db.get("Session"+session).decode("utf-8")),Session)
    g = s.get_graph()


    addSpeakerGraph(session,g,streamID)

    
    return streamID


def getOutputLanguages(graph,session,stream):
    candidates = ["user:"+str(stream)]
    worker= []
    for k in graph:
        if(k in candidates):
            #everybody getting messages from user:stream is a candidate
            candidates += graph[k]
        if(k in candidates and "api" in graph[k]):
            #candidate sending to api is a worker
            worker.append(k)

    langs=[]
    for k in worker:
        k = k.split(":")
        l = db.getPropertyValues("display_language", context=[k[0],session,k[1]])
        print("Lange:" + l)
        langs.append(l)
    print("Langauges:",langs)
    return langs


def addSpeakerGraph(session,g,streamID):
    try:
        p = db.db.pipeline()
        p.watch("Session"+session)
        p.multi();


        node_new = "user:"+streamID

        children = g["user:0"]
        g[node_new] = []
        for c in children:
            if(c.startswith("asr")):
                new_asr = c
                while(new_asr in g):
                    comp,tag = new_asr.split(":")
                    new_asr =  comp+":"+str(int(tag)+1)
                    
                g[node_new].append(new_asr)
                g[new_asr] = g[c]
                comp, tag = c.split(":")
                res = db.getPropertyValues(context=[comp,session,tag])

                comp, tag = new_asr.split(":")
                
                for k,v in res.items():
                    db.setPropertyValue(k, v, context=[comp,session,tag])
                    
            else:
                g[node_new].append(c)
        
        set_graph(session, g)
        
    except redis.exceptions.WatchError:
        print("Redo combining speaker graphs to session", session)
        addSpeakerGraph(session,session_new,g,g_new)

def request_session():
    print("Starting session")
    streamID = db.db.incr("sessionIDs")
    s = Session(streamID)
    db.db.set("Session" + str(streamID), json.dumps((jsons.dump(s))).encode("utf-8"))
    print("Got stream id:", streamID)
    return str(streamID)


@app.route('/ltapi/startsession', methods=['GET', 'POST'])
def startsession():
    return request_session()

@app.route('/ltapi/upload_video', methods=['POST'])
def upload_video():
    data = json.loads(request.json)

    name = data["video_name"]
    video = data["video"]

    db.db.hset('facedubbing_videos', name, video)

    con = get_best_connector()(queue_server,queue_port,db)
    con.publish("lip", "key", json.dumps({"session": "-1", "tag": "-1", "controll": "new_video"}))

    return "Uploaded video successfully."

@app.route('/ltapi/list_videos', methods=['GET'])
def list_videos():
    videos = [v.decode("utf-8") for v in db.db.hkeys('facedubbing_videos')]
    print(videos)
    return videos

@app.route('/ltapi/asr_inference_doehring', methods=['GET', 'POST'])
def asr_inference_doehring():
    audio = request.get_json()
    audio = np.asarray(audio, dtype=np.float32)
    audio = (32767 * audio).astype(np.int16).tobytes()

    res = requests.post("http://192.168.0.60:5009/asr/infer/None,None", files={"pcm_s16le":audio})
    if res.status_code != 200:
        print(res.text)
        return "ERROR"
    return res.json()["hypo"]

def resample_audio(input_bytes, input_sample_rate=24000, output_sample_rate=16000):
    # Convert byte string to numpy array of audio samples
    audio_data = np.frombuffer(input_bytes, dtype=np.int16)

    # Calculate the new length of the audio after resampling
    new_length = int(len(audio_data) * (output_sample_rate / input_sample_rate))

    # Resample the audio data
    resampled_audio = resample(audio_data, new_length)

    # Convert resampled audio data back to byte string
    output_bytes = resampled_audio.astype(np.int16).tobytes()

    return output_bytes

@app.route('/ltapi/tts_inference_doehring', methods=['GET', 'POST'])
def tts_inference_doehring():
    text = request.json.get("text")
    #res = requests.post("http://192.168.0.68:5057/tts/infer/de_DE", files={"seq": text, "len_scale": 1})
    #return res.json()
    res = requests.post("http://192.168.0.64:5053/tts/infer/e2tts", files={"text": text, "speed": "1", "iteration_steps": "10"})
    if res.status_code != 200:
        print(res.text)
        return "ERROR"
    audio = base64.b64decode(res.json()["audio"])
    audio = base64.b64encode(resample_audio(audio)).decode()
    return {"audio":audio}

@app.route('/ltapi/asr_label_doehring', methods=['GET', 'POST'])
def asr_label_doehring():
    data = request.get_json()

    audio = np.asarray(data["audio"], dtype=np.float32)
    pcm_s16le = (32767 * audio).astype(np.int16).tobytes()

    transcript = data["transcript"]

    out_dir = "/doehring" #"/project/OML/chuber/2024/data/Doehring_corrections"
    id = time.time()
    out_file = f"{out_dir}/{id}.mp3"
    cmd = f"ffmpeg -f s16le -ar 16000 -ac 1 -i pipe:0 {out_file}"

    subprocess.run(cmd.split(), input=pcm_s16le)
    with open(f"{out_dir}/labels.cased","a") as f:
        f.write(f"{id} {transcript}\n")

    return "Success"

@app.route('/ltapi/do_urlshortening', methods=['GET', 'POST'])
def do_urlshortening():
    data = request.get_json()  # Retrieve JSON data from the request

    url = data["url"]
    short = data["short"]
    if "/" in short:
        return "ERROR: Shortening is not allowed to contain '/'"

    db.db.hset("shortenings",short,url)

    return f"Success: The url /ltapi/shorten/{short} on this server points now to {url}"

@app.route('/ltapi/shorten/<short>', methods=['GET', 'POST'])
def shorten(short):
    url = db.db.hget("shortenings",short)
    if not url:
        return "ERROR: Shortened route not found!"
    if request.args.get("returnurl") == "1":
        return url
    return requests.get(url.decode("utf-8")).text

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='lt-api bridge')
    #model argument
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    parser.add_argument('--property_provider_url', type=str, default='http://192.168.0.72:5000')
    args = parser.parse_args()

    print("Initialize the LT-API Brdige...")

    db = get_best_database()(args.redis_server,args.redis_port)
    db.setDefaultProperties({"display_language":"Unknown"})
    print("Connected to server")

    queue_server=args.queue_server
    queue_port=args.queue_port
    property_provider_url=args.property_provider_url

    con = get_best_connector()(queue_server,queue_port,db)

    thread = threading.Thread(target=update_availability)
    thread.start()

    app.run(debug=True, host="0.0.0.0", use_reloader=True)
else:
    if "REDIS_SERVER" in os.environ:
        redis_server = os.environ["REDIS_SERVER"]
    else:
        redis_server="localhost"
    if "REDIS_PORT" in os.environ:
        redis_port = os.environ["REDIS_PORT"]
    else:
        redis_port="6379"
    if "QUEUE_SERVER" in os.environ:
        queue_server = os.environ["QUEUE_SERVER"]
    else:
        queue_server="localhost"
    if "QUEUE_PORT" in os.environ:
        queue_port = os.environ["QUEUE_PORT"]
    else:
        queue_port="5672"

    print("Initialize the LT-API Brdige...")
    print("Connect to redis server "+redis_server+":"+redis_port)
    db = get_best_database()(redis_server,redis_port)
    db.setDefaultProperties({"display_language":"Unknown"})
    print("Connected to server")

    thread = threading.Thread(target=update_availability)
    thread.start()

    thread = threading.Thread(target=stop_zombiesessions)
    thread.start()

    con = get_best_connector()(queue_server,queue_port,db)
