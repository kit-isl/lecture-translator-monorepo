import os

from flask import Flask, render_template, request, Response, redirect, url_for, jsonify
import requests
import json
import os
import threading
import datetime
import time
import sys
import pytz

import redis
from sseclient import SSEClient
import urllib
import base64
import subprocess
import tempfile
import re

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import traceback

server = os.getenv("API")
server_stream = os.getenv("API_STREAM")
api_external_host = os.getenv("API_EXTERNAL_HOST", "/ltapi")
archiveAPI = os.getenv("ARCHIVE")
theme = os.getenv("THEME")
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", "6379"))
use_auth = bool(int(os.getenv("USE_AUTH", 1)))
dex_api_addr = os.getenv("DEX_API_ADDR", "dex:5557")
presentation_mode = os.getenv("PRESENTATION_MODE", "markup")
no_database_mode = bool(int(os.getenv("NO_DATABASE", 0)))
domain = os.getenv("DOMAIN")
LOCK_TIMEOUT = 30  # Lock timeout in seconds

def create_app(test_config=None):
    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)

    try:
        from flasgger import Swagger, swag_from

        app.config["SWAGGER"] = {
            "openapi": "3.0.2",
            "title": "My API",
            "uiversion": 3,
        }
        swagger = Swagger(app)
    except ImportError:
        pass

    db = redis.Redis(host=redis_host, port=redis_port, password="")
    db.ping()

    def getAvailability():
        result = [
            ("private", "Private"),
            ("kitemployee", "KIT-Employee"),
            ("kitall", "KIT"),
            ("public", "Public"),
        ]
        return result

    def getUserGroups(user):
        if not use_auth:
            return ["all", "kitall", "kitemployee", "presenter", "admin", "collector"]
        if user == None:
            return ["all"]
        else:
            groups = ["all", "kitall", user]
            if db.sismember("groups:presenter", user):
                groups += ["presenter"]
            if db.sismember("groups:admin", user):
                groups += ["admin"]
            if user.endswith("@kit.edu"):
                groups += ["kitemployee"]
            if db.sismember("groups:collector", user):
                groups += ["collector"]
            if user == "admin@example.com":
                groups += ["admin"]
            return groups

    def get_other_users(user):
        other_users = []
        for user_, username in db.hgetall("user_to_name").items():
            user_ = user_.decode("utf-8")
            username = username.decode("utf-8")
            if user_ != user:
                other_users.append((user_, username))
        print("other_users", other_users)
        return other_users

    @app.route("/")
    @app.route("/index/<directory>/")
    def index(directory="home"):
        # print(server + "/ltapi/list_available_languages")
        resp = requests.post(
            server + "/ltapi/list_available_languages", verify=False, timeout=10
        )
        asr_langs = (
            resp.json()["asr"]
            if resp.status_code == 200 and "asr" in resp.json()
            else []
        )
        # print(asr_langs)
        mt_langs = (
            resp.json()["mt"] if resp.status_code == 200 and "mt" in resp.json() else []
        )
        # print(mt_langs)
        tts_langs = (
            resp.json()["tts"]
            if resp.status_code == 200 and "tts" in resp.json()
            else []
        )
        # print(tts_langs)

        video_langs = (
            tts_langs
            if resp.status_code == 200
            and "lip" in resp.json()
            and len(resp.json()["lip"]) > 0
            else []
        )

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        user_name = user
        if user != None:
            user_name_ = db.hget("user_to_name", user)
            if user_name_ is not None:
                user_name = user_name_.decode("utf-8")

            audio_sample = db.hget("user_to_audiosample", user)
            if audio_sample is not None:
                audio_sample = audio_sample.decode("utf-8")
        else:
            audio_sample = None

        print("User:",user, groups, flush=True)

        other_users = get_other_users(user)

        resp = requests.get(server + "/ltapi/list_videos", verify=False)
        if resp.status_code == 200:
            videos = resp.json()
        else:
            print(resp.status_code)
            print(resp.text)
            videos = []

        buttons = []
        menu = []
        menu.append(("Live event", url_for("index", directory="live")))
        if "presenter" in groups:
            menu.append(("Archive", url_for("index", directory="archive")))
        else:
            menu.append(("Archive", url_for("archive", dir="%2F")))
        if user == None:
            menu.append(("Login", url_for("login")))
        else:
            menu.append(("Settings", url_for("index", directory="settings")))
            menu.append(("Logout", url_for("logout")))

        print("Directory:", directory)
        if directory == "home":
            icon = url_for("static", filename="img/live.png")
            buttons.append(
                ("Live event", icon, url_for("index", directory="live"), "", "", "")
            )
            if "presenter" in groups:
                icon = url_for("static", filename="img/archive.png")
                buttons.append(
                    ("Archive", icon, url_for("index", directory="archive"), "", "", "")
                )
            else:
                icon = url_for("static", filename="img/archive.png")
                buttons.append(
                    ("Archive", icon, url_for("archive", dir="%2F"), "", "", "")
                )
            if user != None:
                icon = url_for("static", filename="img/settings.png")
                buttons.append(
                    (
                        "Settings",
                        icon,
                        url_for("index", directory="settings"),
                        "",
                        "",
                        "",
                    )
                )
            if user == None:
                icon = url_for("static", filename="img/login.png")
                buttons.append(("Login", icon, url_for("login"), "", "", ""))
            else:
                icon = url_for("static", filename="img/logout.png")
                buttons.append(("Logout", icon, url_for("logout"), "", "", ""))

        elif directory == "archive":
            icon = url_for("static", filename="img/home.png")
            buttons.append(
                ("Home", icon, url_for("index", directory="home"), "", "", "")
            )
            icon = url_for("static", filename="img/archive.png")
            buttons.append(
                ("Public Archive", icon, url_for("archive", dir="%2F"), "", "", "")
            )
            if user is not None:
                icon = url_for("static", filename="img/archive.png")
                buttons.append(
                    (
                        "Private Archive",
                        icon,
                        url_for(
                            "archive", dir="%2Fhome%2F" + urllib.parse.quote_plus(user)
                        ),
                        "",
                        "",
                        "",
                    )
                )

        elif directory == "live":
            icon = url_for("static", filename="img/home.png")
            buttons.append(
                ("Home", icon, url_for("index", directory="home"), "", "", "")
            )
            icon = "https://cdn-icons-png.flaticon.com/512/43/43912.png?w=1060&t=st=1682525110~exp=1682525710~hmac=ecc283cb6cf71d93ee527c766838cd6e1d5656e0c1bdd7b4b66d2b23a65d1883"
            buttons.append(
                ("Join Event", icon, url_for("overview", type="meeting"), "", "", "")
            )
            if "presenter" in groups:
                icon = "https://cdn-icons-png.flaticon.com/512/86/86076.png?w=1060&t=st=1683277610~exp=1683278210~hmac=89e9335d7b76a6ed0650c796e9e08059bee37f22e101c8e543380f8387e36fc3"
                buttons.append(
                    (
                        "Start Event",
                        icon,
                        "",
                        "start-lecture",
                        "lecture-session-settings.html",
                        "lectureSession",
                    )
                )
            icon = "https://cdn-icons-png.flaticon.com/512/993/993787.png?w=1060&t=st=1682524352~exp=1682524952~hmac=8d67c0c65c2bccc63eaf2845e626c6d3abb23f58d66e68a788127dc30a79b20c"
            # buttons.append(("Join Seminar",icon,url_for("overview",type="meeting"),"","",""))
            # if("presenter" in groups):
            #    # icon="https://cdn-icons-png.flaticon.com/512/243/243278.png?w=1060&t=st=1682531666~exp=1682532266~hmac=0044e6020a7bcd40c8dd0cf028b2768a4bea310c4547818e7e4624c061a2db9c"
            #    icon="https://cdn-icons-png.flaticon.com/512/3050/3050431.png"
            #    buttons.append(("Start Seminar",icon,"","start-seminar","seminar-session-settings.html","seminarSession"))
            # icon="https://cdn-icons-png.flaticon.com/512/993/993787.png?w=1060&t=st=1682524352~exp=1682524952~hmac=8d67c0c65c2bccc63eaf2845e626c6d3abb23f58d66e68a788127dc30a79b20c"
            if "admin" in groups:
                buttons.append(
                    (
                        "Controll Room",
                        icon,
                        url_for("overview", type="meeting_room"),
                        "",
                        "",
                        "",
                    )
                )
            if "presenter" in groups:
                # icon="https://cdn-icons-png.flaticon.com/512/3050/3050431.png"
                # buttons.append(("Start Meeting (Beta)",icon,url_for("meeting"),"","",""))
                # buttons.append(("Start Seminar",icon,"","start-seminar","seminar-session-settings.html","seminarSession"))
                # buttons.append(("Start Meeting (Beta)",icon,"","start-meeting","saasr-session-settings.html","saasrSession"))
                # buttons.append(("Smart Chaptering Demo","https://cdn-icons-png.flaticon.com/512/6348/6348087.png ","","start-smart","smart-session-settings.html","smartSession"))
                pass

        elif directory == "settings":
            icon = url_for("static", filename="img/home.png")
            buttons.append(
                ("Home", icon, url_for("index", directory="home"), "", "", "")
            )
            if "admin" in groups:
                icon = "https://cdn-icons-png.flaticon.com/512/86/86076.png?w=1060&t=st=1683277610~exp=1683278210~hmac=89e9335d7b76a6ed0650c796e9e08059bee37f22e101c8e543380f8387e36fc3"
                buttons.append(
                    (
                        "Upload Video",
                        icon,
                        "",
                        "upload-video",
                        "upload-settings.html",
                        "uploadinfo",
                    )
                )
                icon = "https://cdn-icons-png.flaticon.com/512/86/86076.png?w=1060&t=st=1683277610~exp=1683278210~hmac=89e9335d7b76a6ed0650c796e9e08059bee37f22e101c8e543380f8387e36fc3"
                buttons.append(
                    (
                        "Record Video",
                        icon,
                        "",
                        "record-video",
                        "record-settings.html",
                        "recordinfo",
                    )
                )
                icon = "https://cdn-icons-png.flaticon.com/512/86/86076.png?w=1060&t=st=1683277610~exp=1683278210~hmac=89e9335d7b76a6ed0650c796e9e08059bee37f22e101c8e543380f8387e36fc3"
                buttons.append(
                    (
                        "Add Lecturer",
                        icon,
                        "",
                        "add-lecturer",
                        "add-lecturer.html",
                        "addlecturerinfo",
                    )
                )
                icon = "https://cdn-icons-png.flaticon.com/512/86/86076.png?w=1060&t=st=1683277610~exp=1683278210~hmac=89e9335d7b76a6ed0650c796e9e08059bee37f22e101c8e543380f8387e36fc3"
                buttons.append(
                    (
                        "Stop All Sessions",
                        icon,
                        "",
                        "stop-sessions",
                        "stop-sessions.html",
                        "stopsessionsinfo",
                    )
                )
            if user != None:
                icon = url_for("static", filename="img/user.png")
                buttons.append(
                    (
                        "User details",
                        icon,
                        "",
                        "user-settings",
                        "user-settings.html",
                        "userinfo",
                    )
                )

        profiles = [[f"profile_{i}", f"Profile {i}"] for i in range(1, 11)]
        profile_names = db.hget("user_to_profile_names", f"{user}")
        if profile_names:
            for name, profile in zip(profile_names.decode().split("|"), profiles):
                profile[1] = name

        return render_template(
            "index.html",
            layout=buttons,
            menu=menu,
            asr_langs=asr_langs,
            mt_langs=mt_langs,
            tts_langs=tts_langs,
            video_langs=video_langs,
            user=user,
            user_name=user_name,
            other_users=other_users,
            audio_sample=audio_sample,
            groups=groups,
            profiles=profiles,
            theme=theme,
            availability=getAvailability(),
            videos=videos,
            presentation_mode=presentation_mode,
        )

    @app.route("/login")
    def login():
        return index()

    @app.route("/logout")
    def logout():
        return index()

    from rouge_metric import PyRouge
    import heapq
    from nltk import sent_tokenize

    fmt_text = lambda text: text.replace("\n", " ").strip(" ")

    def join_captions(captions):
        return " ".join([fmt_text(c["text"]) for c in captions])

    def check_captions(captions, sentence):
        current_caption_idx = 1
        current_caption = join_captions(captions[0 : current_caption_idx + 1])

        for _ in range(len(captions)):
            if sentence == current_caption:
                return (True, current_caption_idx, current_caption)
            elif current_caption.startswith(sentence):
                return (False, current_caption_idx, current_caption)
            current_caption_idx += 1
            current_caption = join_captions(captions[0 : current_caption_idx + 1])

    def get_annotated_sentences(captions, sentences):
        annotated_sentences = []

        for sentence in sentences:

            caption = captions[0]
            caption_text = fmt_text(caption["text"])
            start = caption["start"]
            end = caption["end"]
            duration = end - start

            if sentence == caption_text:
                annotated_sentences.append(
                    {
                        "text": sentence,
                        "start": start,
                        "end": end,
                    }
                )
                captions.pop(0)
            elif sentence.startswith(caption_text):
                is_exact_match, caption_idx, current_caption = check_captions(
                    captions, sentence
                )
                if is_exact_match:
                    annotated_sentences.append(
                        {
                            "text": sentence,
                            "start": start,
                            "end": captions[caption_idx]["end"],
                        }
                    )
                    captions = captions[caption_idx + 1 :]
                else:
                    current_caption_start = captions[caption_idx]["start"]
                    current_caption_duration = (
                        captions[caption_idx]["end"] - captions[caption_idx]["start"]
                    )
                    current_caption_text = current_caption[len(sentence) :]
                    duration_ratio = 1 - len(current_caption_text) / len(
                        captions[caption_idx]["text"]
                    )

                    interpolated_sentence_end = (
                        current_caption_start
                        + current_caption_duration * duration_ratio
                    )
                    annotated_sentences.append(
                        {
                            "text": sentence,
                            "start": start,
                            "end": interpolated_sentence_end,
                        }
                    )
                    captions = captions[caption_idx:]

                    captions[0]["text"] = current_caption[len(sentence) :].lstrip(" ")
                    captions[0]["start"] = interpolated_sentence_end
            elif caption_text.startswith(sentence):
                interpolated_sentence_end = start + duration * len(sentence) / len(
                    caption_text
                )

                annotated_sentences.append(
                    {
                        "text": sentence,
                        "start": start,
                        "end": interpolated_sentence_end,
                    }
                )

                caption["text"] = caption_text[len(sentence) :].lstrip(" ")
                caption["start"] = interpolated_sentence_end
            else:
                break
        return captions, annotated_sentences

    def calculate_rouge_l(summary, sentence):
        rouge = PyRouge(rouge_n=(1, 2, 4), rouge_l=True)
        scores = rouge.evaluate([sentence], [[summary]])
        return scores["rouge-l"]["f"]

    def get_scores(summary, segment_sentences):
        scores = [
            calculate_rouge_l(summary, sentence) for sentence in segment_sentences
        ]
        scores = [score if score >= 0.1 else 0 for score in scores]
        top_indices = heapq.nlargest(3, range(len(scores)), key=scores.__getitem__)
        min_index, max_index = min(top_indices), max(top_indices)

        # Set the scores within the range to 1 and the rest to 0
        scores = [1 if min_index <= i <= max_index else 0 for i in range(len(scores))]
        return scores

    @app.route("/get_summary_links", methods=["POST"])
    def get_summary_links():
        data = request.json
        summary_text = data["summary_text"]
        segment_captions = data["segment_texts"]
        segment_captions = [
            {
                "text": fmt_text(caption["text"]),
                "start": caption["start"],
                "end": caption["end"],
            }
            for caption in segment_captions
            if caption["text"].strip() != ""
        ]

        text = join_captions(segment_captions)
        sentences = sent_tokenize(text)
        _, annotated_sentences = get_annotated_sentences(segment_captions, sentences)

        scores = get_scores(summary_text, sentences)
        links = []  # start, end, score
        for score, sentences in zip(scores, annotated_sentences):
            links.append(
                {"start": sentences["start"], "end": sentences["end"], "score": score}
            )
        return {"links": links}

    import wikipedia

    @app.route("/get_wiki_suggestion", methods=["POST"])
    def get_wiki_suggestion():
        data = request.json
        term = data["term"]
        lang = data["lang"]

        suggestions = wikipedia.search(term)

        wikipage = None
        if len(suggestions) > 0:
            article = term if term in suggestions else suggestions[0]
            try:
                wikipage = wikipedia.page(article, auto_suggest=False)
            except wikipedia.DisambiguationError as e:
                for i, article in enumerate(e.options, 1):
                    try:
                        wikipage = wikipedia.page(article, auto_suggest=False)
                        break
                    except (
                        wikipedia.DisambiguationError,
                        wikipedia.exceptions.PageError,
                    ):
                        pass
            except wikipedia.exceptions.PageError:
                wikipage = None

        if wikipage is None:
            return {"link": "", "term": ""}
        else:
            return {"link": wikipage.url, "term": wikipage.title}

    # +++++++++++++++++

    @app.route("/smart_demo", methods=["GET"])
    def smart_demo():
        user = request.headers.get("X-Forwarded-User", None)
        try:
            name = request.form["name"]
            if name == "":
                now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
                name = "Demo " + (now.strftime("%d.%m.%Y %H:%M"))
        except:
            print("No name given")
            now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
            name = "Demo " + (now.strftime("%d.%m.%Y %H:%M"))
        return render_template(
            "smart-session.html",
            correction_mode_deactivated=False,
            user=user,
            show_url=True,
            name=name,
            theme=theme,
            server=api_external_host,
        )

    # +++++++++++++++++

    @app.route("/runtime-spk-setting", methods=["GET", "POST"])
    def runtime_spk_setting():
        return render_template("runtime-speaker-setting.html", theme=theme)

    def find_turn_similar_to_turn(embed_similarity, word_index, words, threshold=0.6):
        def find_similar(embed_similarity, word_index):
            return np.where(embed_similarity[word_index] > threshold)[0].tolist()

        id_similar_to_word_index = find_similar(embed_similarity, word_index)
        order_of_word_index = id_similar_to_word_index.index(word_index)
        word_index_to_turn = [word_index]
        for i in range(order_of_word_index + 1, len(id_similar_to_word_index)):
            if word_index_to_turn[-1] + 1 == id_similar_to_word_index[i]:
                word_index_to_turn.append(id_similar_to_word_index[i])
            else:
                break
        for i in range(order_of_word_index - 1, -1, -1):
            if word_index_to_turn[0] - 1 == id_similar_to_word_index[i]:
                word_index_to_turn = [id_similar_to_word_index[i]] + word_index_to_turn
            else:
                break

        id_similar_to_turn = [
            find_similar(embed_similarity, idx) for idx in word_index_to_turn
        ]
        id_similar_to_turn = list(set([x for xs in id_similar_to_turn for x in xs]))
        id_similar_to_turn = sorted(id_similar_to_turn)
        turns = []
        current_turn = []
        for idx in id_similar_to_turn:
            if len(current_turn) == 0:
                current_turn.append(idx)
            else:
                if current_turn[-1] + 1 == idx and words[current_turn[-1]][-1] not in [
                    ".",
                    "!",
                    "?",
                ]:
                    current_turn.append(idx)
                else:
                    # print("Current turn:", [words[word_idx] for word_idx in current_turn])
                    turns.append(current_turn)
                    current_turn = [idx]
        if len(current_turn) > 0:
            turns.append(current_turn)
        return turns

    @app.route("/get-similar-turn", methods=["POST"])
    def get_similar_turn():
        data = request.json
        session_id = data["session_id"]
        stream_id = data["stream_id"]
        start_word_id = int(data["start_word_id"])
        end_word_id = int(data["end_word_id"])
        target_word_id = int(data["target_word_id"])

        # print("Request for speaker attribute embedding")
        # print("Session ID:", session_id)
        # print("Stream ID:", stream_id)
        # print("word ID list: [{}, {}]".format(start_word_id, end_word_id))

        try:
            word_info = [
                json.loads(
                    db.get(f"asr/{session_id}/{stream_id}/word-{idx}").decode("utf-8")
                )
                for idx in range(start_word_id, end_word_id + 1)
            ]
            words = [item["word"] for item in word_info]
            embed = np.array([item["embed"] for item in word_info])
            embed_similarity = cosine_similarity(embed, embed)

            print("Total words:", len(words))
            print("Embedding shape:", embed.shape)

            turn_similar_id = find_turn_similar_to_turn(
                embed_similarity, target_word_id - start_word_id, words
            )
            turn_similar_string = [
                " ".join([words[idx] for idx in turn]) for turn in turn_similar_id
            ]
            for idx, turn in enumerate(turn_similar_id):
                turn_similar_id[idx] = [idx + start_word_id for idx in turn]
        except:
            traceback.print_exc()
            return []
        return [
            {"word_id": turn_id, "words": turn_string}
            for turn_id, turn_string in zip(turn_similar_id, turn_similar_string)
        ]

    @app.route("/get-spk-cluster", methods=["POST"])
    def get_spk_cluster():
        data = request.json
        session_id = data["session_id"]
        stream_id = data["stream_id"]
        try:
            cluster_data = json.loads(
                db.get(f"asr/{session_id}/{stream_id}/spk-cluster").decode("utf-8")
            )
        except:
            cluster_data = {}
        return cluster_data

    @app.route("/remove-spk-cluster", methods=["POST"])
    def remove_spk_cluster():
        data = request.json
        session_id = data["session_id"]
        stream_id = data["stream_id"]
        profile_id = data["profile_id"]
        cluster_data = {}
        # save to db
        print("Save cluster data", cluster_data)
        result_session_update = str(
            db.set(
                f"asr/{session_id}/{stream_id}/spk-cluster", json.dumps(cluster_data)
            )
        )
        print(
            f"Session update result with key asr/{session_id}/{stream_id}/spk-cluster:",
            result_session_update,
        )

        # save to user database
        user = request.headers.get("X-Forwarded-User", None)
        result_user_update = None
        if user is not None:
            result_user_update = str(
                db.set(f"{user}_{profile_id}/spk-cluster", json.dumps(cluster_data))
            )
        print(
            f"User update result with key {user}_{profile_id}/spk-cluster:",
            result_user_update,
        )

        return {
            "status": result_session_update,
            "user_update_status": result_user_update,
        }

    @app.route("/update-spk-cluster", methods=["POST"])
    def update_spk_cluster():
        data = request.json
        session_id = data["session_id"]
        stream_id = data["stream_id"]
        profile_id = data["profile_id"]
        speaker_name = data["speaker_name"]
        turns_word_id = data["cluster_word_id"]
        reset_and_update = data["reset_and_update"]

        print(
            "Speaker name:",
            speaker_name,
            "turns_word_id:",
            turns_word_id,
            "reset_and_update:",
            reset_and_update,
        )

        turns_word_id = [
            [int(idx) for idx in turn.split(",")] for turn in turns_word_id
        ]
        list_word_id = [
            f"asr/{session_id}/{stream_id}/word-{idx}"
            for turn in turns_word_id
            for idx in turn
        ]

        # get current cluster
        try:
            cluster_data = json.loads(
                db.get(f"asr/{session_id}/{stream_id}/spk-cluster").decode("utf-8")
            )
        except:
            cluster_data = {}
        # find the name
        if speaker_name not in cluster_data or reset_and_update:
            cluster_data[speaker_name] = []
        # update the cluster
        cluster_data[speaker_name].extend(list_word_id)
        cluster_data[speaker_name] = list(set(cluster_data[speaker_name]))
        # save to db
        print("Save cluster data", cluster_data)
        result_session_update = str(
            db.set(
                f"asr/{session_id}/{stream_id}/spk-cluster", json.dumps(cluster_data)
            )
        )
        print(
            f"Session update result with key asr/{session_id}/{stream_id}/spk-cluster:",
            result_session_update,
        )

        # save to user database
        user = request.headers.get("X-Forwarded-User", None)
        result_user_update = str(
            db.set(f"{user}_{profile_id}/spk-cluster", json.dumps(cluster_data))
        )
        print(
            f"User update result with key {user}_{profile_id}/spk-cluster:",
            result_user_update,
        )

        return {
            "status": result_session_update,
            "user_update_status": result_user_update,
        }

    @app.route("/meeting", methods=["GET", "POST"])
    def meeting():
        user = request.headers.get("X-Forwarded-User", None)
        if user is None:
            return index()
        groups = getUserGroups(user)
        if "presenter" not in groups:
            print(user + " has no right to create session")
            return render_template("error.html", text="Permission denied", theme=theme)
        try:
            name = request.form["name"]
            if name == "":
                now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
                name = "Meeting " + (now.strftime("%d.%m.%Y %H:%M"))
        except:
            print("No name given")
            now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
            name = "Meeting " + (now.strftime("%d.%m.%Y %H:%M"))
        return render_template(
            "saasr-session.html",
            correction_mode_deactivated=False,
            user=user,
            show_url=True,
            name=name,
            theme=theme,
            server=api_external_host,
        )

    @app.route("/set_user_name/", methods=["POST"])
    def set_user_name():
        print("Set user name")
        data = request.json
        user = request.headers.get("X-Forwarded-User", None)

        if user != None and "id" in data and user == data["id"] and "name" in data:
            db.hset("user_to_name", user, data["name"])
        if (
            user != None
            and "id" in data
            and user == data["id"]
            and "audio_sample" in data
        ):
            db.hset("user_to_audiosample", user, data["audio_sample"])

        name_ = db.hget("user_to_name", user)
        if name_ is not None:
            name = name_.decode("utf-8")
        else:
            name = user

        audio_sample = db.hget("user_to_audiosample", user)
        if audio_sample is not None:
            audio_sample = audio_sample.decode("utf-8")

        return {
            "name": name,
            "audio_sample": audio_sample,
        }

    @app.get("/gettoken")
    def gettoken():
        token = request.cookies.get("_forward_auth")
        if token:
            return render_template("gettoken.html", token=token)
        else:
            return "`_forward_auth` cookie not set", 400

    def extractSpeaker(inp):
        pattern = r"^(.*?)<([^>]+)>$"
        match = re.match(pattern, inp)

        if match:
            name = match.group(1).strip()  # Extract and strip the name
            email = match.group(2)  # Extract the email

            print("Name:", name)
            print("Email:", email)
        else:
            name = inp
            email = inp + "@guest"
            print("Invalid email format.")
        print("", flush=True)
        return email, name

    @app.route("/joinmeeting/<id>/")
    def joinmeeting(id):
        sessionID = id
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        res = requests.post(server + "/ltapi/session_type/" + sessionID, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_type in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        type = res.text
        print("Type", type)
        if "kitall" not in groups and type.startswith("kitall"):
            print("WARNING: User is not allowed to join this session.")
            return render_template("error.html", text="Permission denied", theme=theme)
        if "kitemployee" not in groups and type.startswith("kitemployee"):
            print("WARNING: User is not allowed to join this session.")
            return render_template("error.html", text="Permission denied", theme=theme)

        res = requests.post(server + "/ltapi/session_name/" + sessionID, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_name in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        name = res.text

        data = {"session": sessionID}

        res = requests.post(
            server + "/ltapi/addSpeaker", json=json.dumps(data), verify=False
        )
        if res.status_code != 200:
            print("ERROR in requesting addSpeaker in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        streamID = res.text.split()[0]

        res = requests.post(server + "/ltapi/" + sessionID + "/getgraph")
        if res.status_code != 200:
            print("ERROR in getgraph in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        graph = json.loads(res.text)

        langs = []
        for k in getOutputWorker(graph, streamID):
            print("Worker", "joinmeeting", k)
            if "postproduction" in k:
                continue
            k = k.split(":")
            l = requests.post(
                server
                + "/ltapi/"
                + sessionID
                + "/"
                + k[1]
                + "/get_output_language_component",
                json=json.dumps({"component": k[0]}),
            )
            if l.status_code != 200:
                print("ERROR in get_output_language_component:", l.reason)
                continue
            print("Lange:" + l.text)
            langs.append(l.text)
        print("Langauges:", langs)

        data = {"controll": "START"}
        info = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamID + "/append",
            json=json.dumps(data),
            verify=False,
        )
        if info.status_code != 200:
            print("ERROR in sending START message in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + info.reason, theme=theme
            )

        if len(langs) == 0:
            return render_template(
                "error.html", text="No ASR languages found", theme=theme
            )

        data = json.loads(db.get("SessionCreationArguments" + str(sessionID)))
        error_correction = data["error_correction"]
        error_correction_init = data["error_correction_init"]
        show_url = "do_not_show_url" not in data
        saasr = data["asr_prop"]["saasr"]
        log = data["log"]
        lang = data["language"]
        aiassistant = data["aiassistant"]

        windows_without_wiki_suggester = []

        if error_correction_init is not None:
            init_windows = [
                str(langs.index("English Reference") + 1),
                str(langs.index("English Automatic Transcript with Errors") + 1),
                str(langs.index("English") + 1),
                str(langs.index("English Audio") + 1),
            ]
            windows_without_wiki_suggester = init_windows
        elif use_memory:
            init_windows = [
                str(i + 1) for i, l in enumerate(langs) if "Memory" in l
            ]  # [str(langs.index('Multilingual Memory')+1)]
        else:
            init_windows = [str(langs.index("Multilingual") + 1)]

        errorCorrectionTools = False if error_correction == "None" else error_correction

        return render_template(
            "session.html",
            recording=True,
            host=False,
            type="Seminar",
            chat=aiassistant,
            selection_tool_able_to_remove_ranges=error_correction_init is not None,
            errorCorrectionTools=errorCorrectionTools,
            correction_mode_deactivated=error_correction_init is not None,
            user=user,
            show_url=show_url,
            init_windows=init_windows,
            windows_without_wiki_suggester=windows_without_wiki_suggester,
            name=name,
            langs=langs,
            add_lang=[],
            default_lang=langs[0],
            session=sessionID,
            stream=streamID,
            server=api_external_host,
            groups=groups,
            theme=theme,
            saasr=(saasr == "1"),
            memory="memory" in lang,
            log=log == "True",
        )

    @app.route("/viewmeeting/<id>/")
    def viewmeeting(id):

        streamID = 0
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        res = requests.post(server + "/ltapi/session_type/" + id, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_type in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        type = res.text

        if "kitall" not in groups and type.startswith("kitall"):
            print("WARNING: User is not allowed to join this session.")
            return render_template("error.html", text="Permission denied", theme=theme)
        if "kitemployee" not in groups and type.startswith("kitemployee"):
            print("WARNING: User is not allowed to join this session.")
            return render_template("error.html", text="Permission denied", theme=theme)

        res = requests.post(server + "/ltapi/session_name/" + id, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_name in /session")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        name = res.text

        res = requests.post(server + "/ltapi/" + id + "/getgraph")
        if res.status_code != 200:
            print("ERROR in getgraph in /joinmeeting")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        graph = json.loads(res.text)
        print("Graph:", graph)

        langs = []
        for k in getOutputWorker(graph, streamID):
            k = k.split(":")
            l = requests.post(
                server + "/ltapi/" + id + "/" + k[1] + "/get_output_language_component",
                json=json.dumps({"component": k[0]}),
            )
            if l.status_code != 200:
                print("ERROR in get_output_language_component:", l.reason)
                continue
            print("Lange:" + l.text)
            langs.append(l.text)
        print("Langauges:", langs)

        print("External host:", api_external_host, flush=True)

        data = json.loads(db.get("SessionCreationArguments" + str(id)))
        aiassistant = data["aiassistant"]

        return render_template(
            "session.html",
            recording=False,
            host=False,
            type="Seminar",
            chat=aiassistant,
            selection_tool_able_to_remove_ranges=False,
            errorCorrectionTools=False,
            correction_mode_deactivated=False,
            user=user,
            show_url=True,
            init_windows=[str(langs.index("Multilingual") + 1)],
            windows_without_wiki_suggester=[],
            name=name,
            langs=langs,
            add_lang=[],
            default_lang=langs[0],
            session=id,
            server=api_external_host,
            groups=groups,
            theme=theme,
        )

    @app.route("/joinroom/", methods=["GET", "POST"])
    def joinroom():

        sessionID = request.form["id"]
        password = request.form["password"]
        name = request.form["room_name"]

        res = requests.post(
            server + "/ltapi/check_password/" + sessionID,
            json=json.dumps({"name": name, "password": password}),
            verify=False,
        )
        if res.status_code != 200:
            print("ERROR in requesting session_type in /joinroom")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        answer = res.text
        if answer != "OK":
            print("ERROR in requesting access to room")
            return render_template(
                "error.html", text="Could not connect: " + answer, theme=theme
            )

        return render_template(
            "controllRoom.html",
            name=name,
            session=sessionID,
            server=api_external_host,
            theme=theme,
        )

    @app.route("/session/<id>/")
    def session(id):

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        res = requests.post(server + "/ltapi/session_type/" + id, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_type in /session")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        type = res.text
        if "kitall" not in groups and type.startswith("kitall"):
            print("WARNING: User is not allowed to join this session.")
            return render_template("error.html", text="Permission denied", theme=theme)
        if "kitemployee" not in groups and type.startswith("kitemployee"):
            print("WARNING: User is not allowed to join this session.")
            return render_template("error.html", text="Permission denied", theme=theme)

        cached_page = db.get(f"session_{id}")
        print("Check cache page:",f"session_{id}",flush=True)
        if cached_page:
            print("Return:",f"session_{id}",flush=True)
            return cached_page.decode("utf-8")


        res = requests.post(server + "/ltapi/session_name/" + id, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_name in /session")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        name = res.text

        res = requests.post(server + "/ltapi/" + id + "/getgraph")
        if res.status_code != 200:
            print("ERROR in getgraph in /session")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        graph = json.loads(res.text)
        worker = getOutputWorker(graph, 0)
        langs = []
        for k in worker:
            print("Worker", "session", k)
            if "postproduction" in k:
                continue
            streamID = k.split(":")[1]
            l = requests.post(
                server
                + "/ltapi/"
                + id
                + "/"
                + streamID
                + "/get_output_language_component",
                json=json.dumps({"component": k}),
            )
            if l.status_code != 200:
                print("ERROR in get_output_language_component in /session:", l.reason)
                continue
            print("Lange:" + l.text)
            langs.append(l.text)

        if len(langs) == 0:
            return render_template(
                "error.html", text="No ASR languages found", theme=theme
            )

        data = json.loads(db.get("SessionCreationArguments" + str(id)))
        if "aiassistant" in data:
            aiassistant = data["aiassistant"]
        else:
            aiassistant = False

        #index = langs.index("Multilingual") if "Multilingual" in langs else 0
        #init_windows = [str(index + 1)]

        return render_template(
            "session.html",
            recording=False,
            host=False,
            type="Lecture",
            chat=aiassistant,
            selection_tool_able_to_remove_ranges=False,
            errorCorrectionTools=False,
            correction_mode_deactivated=False,
            user=user,
            show_url=True,
            init_windows=[],
            windows_without_wiki_suggester=[],
            name=name,
            langs=langs,
            add_lang=[],
            default_lang="",
            session=id,
            server=api_external_host,
            groups=groups,
            theme=theme,
            permanent_link=f"https://{domain}/session/{id}",
            domain=domain,
        )


    @app.route("/generate_cache_page/<id>",methods=["GET", "POST"])
    def generate_cache_page(id):
        """Fetch new data and generate the processed page using Puppeteer."""
        lock = db.lock(f"cache_lock_{id}", timeout=LOCK_TIMEOUT)

        if lock.acquire(blocking=False):  # If no other process is generating
            try:
                # Run Puppeteer script to generate HTML
                result = subprocess.run(["node", "ltweb/static/js/create_cached_page.js", "http://localhost:5000/session/"+id+"/", id], capture_output=True, text=True)

                if result.returncode == 0:
                    # Read generated HTML from Puppeteer output
                    with open("/tmp/processed." + id + ".html", "r") as file:
                        processed_html = file.read()
                    
                    # Store in Redis
                    db.set(f"session_{id}", processed_html)
                    print("Saved html page:", f"session_{id}")
                else:
                    print("Puppeteer output:", result.stdout, flush=True)
                    print("Pusher output:", result.stderr, flush=True)
                    print("Error: Failed to generate cached page", flush=True)

            except Exception as e:
                print(f"An error occurred: {e}", flush=True)
                traceback.print_exc()
            finally:
                lock.release()
        return "OK",200
    
    @app.route("/delete_cache_page/<id>/")
    def delete_cache_page(id):
        cached_page = db.get(f"session_{id}")
        if cached_page:
            print("Cached page exists, deleting...", flush=True)
        db.delete(f"session_{id}")
        cached_page = db.get(f"session_{id}")
        if cached_page:
            print("Cached page exists, deleting did not work", flush=True)
        else:
            print("Cached page deleted", flush=True)

        try:
            os.remove(f"/tmp/processed.{id}.html")
        except FileNotFoundError:
            print(f"File /tmp/processed.{id}.html not found, skipping deletion.")
        return "OK"

    @app.route("/present/<id>")
    def present(id):

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        res = requests.post(server + "/ltapi/session_host/" + id, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session_host in /present")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        host = res.text

        if "admin" not in groups and not ("presenter" in groups and host == user):
            print(user + " has no right to present")
            return render_template("error.html", text="Permission denied", theme=theme)

        res = requests.post(server + "/ltapi/session_name/" + id, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting session name in /present")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        name = res.text

        res = requests.post(server + "/ltapi/" + id + "/getgraph")
        if res.status_code != 200:
            print("ERROR in getgraph in /present")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        graph = json.loads(res.text)
        worker = getOutputWorker(graph, 0)
        langs = []
        for k in worker:
            print("Worker", "present", k)
            if "postproduction" in k:
                continue
            streamID = k.split(":")[1]
            l = requests.post(
                server
                + "/ltapi/"
                + id
                + "/"
                + streamID
                + "/get_output_language_component",
                json=json.dumps({"component": k}),
            )
            if l.status_code != 200:
                print("ERROR in get_output_language_component:", l.reason)
                continue
            print("Lange:" + l.text)
            langs.append(l.text)

        if len(langs) == 0:
            return render_template(
                "error.html", text="No ASR languages found", theme=theme
            )

        data = json.loads(db.get("SessionCreationArguments" + str(id)))
        if "aiassistant" in data:
            aiassistant = data["aiassistant"]
        else:
            aiassistant = 0

        if "Multilingual" in langs:
            init_index = str(langs.index("Multilingual") + 1)
        else:
            init_index = "1"

        return render_template(
            "session.html",
            recording=True,
            host=True,
            type="Lecture",
            chat=aiassistant,
            selection_tool_able_to_remove_ranges=False,
            errorCorrectionTools=False,
            correction_mode_deactivated=False,
            user=user,
            show_url=True,
            init_windows=[init_index],
            windows_without_wiki_suggester=[],
            name=name,
            langs=langs,
            add_lang=[],
            default_lang=langs[0],
            session=id,
            stream=0,
            server=api_external_host,
            groups=groups,
            theme=theme,
        )

    def list_sessions(type, groups, user):
        res = requests.post(server + "/ltapi/list_sessions/" + type, verify=False)
        if res.status_code != 200:
            print("ERROR in requesting for list of " + type)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        list = json.loads(res.text)

        if "kitall" in groups:
            res = requests.post(
                server + "/ltapi/list_sessions/" + "kitall" + type, verify=False
            )
            if res.status_code != 200:
                print("ERROR in requesting for list of kitall" + type)
                return render_template(
                    "error.html", text="Could not connect: " + res.reason, theme=theme
                )
            list += json.loads(res.text)
        if "kitemployee" in groups:
            res = requests.post(
                server + "/ltapi/list_sessions/" + "kitemployee" + type, verify=False
            )
            if res.status_code != 200:
                print("ERROR in requesting for list of kitemployee" + type)
                return render_template(
                    "error.html", text="Could not connect: " + res.reason, theme=theme
                )
            list += json.loads(res.text)
        print("List of lectures:", list)
        print("User", user)
        print("Groups", groups)
        for i in range(len(list)):
            if list[i][2] == user:
                list[i][2] = True
            elif "admin" in groups:
                list[i][2] = True
            else:
                list[i][2] = False
        print("List of lectures or meetings:", list, flush=True)
        return list

    @app.route("/overview/<type>")
    def overview(type):
        print(server + "/ltapi/list_sessions/" + type)

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)

        list = list_sessions(type, groups, user)

        other_users = get_other_users(user)

        if type == "meeting_room":
            return render_template("roomOverview.html", sessionList=list, theme=theme)
        else:
            list2 = list_sessions(
                "lecture" if type == "meeting" else "meeting", groups, user
            )

            listMeeting = list if type == "meeting" else list2
            listLecture = list if type == "lecture" else list2

            user_name = user
            if user != None:
                user_name_ = db.hget("user_to_name", user)
                if user_name_ is not None:
                    user_name = user_name_.decode("utf-8")
            res = requests.post(
                server + "/ltapi/list_available_languages", verify=False
            )
            asr_langs = (
                json.loads(res.text)["asr"]
                if res.status_code == 200 and "asr" in json.loads(res.text)
                else []
            )
            return render_template(
                "meetingOverview.html",
                sessionList=listMeeting,
                sessionListLecture=listLecture,
                server=api_external_host,
                asr_langs=asr_langs,
                theme=theme,
                user_name=user_name,
                user=user,
                other_users=other_users,
            )

    def getOutputWorker(graph, stream):
        candidates = ["user:" + str(stream)]
        checked = []
        worker = []
        while len(candidates) > 0:
            can = candidates[0]
            if can not in checked and can in graph and "api" in graph[can]:
                worker.append(can)
            checked.append(can)
            candidates = candidates[1:]
            if can in graph:
                for k in graph[can]:
                    if not k in checked:
                        candidates.append(k)
        return worker

    @app.route("/get_profile_data/<profile_id>", methods=["GET", "POST"])
    def getProfileData(profile_id):
        user = request.headers.get("X-Forwarded-User", None)
        profile = db.hget("user_to_profiles", f"{user}_{profile_id}")
        if profile is None:
            print("Gathering default profile")
            profile = json.dumps(
                [
                    ("name", ""),
                    ("language", "en"),
                    ("language", "de"),
                    ("mtLanguage", "en"),
                    ("mtLanguage", "de"),
                    ("mtLanguage", "es"),
                    ("format", "resending"),
                    ("availability", "public"),
                    ("videoLanguage", "None"),
                    ("smartChaptering", "online_dynamic"),
                    ("summarization", "1"),
                    ("errorCorrection", "None"),
                    ("logging", "1"),
                    ("type", "lecture"),
                    ("save_profile", "1"),
                    ("saasr", "0"),
                ]
            )
        # print(f"{profile = }")
        return profile

    # ===========
    @app.route("/create_error_correction", methods=["GET"])
    def create_error_correction():
        return redirect(
            url_for(
                "create",
                name="Correction Tool",
                legals="on",
                language="en",
                format="resending",
                availability="public",
                videoLanguage="en",
                smartChaptering="streaming_simple",
                errorCorrection="dialog2",
                errorCorrectionInit="S1",
                do_not_show_url="",
                theme="defaulttheme",
            )
            + "&language=su"
        )

    @app.route("/create_multilingual_error_correction", methods=["GET"])
    def create_multilingual_error_correction():
        return redirect(
            url_for(
                "create",
                name="Correction Tool",
                legals="on",
                language="en",
                format="resending",
                availability="public",
                videoLanguage="en",
                smartChaptering="streaming_simple",
                errorCorrection="dialog2",
                errorCorrectionInit="S1",
                do_not_show_url="",
                theme="defaulttheme",
            )
            + "&language=de&language=su"
        )

    @app.route("/create", methods=["GET", "POST"])
    def create():
        """
        Create a new session.
        ---
        requestBody:
          required: true
          content:
            application/x-www-form-urlencoded:
              schema:
                type: object
                required:
                - name
                - type
                - availability
                - language
                properties:
                  name:
                    type: string
                    description: "Session name"
                  type:
                    type: string
                    description: "Session type (lecture, meeting, ...)"
                    enum:
                    - lecture
                    - meeting
                  availability:
                    type: string
                    description: "Session availability (public, kitall, kitemployee)"
                    enum:
                    - public
                    - kitall
                    - kitemployee
                  language:
                    type: string
                    description: "ASR language"
                  errorCorrection:
                    type: string
                    description: "???"
                  errorCorrectionInit:
                    type: string
                    description: "???"
                  demoMode:
                    type: string
                    description: "???"
                  otherSpeakers:
                    type: array
                    items:
                        type: string
                        description: "???"
                  smartChaptering:
                    type: string
                    description: "Smart chaptering mode to use"
                    enum:
                    - streaming_simple
                    - online_dynamic
                    - online_static
                    - offline
                  summarization:
                    type: string
                    description: "Whether to use summarization"
                  postproduction:
                    type: string
                    description: "Whether to use post-production"
                  logging:
                    type: string
                    description: "Whether to use logging"
                  mtLanguage:
                    type: array
                    items:
                        type: string
                        description: "Languages to translate to"
                  audioLanguage:
                    type: array
                    items:
                        type: string
                        description: "TTS languages"
                  videoLanguage:
                    type: string
                    description: "Video language"
                  video:
                    type: string
                    description: "Background video to use for facedubbed video generation"
                  format:
                    type: string
                    description: "Resending format"
                    enum:
                    - resending
                    - online
                    - offline
                  saasr:
                    type: string
                    description: "???"
                  speaker:
                    type: string
                    description: "???"
        responses:
          '200':
            description: "Rendered session page"
            content:
              text/html:
                schema:
                  type: string
        """
        print("Create session")
        # data = json.loads(request.json);
        if request.method == "GET":
            request.form = request.args

        # print(request.form)

        name = request.form["name"]
        type = request.form.get("type", "lecture")
        availability = request.form["availability"]

        lang = request.form.getlist("language")
        # print("Input language",lang)

        error_correction = (
            request.form["errorCorrection"]
            if "errorCorrection" in request.form
            else "None"
        )
        error_correction_init = (
            request.form["errorCorrectionInit"]
            if "errorCorrectionInit" in request.form
            else None
        )
        compute_hyperarticulation = (
            "compute_hyperarticulation" in request.form
            and request.form["compute_hyperarticulation"].lower() == "true"
        )
        user = request.headers.get("X-Forwarded-User", None)

        show_url = "do_not_show_url" not in request.form
        theme_to_use = request.form["theme"] if "theme" in request.form else theme

        # for info in db.hgetall("active_sessions").values():
        #     info = json.loads(info)
        #     if "host" in info and info["host"] == user:
        #         return render_template('error.html',text="You already have a session started. Go to the main page -> 'Live event' -> 'Join lecture' -> 'Present'. End your current session with 'End Lecture' to start a new session.",theme=theme)

        groups = getUserGroups(user)
        if "presenter" not in groups and (
            error_correction == "None"
            or error_correction_init is None
            or "collector" not in groups
        ):
            print(user + " has no right to create session")
            return render_template("error.html", text="Permission denied", theme=theme)
        if "demoMode" in request.form:
            demo = int(request.form["demoMode"])
        else:
            demo = 0

        other_speakers = request.form.getlist("otherSpeakers")
        print("OtherSpeakers", other_speakers)

        audio_samples = []
        if user is not None:
            audio_samples.append(db.hget("user_to_audiosample", user))
        audio_samples += [db.hget("user_to_audiosample", s) for s in other_speakers]
        audio_samples = [
            a.decode("utf-8") if a is not None else "None" for a in audio_samples
        ]

        # print("Audio Samples",audio_samples)

        if "smartChaptering" in request.form:
            # print("smartChaptering:",request.form["smartChaptering"])
            if request.form["smartChaptering"] == "streaming_simple":
                textseg_prop = {"method": "streaming_simple"}
            elif request.form["smartChaptering"] == "online_dynamic":
                textseg_prop = {"method": "online_model", "dynamic_threshold": True}
            elif request.form["smartChaptering"] == "online_static":
                textseg_prop = {"method": "online_model", "dynamic_threshold": False}
            else:
                textseg_prop = {"method": "offline_model"}
        else:
            textseg_prop = {"method": "streaming_simple"}

        summarize = "summarization" in request.form
        postproduction = request.form.getlist("postproduction")

        use_memory = "memory" in request.form

        if "logging" in request.form:
            log = "True"
        else:
            log = "False"

        mt_langs = request.form.getlist("mtLanguage")
        # print("MT_LANGS",mt_langs)

        tts_langs = request.form.getlist("audioLanguage")
        # print("TTS_LANGS",tts_langs)

        if "videoLanguage" in request.form:
            video_lang = request.form["videoLanguage"]
        else:
            video_lang = None

        if "video" in request.form:
            video = request.form["video"]
        else:
            video = None

        if "format" in request.form:
            format = request.form["format"]
        else:
            format = "resending"

        if format == "offline":
            textseg_prop = {"method": "offline_model"}

        # print("Demo:",demo)
        # print("Name:",name)
        # print("Type:",type)
        # print("Format:",format)
        mt_prop = {"mode": "SendUnstable"}
        asr_prop = {"mode": "SendUnstable", "stability_detection": "True"}
        if format == "offline":
            mt_prop["mode"] = "SendStable"
            asr_prop["mode"] = "SendStable"
            asr_prop["stability_detection"] = "False"
        elif format == "online":
            mt_prop["mode"] = "SendPartialAndUnstable"
            asr_prop["mode"] = "SendUnstable"

        if "filterNonVerbal" in request.form:
            asr_prop["filter_non_verbal"] = "True"

        ##### check saasr box been clicked or not
        if "saasr" in request.form:
            print("SAASR:", request.form["saasr"])
            saasr = request.form["saasr"]
        else:
            saasr = "0"

        if "aiassistant" in request.form:
            print("AI Assistant:", request.form["aiassistant"])
            aiassistant = True
        else:
            aiassistant = False

        profile_id = request.form.get("profile")

        print("INPUT LANGS", json.dumps(lang))
        print("Type:", type)
        if demo == 1:
            sessionID, streamID = initDemoSession()
        else:
            sessionID, streamID = initSession(
                lang,
                mt_prop,
                asr_prop,
                textseg_prop,
                video,
                video_lang,
                mt_langs,
                tts_langs,
                summarize,
                postproduction,
                error_correction,
                error_correction_init,
                log,
                type,
                audio_samples,
                user,
                compute_hyperarticulation,
                use_memory,
                aiassistant,
                speakerdiarization=saasr == "1",
                profile_id=profile_id,
            )

        print("Session:", sessionID, "StreamID:", streamID)

        # wait till all workers startet
        res = requests.post(server + "/ltapi/" + sessionID + "/getgraph")
        if res.status_code != 200:
            print("ERROR in getgraph in /create")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        graph = json.loads(res.text)

        langs = []
        for k in getOutputWorker(graph, 0):
            print("Worker", "create session", k)
            if "postproduction" in k:
                continue
            k = k.split(":")
            l = requests.post(
                server
                + "/ltapi/"
                + sessionID
                + "/"
                + k[1]
                + "/get_output_language_component",
                json=json.dumps({"component": k[0]}),
            )
            if l.status_code != 200:
                print("ERROR in get_output_language_component:", l.reason)
                continue
            print("Lange:" + l.text)
            langs.append(l.text)

        if name == "":
            now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
            name = "Lecture " + (now.strftime("%d.%m.%Y %H:%M"))

        data = {"controll": "START", "name": name, "host": user}
        if availability == "public":
            data["type"] = type
        elif availability != "private":
            data["type"] = availability + type
        data["access"] = availability

        if "path" in request.form and request.form["path"].strip() != "":
            archivepath = request.form["path"]+"/"+name
            data["directory"] = "/logs/archive/" + archivepath
        elif "logging" in request.form and user is not None:
            data["directory"] = f"/logs/archive/home/{user}/{name}_{sessionID}"
        if("path" in request.form):
            print("Path:",request.form["path"])
        if("directory" in data):
            print("Usigng directory:",data["directory"])

        today = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
        data["meta"] = (
            '{"title":"'
            + name
            + '", "presenter": "'
            + user
            + '", "event": "'
            + today.strftime("%d.%m.%Y")
            + '"}'
        )


        info = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamID + "/append",
            json=json.dumps(data),
            verify=False,
        )
        if info.status_code != 200:
            print("ERROR in append in /create")
            return render_template(
                "error.html", text="Could not connect: " + info.reason, theme=theme
            )

        if len(langs) == 0:
            return render_template(
                "error.html", text="No ASR languages found", theme=theme
            )

        if request.form.get("save_profile") == "1":
            profile = list(request.form.items(multi=True))
            j = -1
            for i in range(len(profile)):
                if profile[i][0] == "profile_names":
                    j = i
                    break
            if j >= 0:
                profile_names = profile.pop(j)[1]
                if profile_names:
                    db.hset("user_to_profile_names", f"{user}", profile_names)
                    print("Set new profile names ", profile_names)

            for i in range(len(profile)):
                if(profile[i][0] == "path"):
                    profile.pop(i)
                    break
            db.hset("user_to_profiles", f"{user}_{profile_id}", json.dumps(profile))
            print("Saving profile " + str(profile))

        init_windows = []
        windows_without_wiki_suggester = []
        if error_correction_init is not None:
            if "English Reference" in langs:
                init_windows = [
                    str(langs.index("English Reference") + 1),
                    str(langs.index("English Automatic Transcript with Errors") + 1),
                    str(langs.index("English Audio") + 1),
                ]
                windows_without_wiki_suggester = init_windows
            elif "German Reference" in langs:
                init_windows = [
                    str(langs.index("German Reference") + 1),
                    str(langs.index("German Automatic Transcript with Errors") + 1),
                    str(langs.index("German Audio") + 1),
                ]
                windows_without_wiki_suggester = init_windows
            elif "Multilingual Reference" in langs:
                init_windows = [
                    str(langs.index("Multilingual Reference") + 1),
                    str(
                        langs.index("Multilingual Automatic Transcript with Errors") + 1
                    ),
                    str(langs.index("Multilingual Audio") + 1),
                ]
                windows_without_wiki_suggester = init_windows
        elif use_memory:
            init_windows = [
                str(i + 1) for i, l in enumerate(langs) if "Memory" in l
            ]  # [str(langs.index('Multilingual Memory')+1)]
        else:
            index = langs.index("Multilingual") if "Multilingual" in langs else 0
            init_windows = [str(index + 1)]

        errorCorrectionTools = False if error_correction == "None" else error_correction
        agreed_error_correction_eval = db.get(f"{user}_agreed_error_correction_eval")
        if agreed_error_correction_eval is not None:
            agreed_error_correction_eval = str(agreed_error_correction_eval, "utf-8")

        shorten = request.form.get("shorten")
        if shorten and "/webapi/shorten/" in shorten:
            shortening_server, short = shorten.split("/webapi/shorten/")

            shortening_server = f"{shortening_server}/webapi/do_urlshortening"
            payload = {
                "url": f"https://{os.getenv('DOMAIN')}/session/{sessionID}/",
                "short": short,
            }
            response = requests.post(shortening_server, json=payload)
            print(f"Shortening server: {shortening_server}, {payload = }, {response.text = }")
        else:
            shorten = f"https://{domain}/session/{sessionID}"

        return render_template(
            "session.html",
            recording=True,
            host=True,
            type=type,
            chat=aiassistant,
            selection_tool_able_to_remove_ranges=error_correction_init is not None,
            errorCorrectionTools=errorCorrectionTools,
            correction_mode_deactivated=error_correction_init is not None,
            user=user,
            agreed_error_correction_eval=agreed_error_correction_eval,
            show_url=show_url,
            init_windows=init_windows,
            windows_without_wiki_suggester=windows_without_wiki_suggester,
            name=name,
            langs=langs,
            add_lang=[],
            default_lang=langs[0],
            session=sessionID,
            stream=streamID,
            server=api_external_host,
            groups=groups,
            theme=theme_to_use,
            saasr=(saasr == "1"),
            memory=use_memory,
            profile_id=profile_id,
            domain=domain,
            permanent_link=shorten,
            log=log == "True",
        )

    def initDemoSession():
        res = requests.post(
            server + "/ltapi/get_demo_asr", json=json.dumps({}), verify=False
        )
        if res.status_code != 200:
            print("ERROR in requesting demo graph for ASR in /initDemoSession")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        sessionID, streamID = res.text.split()
        return sessionID, streamID

    def initSession(
        lang,
        mt_prop,
        asr_prop,
        textseg_prop,
        video,
        video_lang,
        mt_langs,
        tts_langs,
        summarize,
        postproduction,
        error_correction,
        error_correction_init,
        log,
        type,
        audio_samples,
        user,
        compute_hyperarticulation,
        use_memory,
        aiassistant,
        speakerdiarization,
        profile_id,
    ):
        d = {"language": lang}

        d["mt"] = json.dumps(mt_langs)
        d["tts"] = ",".join(tts_langs)
        if video_lang != "None":
            d["video"] = video_lang
        d["log"] = log
        d["summarize"] = summarize
        d["use_memory"] = use_memory
        d["postproduction"] = postproduction
        d["error_correction"] = error_correction
        d["error_correction_init"] = error_correction_init
        d["compute_hyperarticulation"] = compute_hyperarticulation
        d["asr_prop"] = asr_prop
        d["textseg_prop"] = textseg_prop
        d["mt_prop"] = mt_prop
        d["lip_prop"] = {"facedubbing_video": video}
        d["aiassistant"] = aiassistant

        d["user"] = user
        d["speakerdiarization"] = speakerdiarization
        d["profile_id"] = profile_id

        print("Arguments to get_default_asr", d)

        res = requests.post(
            server + "/ltapi/get_default_asr", json=json.dumps(d), verify=False
        )
        if res.status_code != 200:
            print("ERROR in requesting default graph for ASR", res.text)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        sessionID, streamID = res.text.split()

        return sessionID, streamID

    @app.route("/archive/<dir>", methods=["GET", "POST"])
    def archive(dir):
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        orig_dir = dir
        dir = urllib.parse.unquote_plus(dir)

        if (
            user is not None
            and os.path.normpath(dir) == "/home/" + user
            and "presenter" in groups
        ):
            requests.post(
                archiveAPI + "/ltarchive/create_home", json=json.dumps({"user": user})
            ).text

        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = False
        print("My groups:", groups)
        print("Access:", rights)
        for g in groups:
            if g in rights:
                access = True
        if not access:
            if user is None:
                return redirect(url_for("login"))
            else:
                print("No access to Archive")
                return render_template(
                    "error.html", text="Permission denied", theme=theme
                )

        w_rights = requests.post(
            archiveAPI + "/ltarchive/write_access", json=json.dumps({"directory": dir})
        ).text
        w_rights = json.loads(w_rights)
        w_access = False
        for g in groups:
            if g in w_rights:
                w_access = True

        print("Show archive for ", dir)
        print(archiveAPI + "/ltarchive/ls")
        res = requests.post(
            archiveAPI + "/ltarchive/ls",
            json=json.dumps({"directory": dir, "groups": groups}),
        )
        if res.status_code != 200:
            print("ERROR in doing ltarchive/ls", res.text)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        list = res.text
        print(list)
        list = json.loads(list)
        list.sort()
        for i in range(len(list)):
            print(i, " out of ", len(list))
            print("Meta:", list[i][2], flush=True)
            print(json.loads(list[i][2]), flush=True)
            print("L0:", list[i][0], flush=True)
        list = [
            (
                (
                    l[0],
                    l[1],
                    urllib.parse.quote_plus(os.path.dirname(dir)),
                    json.loads(l[2]),
                    l[3],
                    l[4]
                )
                if l[0] == " Back"
                else (
                    l[0],
                    l[1],
                    urllib.parse.quote_plus(dir + "/" + l[0]),
                    json.loads(l[2]),
                    l[3],
                    l[4]
                )
            )
            for l in list
        ]
        print("List:", list, flush=True)

        print(server + "/ltapi/list_available_languages", flush=True)
        resp = requests.post(
            server + "/ltapi/list_available_languages", verify=False, timeout=10
        )
        asr_langs = (
            resp.json()["asr"]
            if resp.status_code == 200 and "asr" in resp.json()
            else []
        )
        print(asr_langs, flush=True)
        mt_langs = (
            resp.json()["mt"] if resp.status_code == 200 and "mt" in resp.json() else []
        )
        print(mt_langs, flush=True)
        tts_langs = (
            resp.json()["tts"]
            if resp.status_code == 200 and "tts" in resp.json()
            else []
        )
        print(tts_langs, flush=True)
        video_langs = (
            tts_langs
            if resp.status_code == 200
            and "lip" in resp.json()
            and len(resp.json()["lip"]) > 0
            else []
        )
        print(video_langs, flush=True)

        resp = requests.get(server + "/ltapi/list_videos", verify=False)
        if resp.status_code == 200:
            videos = resp.json()
        else:
            print(resp.status_code)
            print(resp.text)
            videos = []
        print("Videos", flush=True)

        profiles = [[f"profile_{i}", f"Profile {i}"] for i in range(1, 11)]
        profile_names = db.hget("user_to_profile_names", f"{user}")
        if profile_names:
            for name, profile in zip(profile_names.decode().split("|"), profiles):
                profile[1] = name

        response = requests.post(
            archiveAPI + "/ltarchive/use_aiassistant",
            json=json.dumps({"directory": dir}),
        )
        if response.status_code != 200:
            print("ERROR in doing ltarchive/use_aiassistant", response.text)
            return render_template(
                "error.html", text="Could not connect: " + response.reason, theme=theme
            )
        aiassistant = True if response.text == "TRUE" else False

        return render_template(
            "archive.html",
            children=list,
            name=dir.strip("/").split("/")[-1],
            directory=dir,
            dir=urllib.parse.quote_plus(dir),
            theme=theme,
            asr_langs=asr_langs,
            mt_langs=mt_langs,
            tts_langs=tts_langs,
            video_langs=video_langs,
            groups=groups,
            profiles=profiles,
            videos=videos,
            availability=getAvailability(),
            presentation_mode=presentation_mode,
            w_access=w_access,
            chat=aiassistant,
            user=user,
            mediafile=urllib.parse.quote_plus(orig_dir),
            server=api_external_host,
        )

    @app.route("/archive_configure/<dir>", methods=["GET", "POST"])
    def archive_configure(dir):
        start = time.time()
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        orig_dir = dir
        dir = urllib.parse.unquote_plus(dir)

        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)

        w_rights = requests.post(
            archiveAPI + "/ltarchive/write_access", json=json.dumps({"directory": dir})
        ).text
        w_rights = json.loads(w_rights)
        access = False
        for g in groups:
            if g in w_rights:
                access = True

        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)

        print("Orig Directory:" + dir, flush=True)
        print("Directory:" + dir, flush=True)

        response = requests.post(
            archiveAPI + "/ltarchive/use_aiassistant",
            json=json.dumps({"directory": dir}),
        )
        if response.status_code != 200:
            print("ERROR in doing ltarchive/use_aiassistant", response.text)
            return render_template(
                "error.html", text="Could not connect: " + response.reason, theme=theme
            )
        aiassistant = 1 if response.text == "TRUE" else 0
        print("AI Assisntant:", response.text, aiassistant)

        return render_template(
            "archive_configure.html",
            dir=orig_dir,
            theme=theme,
            name=os.path.basename(dir),
            path=os.path.dirname(dir),
            read_access=rights,
            write_access=w_rights,
            aiassistant=aiassistant,
            users=groups,
        )

    @app.route("/archivesession/<dir>", methods=["GET", "POST"])
    def archivesession(dir):
        start = time.time()
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        orig_dir = dir
        dir = urllib.parse.unquote_plus(dir)
        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = False
        for g in groups:
            if g in rights:
                access = True
        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)
        print("Show session for ", dir)

        w_rights = requests.post(
            archiveAPI + "/ltarchive/write_access", json=json.dumps({"directory": dir})
        ).text
        w_rights = json.loads(w_rights)
        w_access = False
        for g in groups:
            if g in w_rights:
                w_access = True

        start2 = time.time()
        res = requests.post(
            archiveAPI + "/ltarchive/languages", json=json.dumps({"directory": dir})
        )
        if res.status_code != 200:
            print("ERROR in doing ltarchive/messages", res.text)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        end2 = time.time()
        print("Time for messages:", end2 - start2)
        langs = res.text
        langs = json.loads(langs)
        text = []
        start2 = time.time()
        response = requests.post(
            archiveAPI + "/ltarchive/mediatype", json=json.dumps({"directory": dir})
        )
        if response.status_code != 200:
            print("ERROR in doing ltarchive/mediatype", response.text)
            return render_template(
                "error.html", text="Could not connect: " + response.reason, theme=theme
            )
        media = response.text
        end2 = time.time()
        print("Time for media type:", end2 - start2)
        print("Content type:", media)

        if len(langs) == 0:
            return render_template(
                "error.html", text="No ASR languages found", theme=theme
            )
        end = time.time()
        print("Time for loading archive:", end - start, flush=True)

        langs_audio = [l for l in langs if l.endswith("Audio")]
        langs_video = [l for l in langs if l.endswith("Video")]
        langs = [
            l for l in langs if not l.endswith("Audio") and not l.endswith("Video")
        ]
        # text = [t for t in text if not t[0].endswith("Audio") and not t[0].endswith("Video")]

        if user is not None:
            hash_key = "user_access" + dir
            db.hincrby(hash_key, user, 1)

            num_accesses = len(db.hgetall(hash_key))
        num_accesses = 0

        response = requests.post(
            archiveAPI + "/ltarchive/use_aiassistant",
            json=json.dumps({"directory": dir}),
        )
        if response.status_code != 200:
            print("ERROR in doing ltarchive/use_aiassistant", response.text)
            return render_template(
                "error.html", text="Could not connect: " + response.reason, theme=theme
            )
        aiassistant = True if response.text == "TRUE" else False

        admin = False
        if "admin" in groups:
            admin = True

        print("Reder template", flush=True)
        return render_template(
            "archivesession.html",
            chat=aiassistant,
            user=user,
            mediatype=media,
            dir=orig_dir,
            mediafile=urllib.parse.quote_plus(orig_dir),
            name=dir.strip("/").split("/")[-1],
            langs=langs,
            add_lang=[],
            default_lang=langs[0],
            theme=theme,
            num_accesses=num_accesses,
            groups=groups,
            langs_audio=langs_audio,
            langs_video=langs_video,
            server=api_external_host,
            admin=admin,
            w_access=w_access,
        )

    @app.route("/delete_recording/<dir>", methods=["GET", "POST"])
    def delete_recording(dir):
        start = time.time()
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        orig_dir = dir
        dir = urllib.parse.unquote_plus(dir)
        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = False
        for g in groups:
            if g in rights:
                access = True
        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)

        print("Delete recording:" + dir, flush=True)
        res = requests.post(
            archiveAPI + "/ltarchive/delete_recording",
            json=json.dumps({"directory": dir}),
        )
        if res.status_code != 200:
            print("ERROR in doing ltarchive/meta", res.text)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        path = os.path.dirname(dir)
        return archive(urllib.parse.quote_plus(path))

    @app.route("/archivesession_configure/<dir>", methods=["GET", "POST"])
    def archivesession_configure(dir):
        start = time.time()
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        orig_dir = dir
        dir = urllib.parse.unquote_plus(dir)

        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)

        w_rights = requests.post(
            archiveAPI + "/ltarchive/write_access", json=json.dumps({"directory": dir})
        ).text
        w_rights = json.loads(w_rights)
        access = False
        for g in groups:
            if g in w_rights:
                access = True

        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)

        print("Orig Directory:" + dir, flush=True)
        print("Directory:" + dir, flush=True)
        res = requests.post(
            archiveAPI + "/ltarchive/meta", json=json.dumps({"directory": dir})
        )
        if res.status_code != 200:
            print("ERROR in doing ltarchive/meta", res.text)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        meta = json.loads(res.text)
        print("Meta:", meta, flush=True)
        if "title" not in meta:
            meta["title"] = ""
        if "event" not in meta:
            meta["event"] = ""
        if "presenter" not in meta:
            meta["presenter"] = ""

        profiles = [[f"profile_{i}", f"Profile {i}"] for i in range(1, 11)]
        profile_names = db.hget("user_to_profile_names", f"{user}")
        if profile_names:
            for name, profile in zip(profile_names.decode().split("|"), profiles):
                profile[1] = name

        resp = requests.post(
            server + "/ltapi/list_available_languages", verify=False, timeout=10
        )
        asr_langs = (
            resp.json()["asr"]
            if resp.status_code == 200 and "asr" in resp.json()
            else []
        )
        # print(asr_langs)
        mt_langs = (
            resp.json()["mt"] if resp.status_code == 200 and "mt" in resp.json() else []
        )
        # print(mt_langs)
        tts_langs = (
            resp.json()["tts"]
            if resp.status_code == 200 and "tts" in resp.json()
            else []
        )
        # print(tts_langs)

        video_langs = (
            tts_langs
            if resp.status_code == 200
            and "lip" in resp.json()
            and len(resp.json()["lip"]) > 0
            else []
        )

        resp = requests.get(server + "/ltapi/list_videos", verify=False)
        if resp.status_code == 200:
            videos = resp.json()
        else:
            print(resp.status_code)
            print(resp.text)
            videos = []

        return render_template(
            "archivesession_configure.html",
            dir=orig_dir,
            profiles=profiles,
            theme=theme,
            topic=meta["title"],
            date=meta["event"],
            speaker=meta["presenter"],
            name=os.path.basename(dir),
            path=os.path.dirname(dir),
            asr_langs=asr_langs,
            mt_langs=mt_langs,
            video_langs=video_langs,
            videos=videos,
            tts_langs=tts_langs,
            availability=getAvailability(),
            mediafile=dir,
            presentation_mode=presentation_mode,
            read_access=rights,
            write_access=w_rights,
            users=groups,
        )

    @app.route("/archivesession_access/", methods=["GET", "POST"])
    def archivesession_access():

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)

        data = request.get_json()
        print("Get for access change:", data)

        dir = urllib.parse.unquote_plus(data["dir"])

        w_rights = requests.post(
            archiveAPI + "/ltarchive/write_access", json=json.dumps({"directory": dir})
        ).text
        w_rights = json.loads(w_rights)
        access = False
        for g in groups:
            if g in w_rights:
                access = True

        if not access:
            print("No right to set access")
            return jsonify(error="Access Not Allowed for dir" + dir), 405

        req = requests.post(
            archiveAPI + "/ltarchive/change_access",
            json=json.dumps(
                {
                    "directory": dir,
                    "readAccess": data["readAccess"],
                    "writeAccess": data["writeAccess"],
                }
            ),
        )
        if req.status_code != 200:
            print("Could not to set access")
            return jsonify(error="Could not set access rights" + req.reason), 405

        resp = jsonify(success=True)
        return resp

    @app.route("/archivesession_setAIAssistant/", methods=["GET", "POST"])
    def archivesession_setAIAssistant():

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)

        data = request.get_json()
        print("Get for access change:", data)

        dir = urllib.parse.unquote_plus(data["dir"])

        w_rights = requests.post(
            archiveAPI + "/ltarchive/write_access", json=json.dumps({"directory": dir})
        ).text
        w_rights = json.loads(w_rights)
        access = False
        for g in groups:
            if g in w_rights:
                access = True

        if not access:
            print("No right to set access")
            return jsonify(error="Access Not Allowed for dir" + dir), 405

        print("Change AI assistant to", data["aiassistant"])
        req = requests.post(
            archiveAPI + "/ltarchive/setAIAssistant",
            json=json.dumps({"directory": dir, "aiassistant": data["aiassistant"]}),
        )
        if req.status_code != 200:
            print("Could not to set access")
            return jsonify(error="Could not set access rights" + req.reason), 405

        resp = jsonify(success=True)
        return resp

    @app.route("/archive_messages/<dir>", methods=["GET", "POST"])
    def archive_messages(dir):
        print(dir)
        print("archive messages, line 1905 in __init__.py")
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        dir = urllib.parse.unquote_plus(dir)
        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = False
        for g in groups:
            if g in rights:
                access = True
        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)

        # Get the 'page' and 'limit' parameters from the request
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 100))

        print("Page:", page, "Limit:", limit)

        res = requests.post(
            archiveAPI
            + "/ltarchive/messages?page="
            + str(page)
            + "&limit="
            + str(limit),
            json=json.dumps({"directory": dir}),
        )
        print("resonse", res.content)
        if res.status_code != 200:
            print("ERROR in doing ltarchive/messages", res.text)
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )

        response = Response(res.content)
        response.headers["Content-Type"] = res.headers["Content-Type"]
        return response

    @app.route("/archivemediafile/<dir>/<filename>", methods=["GET", "POST"])
    def archivemedia_file(dir, filename):
        return handle_archive_media(dir, filename=filename)

    @app.route("/archivemedia/<dir>", methods=["GET", "POST"])
    def archivemedia(dir):
        return handle_archive_media(dir)

    def parse_range_header(range_header, file_size):
        """
        Parse the Range header from the request.

        Args:
            range_header (str): The Range header value, e.g., "bytes=0-499".
            file_size (int): The total size of the file in bytes.

        Returns:
            tuple: (start, end) byte positions.
        """
        if not range_header or not range_header.startswith("bytes="):
            return 0, file_size  # Serve the entire file if no valid range is specified.

        # Extract the byte range
        range_values = range_header.replace("bytes=", "").split("-")
        try:
            start = int(range_values[0]) if range_values[0] else 0
            end = (
                int(range_values[1])
                if len(range_values) > 1 and range_values[1]
                else file_size - 1
            )
        except ValueError:
            return 0, file_size  # Fallback to entire file if parsing fails.

        # Ensure range is within bounds
        start = max(0, start)
        end = min(file_size - 1, end)

        if start > end:
            start, end = 0, file_size  # Serve entire file if range is invalid.

        return start, end + 1  # End is inclusive, so add 1 for slicing.

    def handle_archive_media(dir, filename=None):
        print(dir, flush=True)
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        dir = urllib.parse.unquote_plus(dir)

        # Check user access
        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = any(g in rights for g in groups)

        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)

        # Handle MP4 files specifically for range requests
        data = {"directory": dir}
        if filename:
            data["filename"] = filename
        data = json.dumps(data)

        req = requests.post(
            archiveAPI + "/ltarchive/media", json=data, allow_redirects=False
        )

        if req.status_code == 302:
            print("Location:", req.headers["Location"])
            return redirect(req.headers["Location"], code=302)

        if req.status_code != 200:
            print("ERROR in doing ltarchive/media", req.text)
            return render_template(
                "error.html", text="Could not connect: " + req.reason, theme=theme
            )

        content_type = req.headers.get("Content-Type", "")
        if "video/mp4" in content_type:  # Handle MP4 files with Range support
            range_header = request.headers.get("Range", None)
            if range_header:
                start, end = parse_range_header(range_header, len(req.content))
                response = Response(req.content[start:end], 206)
                response.headers["Content-Range"] = (
                    f"bytes {start}-{end-1}/{len(req.content)}"
                )
                response.headers["Accept-Ranges"] = "bytes"
            else:
                response = Response(req.content)
            response.headers["Content-Type"] = content_type
            response.headers["Content-Disposition"] = req.headers.get(
                "Content-Disposition", ""
            )
            return response
        else:  # Default handling for non-MP4 files
            response = Response(req.content)
            response.headers["Content-Type"] = content_type
            response.headers["Content-Disposition"] = req.headers.get(
                "Content-Disposition", ""
            )
            return response

    @app.route("/thumb/<dir>", methods=["GET", "POST"])
    def thumb(dir):
        print(dir)
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        dir = urllib.parse.unquote_plus(dir)
        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = False
        for g in groups:
            if g in rights:
                access = True
        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)
        req = requests.post(
            archiveAPI + "/ltarchive/thumb", json=json.dumps({"directory": dir})
        )
        if req.status_code != 200:
            print("ERROR in doing ltarchive/media", req.text)
            return render_template(
                "error.html", text="Could not connect: " + req.reason, theme=theme
            )
        response = Response(req.content)
        response.headers["Content-Type"] = req.headers["Content-Type"]
        response.headers["Content-Disposition"] = req.headers["Content-Disposition"]
        return response

    @app.route("/archivemedia/<dir>/vtt/<lang>", methods=["GET", "POST"])
    def vtt(dir, lang):
        print(dir)
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        dir = urllib.parse.unquote_plus(dir)
        rights = requests.post(
            archiveAPI + "/ltarchive/access", json=json.dumps({"directory": dir})
        ).text
        rights = json.loads(rights)
        access = False
        for g in groups:
            if g in rights:
                access = True
        if not access:
            print("No access to Archive")
            return render_template("error.html", text="Permission denied", theme=theme)
        req = requests.post(
            archiveAPI + "/ltarchive/vtt",
            json=json.dumps({"directory": dir, "language": lang}),
        )
        if req.status_code != 200:
            print("ERROR in doing ltarchive/vtt", req.text)
            return render_template(
                "error.html", text="Could not connect: " + req.reason, theme=theme
            )
        response = Response(req.content)
        response.headers["Content-Type"] = req.headers["Content-Type"]
        response.headers["Content-Disposition"] = req.headers["Content-Disposition"]
        return response

    @app.route("/legals", methods=["GET", "POST"])
    def legals():
        return render_template("themes/" + theme + "/html/legals.html", theme=theme)

    @app.route("/terms", methods=["GET", "POST"])
    def terms():
        return render_template("themes/" + theme + "/html/terms.html", theme=theme)

    @app.route("/about", methods=["GET", "POST"])
    def about():
        return render_template("themes/" + theme + "/html/about.html", theme=theme)

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        return render_template("themes/" + theme + "/html/contact.html", theme=theme)

    @app.route("/upload_lecture", methods=["GET", "POST"])
    def upload_lecture():

        print("upload lecture", flush=True)

        if request.method == "GET":
            request.form = request.args

        print(request.form, flush=True)

        archivepath = request.form.get("path") + "/" + request.form.get("name")
        availability = request.form["availability"]
        lang = request.form.getlist("language")

        user = request.headers.get("X-Forwarded-User", None)

        groups = getUserGroups(user)
        if "presenter" not in groups:
            print(user + " has no right to create session")
            return render_template("error.html", text="Permission denied", theme=theme)

        other_speakers = request.form.getlist("otherSpeakers")

        audio_samples = []
        if user is not None:
            audio_samples.append(db.hget("user_to_audiosample", user))
        audio_samples += [db.hget("user_to_audiosample", s) for s in other_speakers]
        audio_samples = [
            a.decode("utf-8") if a is not None else "None" for a in audio_samples
        ]

        if "smartChaptering" in request.form:
            print("smartChaptering:", request.form["smartChaptering"])
            if request.form["smartChaptering"] == "streaming_simple":
                textseg_prop = {"method": "streaming_simple"}
            elif request.form["smartChaptering"] == "online_dynamic":
                textseg_prop = {"method": "online_model", "dynamic_threshold": True}
            elif request.form["smartChaptering"] == "online_static":
                textseg_prop = {"method": "online_model", "dynamic_threshold": False}
            else:
                textseg_prop = {"method": "offline_model"}
        else:
            textseg_prop = {"method": "streaming_simple"}

        print("textseg_prop", textseg_prop)

        error_correction = (
            request.form["errorCorrection"]
            if "errorCorrection" in request.form
            else "None"
        )
        error_correction_init = (
            request.form["errorCorrectionInit"]
            if "errorCorrectionInit" in request.form
            else None
        )
        compute_hyperarticulation = (
            "compute_hyperarticulation" in request.form
            and request.form["compute_hyperarticulation"].lower() == "true"
        )

        summarize = "summarization" in request.form
        postproduction = request.form.getlist("postproduction")

        use_memory = "memory" in request.form

        mt_langs = request.form.getlist("mtLanguage")
        print("MT_LANGS", mt_langs, flush=True)

        tts_langs = request.form.getlist("audioLanguage")
        print("TTS_LANGS", tts_langs, flush=True)

        if "videoLanguage" in request.form:
            video_lang = request.form["videoLanguage"]
        else:
            video_lang = None

        if "video" in request.form:
            video = request.form["video"]
        else:
            video = None

        if "format" in request.form:
            format = request.form["format"]
        else:
            format = "resending"

        if format == "offline":
            textseg_prop = {"method": "offline_model"}

        mt_prop = {"mode": "SendStable", "version": "offline"}
        asr_prop = {
            "mode": "SendStable",
            "stability_detection": "False",
            "version": "offline",
            "segmenter": "SHAS",
        }

        if "filterNonVerbal" in request.form:
            asr_prop["filter_non_verbal"] = "True"

        ##### check saasr box been clicked or not
        if "saasr" in request.form:
            print("SAASR:", request.form["saasr"])
            saasr = request.form["saasr"]
        else:
            saasr = "0"

        if "aiassistant" in request.form:
            print("AI Assistant:", request.form["aiassistant"])
            aiassistant = True
        else:
            aiassistant = False
        log = "True"
        type = "upload"

        profile_id = request.form.get("profile")

        topic = request.form.get("topicname")
        date = request.form.get("date")
        speaker = request.form.get("speakername")

        print("Path:", archivepath, flush=True)

        if archivepath == "":
            print("ERROR: Archive path is empty")
            return render_template(
                "error.html",
                text="Archive path is not allowed to be empty",
                theme=theme,
            )

        if "videofile" in request.files:
            print("Videofile", request.files["videofile"], flush=True)
            try:
                video_bytes = request.files["videofile"].read()

            except:
                print("ERROR: Could not read video")
                return render_template(
                    "error.html", text="Could not read video", theme=theme
                )
        elif "videofile" in request.form:
            print("Link to video:", request.form["videofile"], flush=True)
            try:
                data = {"directory": request.form["videofile"]}
                data = json.dumps(data)

                req = requests.post(
                    archiveAPI + "/ltarchive/media", json=data, allow_redirects=False
                )
                video_bytes = req.content
            except:
                print("ERROR: Could not read video")
                return render_template(
                    "error.html", text="Could not read video", theme=theme
                )
        else:
            print("ERROR: No video specified")
            return render_template(
                "error.html", text="No video file provided", theme=theme
            )

        print("Video size:", len(video_bytes), flush=True)

        sessionID, streamID = initSession(
            lang,
            mt_prop,
            asr_prop,
            textseg_prop,
            video,
            video_lang,
            mt_langs,
            tts_langs,
            summarize,
            postproduction,
            error_correction,
            error_correction_init,
            log,
            type,
            audio_samples,
            user,
            compute_hyperarticulation,
            use_memory,
            aiassistant,
            speakerdiarization=saasr == "1",
            profile_id=profile_id,
        )

        data = {"controll": "START"}
        data["meta"] = (
            '{"title":"'
            + topic
            + '", "presenter": "'
            + speaker
            + '", "event": "'
            + date
            + '"}'
        )
        data["access"] = availability
        data["directory"] = "/logs/archive/" + archivepath

        if "videofile" in request.form:
            # THIS IS A RERUN, REPLACE LOGS
            data["replace_logs"] = True

        info = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamID + "/append",
            json=json.dumps(data),
            verify=False,
        )
        if info.status_code != 200:
            print("ERROR in append in /create")
            return render_template(
                "error.html", text="Could not connect: " + info.reason, theme=theme
            )

        data = {"b64_enc_audio": base64.b64encode(video_bytes).decode("ascii")}
        res = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamID + "/append",
            json=json.dumps(data),
        )
        if res.status_code != 200:
            print(res.status_code, res.text)
            print("ERROR in sending video")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )
        print("Video successfully sent.")

        data = {"controll": "END"}
        res = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamID + "/append",
            json=json.dumps(data),
        )
        if res.status_code != 200:
            print(res.status_code, res.text)
            print("ERROR in sending END message")
            return render_template(
                "error.html", text="Could not connect: " + res.reason, theme=theme
            )

        return render_template(
            "success.html", text="Lecture video successfully uploaded", theme=theme
        )

    @app.route("/create_dir", methods=["GET", "POST"])
    def create_dir():
        path = request.form.get("newdirpath")
        newdir = request.form.get("newdir")
        availability = request.form["availability"]

        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        access = ["all"] if availability == "public" else ["kitall"]
        print("Create dir", newdir, "in", path)
        answer = requests.post(
            archiveAPI + "/ltarchive/create_dir",
            json=json.dumps(
                {"directory": newdir, "path": path, "groups": groups, "access": access}
            ),
        ).text
        if answer == "OK":
            return archive(urllib.parse.quote_plus(path + "/" + newdir))
        else:
            print("Could not create dir", answer)
            return render_template(
                "error.html", text="Could not create dir", theme=theme
            )

    @app.route("/upload", methods=["GET", "POST"])
    def upload():
        print("Upload video", flush=True)

        video_name = request.form.get("videoname")

        if video_name == "":
            print("ERROR: Video name is empty")
            return render_template(
                "error.html", text="Video name is not allowed to be empty", theme=theme
            )

        print(request.files)
        print(request.form.keys())

        video_bytes = None
        try:
            video_bytes = request.files["videofile"].read()
            if len(video_bytes) == 0:
                print("Got empty video")
                raise RuntimeError
        except Exception as e:
            pass  # print(e)

        try:
            video = base64.b64decode(request.form["videofile"])
            with tempfile.NamedTemporaryFile("rb", suffix=".mp4") as temp:
                name = temp.name

            with tempfile.NamedTemporaryFile("wb", suffix=".webm") as temp:
                temp.write(video)
                temp.flush()
                cmd = "ffmpeg -i " + temp.name + " -r 30 " + name
                res = subprocess.run(cmd.split())
                print(res)
                with open(name, "rb") as temp2:
                    video_bytes = temp2.read()
                os.remove(name)
                print(type(video_bytes), len(video_bytes))
        except Exception as e:
            pass  # print(e)

        if video_bytes is None:
            print("ERROR: Could not read video")
            return render_template(
                "error.html", text="Could not read video", theme=theme
            )

        data = {
            "video_name": video_name,
            "video": base64.b64encode(video_bytes).decode("ascii"),
        }
        res = requests.post(
            server + "/ltapi/upload_video", json=json.dumps(data), verify=False
        )
        if res.status_code != 200:
            print("ERROR: Could upload video to ltapi")
            return render_template(
                "error.html", text="Could not upload video to server", theme=theme
            )

        user = request.headers.get("X-Forwarded-User", None)
        db.hset("user_to_audiosample", user, video_name)

        return render_template(
            "success.html", text="Facedubbing video successfully uploaded", theme=theme
        )

    def add_user(email, username, password, user_id):
        import bcrypt
        import api_pb2
        import api_pb2_grpc
        import grpc

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        pw = api_pb2.Password(
            email=email,
            hash=hashed,
            username=username,
            user_id=user_id,
        )

        with grpc.insecure_channel(dex_api_addr) as channel:
            stub = api_pb2_grpc.DexStub(channel)
            resp = stub.CreatePassword(api_pb2.CreatePasswordReq(password=pw))
            # for pw in stub.ListPasswords(api_pb2.ListPasswordReq()).passwords:
            #    print(pw.email, pw.username, pw.user_id)

        return not resp.already_exists

    @app.route("/stop_sessions", methods=["GET", "POST"])
    def stop_sessions():
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        if not "admin" in groups:
            print("Permission denied for", user)
            return render_template("error.html", text="Permission denied", theme=theme)

        sessions = db.hgetall("active_sessions")
        for k in sessions:
            sessionID = k.decode()
            data = {"controll": "END"}
            res = requests.post(
                server + "/ltapi/" + sessionID + "/0/append", json=json.dumps(data)
            )
            if res.status_code != 200:
                print(res.status_code, res.text)

        sessions = db.hgetall("active_sessions")
        if len(sessions) == 0:
            return render_template(
                "success.html",
                text="All sessions have been stopped successfully.",
                theme=theme,
            )
        else:
            print(sessions)
            return render_template(
                "error.html",
                text="Some error occurred, check the log of the frontend!",
                theme=theme,
            )

    @app.route("/add_lecturer", methods=["GET", "POST"])
    def add_lecturer():
        user = request.headers.get("X-Forwarded-User", None)
        groups = getUserGroups(user)
        if not "admin" in groups:
            print("Permission denied for", user)
            return render_template("error.html", text="Permission denied", theme=theme)

        email = request.form.get("emailadress")
        group = request.form["group"]

        if re.match(r'^[a-z]{2}\d{4}@.*kit\.edu$', email) is not None:
            print("ERROR: Could not add email because ab1234@*kit.edu email was used and not firstname.lastname@*kit.edu!")
            return render_template(
                "error.html", text="Could not add email because ab1234@*kit.edu email was used and not firstname.lastname@*kit.edu!", theme=theme
            )

        print("Adding email", email, "to group", group)

        # Perform the SADD operation
        group_key = "groups:" + group

        # Use the SADD command to add the member to the set
        result = db.sadd(group_key, email.lower())

        # Check the result of the SADD operation
        if result == 0:
            return render_template(
                "success.html",
                text="Email address "
                + email
                + " was already added before, no changes were made",
                theme=theme,
            )
        elif result != 1:
            print("ERROR: Could not add email", result)
            return render_template(
                "error.html", text="Could not add email", theme=theme
            )

        if email.endswith("kit.edu"):
            return render_template(
                "success.html",
                text="Email address " + email + " successfully added to group " + group,
                theme=theme,
            )
        else:
            import uuid
            import secrets
            import string

            username = email.partition("@")[0]
            user_id = str(uuid.uuid4())
            alphabet = string.ascii_letters + string.digits
            password = "".join(
                secrets.choice(alphabet) for i in range(20)
            )  # for a 20-character password

            add_user(email, username, password, user_id)

            return render_template(
                "success.html",
                text="Email address "
                + email
                + " successfully added to group "
                + group
                + ", password is "
                + password
                + "\nThis password will only be shown now. There is no possibility to later see or change it!",
                theme=theme,
            )

    @app.route("/update_memory/<sessionID>/<profileID>", methods=["GET", "POST"])
    def update_memory(sessionID, profileID):
        memory_words = request.json
        memory_words = json.dumps(
            memory_words.split("\n") if len(memory_words) > 0 else None
        )

        data = {"memory_words": memory_words, "time": time.time()}
        print("Sending memory", data, flush=True)

        res = requests.post(
            server + "/ltapi/" + sessionID + "/0/append", json=json.dumps(data)
        )
        if res.status_code != 200:
            print(res.status_code, res.text)
            print("ERROR in sending memory", flush=True)
            return "ERROR", 500

        user = request.headers.get("X-Forwarded-User", None)
        print("User for memory update:", user)
        if user is not None:
            db.hset("user_to_memory_words", user + "_" + profileID, memory_words)

        return "Success"

    @app.route("/last_memory_words/<profileID>", methods=["GET", "POST"])
    def last_memory_words(profileID):
        user = request.headers.get("X-Forwarded-User", None)
        if user is None:
            print(
                "last_memory_words: Could not load last memory words because user is None!"
            )
            return ""

        memory_words = db.hget("user_to_memory_words", user + "_" + profileID)
        if memory_words is None:
            print(
                "last_memory_words: Could not load last memory words because none found in database!"
            )
            return ""
        memory_words = json.loads(memory_words)
        if memory_words is None:
            print(
                "last_memory_words: Could not load last memory words because list is empty!"
            )
            return ""
        return "\n".join(memory_words)

    @app.route("/extract_memory/<sessionID>", methods=["GET", "POST"])
    def extract_memory(sessionID):
        pdf_bytes = request.files["pdffile"].read()

        data = {"pdffile": base64.b64encode(pdf_bytes).decode("ascii")}
        print("Sending pdf", flush=True)

        res = requests.post(
            server + "/ltapi/" + sessionID + "/0/append", json=json.dumps(data)
        )
        if res.status_code != 200:
            print(res.status_code, res.text)
            print("ERROR in sending pdf", flush=True)
            return "ERROR", 500

        return "Success"

    @app.route("/delete_session/<sessionID>", methods=["GET", "POST"])
    def delete_session(sessionID):
        user = request.headers.get("X-Forwarded-User", None)
        if user is None:
            return "ERROR", 500

        res = requests.post(
            archiveAPI + "/ltarchive/delete_session",
            json=json.dumps({"sessionID": sessionID, "user": user}),
        )
        if res.status_code != 200:
            print(res.status_code, res.text)
            print("ERROR in removing ression", flush=True)
            return "ERROR", 500

        return "Success"

    @app.route("/multimodal_assistant/<mediafile>", methods=["GET", "POST"])
    def multimodal_assistant(mediafile):
        print("Mediafile:", mediafile, flush=True)
        directory = urllib.parse.unquote_plus(urllib.parse.unquote_plus(mediafile))
        d = {}
        d["bot"] = "bot"
        d["tts"] = "TRUE"

        res = requests.post(
            server + "/ltapi/start_dialog", json=json.dumps(d), verify=False
        )
        if res.status_code != 200:
            print("ERROR in requesting start dialog: ‚", res.text)
            return render_template(
                "error.html", text="Could not start dialog: " + res.reason, theme=theme
            )
        sessionID, streamID, streamIDText, streamIDCommand = res.text.split()
        print("New Session:", sessionID)
        print("Streams:", streamID, streamIDText, streamIDCommand)

        data = {"controll": "START"}

        info = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamID + "/append",
            json=json.dumps(data),
            verify=False,
        )
        if info.status_code != 200:
            print("ERROR in append in /create")
            return render_template(
                "error.html", text="Could not append 1: " + info.reason, theme=theme
            )

        data["content_directory"] = "/logs/archive/" + directory
        print("Content dir:", "/logs/archive/" + directory, flush=True)

        info = requests.post(
            server + "/ltapi/" + sessionID + "/" + streamIDCommand + "/append",
            json=json.dumps(data),
            verify=False,
        )
        if info.status_code != 200:
            print("ERROR in append in /create")
            return render_template(
                "error.html", text="Could not append 2: " + info.reason, theme=theme
            )

        return render_template(
            "mmAssistant/mmAssistant.html",
            server="/webapi",
            theme=theme,
            session=sessionID,
            streamAudio=streamID,
            streamText=streamIDText,
        )

    return app
