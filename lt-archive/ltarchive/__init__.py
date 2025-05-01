import os

from flask import Flask, request,jsonify
import ujson as json
import io
import tempfile
import subprocess
from flask import make_response
from flask import send_file,redirect
import base64
from qbmediator.Database import get_best_database
import time
import os.path
import re
import datetime
import redis
from glob import glob
import wave
import shutil
import requests
import threading

if "LT_ARCHIVE_DIR" in os.environ:
    dir = os.environ["LT_ARCHIVE_DIR"]
else:
    dir="."
if "REDIS_HOST" in os.environ:
    redis_server = os.environ["REDIS_HOST"]
else:
    redis_server="localhost"
if "REDIS_PORT" in os.environ:
    redis_port = os.environ["REDIS_PORT"]
else:
    redis_port="6379"


if "DIR_PREFIX" in os.environ:
    dirprefix = os.environ["DIR_PREFIX"]
else:
    dirprefix="/data"
    
lang_names = {}

dir_creation_lock = threading.Lock()

def lang_name(lang):
    if lang not in lang_names:
        res = requests.get("http://ltapi:5000/ltapi/langs_to_display_langs", json={"langs":[lang]})
        if res.status_code != 200:
            return lang
        lang_names[lang] = res.json()[0]
    return lang_names[lang]

VERSION="1.33"
WAITING="0.1"

def create_app(test_config=None):
    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    db = get_best_database()(redis_server,redis_port)

    @app.get('/ltarchive/health')
    def health():
        return "healthy", 200

    @app.route('/ltarchive/create_dir', methods=["GET", "POST"])
    def create_dir():
        #Show content of directory
        data = json.loads(request.json);
        new_dir=data["directory"]
        path=data["path"]
        groups=data["groups"]
        access=data["access"]
        if(os.path.isdir(dir+"/"+path)):
            if(checkWriteAccess(dir+"/"+path,groups)):
                if(not os.path.exists(dir+"/"+path+"/"+new_dir)):
                    os.mkdir(dir+"/"+path+"/"+new_dir)
                    with open(dir+"/"+path+"/"+new_dir+"/access.json", "w") as outfile:
                        outfile.write(json.dumps(access))
                    return "OK"
        return "FAIL"

    @app.route('/ltarchive/create_home', methods=["GET", "POST"])
    def create_home():
        #Show content of directory
        data = json.loads(request.json);
        user=data["user"]
        if(not os.path.exists(dir+"/home")):
                os.mkdir(dir+"/home")
                with open(dir+"/home/access.json", "w") as outfile:
                    outfile.write(json.dumps(["admin@example.com"]))
                with open(dir+"/home/write_access.json", "w") as outfile:
                    outfile.write(json.dumps(["admin@example.com"]))
        if(not os.path.exists(dir+"/home/"+user)):
                os.mkdir(dir+"/home/"+user)
                with open(dir+"/home/"+user+"/access.json", "w") as outfile:
                    outfile.write(json.dumps([user]))
                with open(dir+"/home/"+user+"/write_access.json", "w") as outfile:
                    outfile.write(json.dumps([user]))
        return "OK"


    # a simple page that says hello
    @app.route('/ltarchive/ls', methods=["GET", "POST"])
    def ls():
        #Show content of directory
        data = json.loads(request.json);
        subdir=data["directory"]
        groups=data["groups"]
        print("Show content of dir:",subdir," in dir: ",dir)
        print("Groups:",groups)
        result=[]
        if(checkWriteAccess(dir+"/"+subdir,groups)):
            result.append((" Create Directory","createdir","{}",False,"read"))
            result.append((" Upload media","upload","{}",False,"read"))
            result.append((" Record session","record","{}",False,"read"))
            print("Write access")
        if(re.sub(r'([\/\\])\1+', r'\1', subdir) != "/"):
            print(subdir+" Norm:"+re.sub(r'([\/\\])\1+', r'\1', subdir))
            if(checkAccess(dir+"/"+os.path.dirname(subdir),groups)):
                result.append((" Back","dir","{}",False,"read"))
                print("Add partent dir for dir:",subdir)
        for d in os.listdir(dir+"/"+subdir):
            #ignore hidden
            if(os.path.isdir(dir+"/"+subdir+"/"+d) and not d.startswith(".")):
                if(os.path.exists(dir+"/"+subdir+"/"+d+"/sessionGraph")):
                    if(checkAccess(subdir+"/"+d,groups)):
                        if(checkWriteAccess(subdir+"/"+d,groups)):
                            result.append((d,"session",meta(subdir+"/"+d),thumbnail(subdir+"/"+d),"write"))
                        else:
                            result.append((d,"session",meta(subdir+"/"+d),thumbnail(subdir+"/"+d),"read"))
                else:
                    if(checkAccess(subdir+"/"+d,groups)):
                        result.append((d,"dir","{}",False,"read"))




        #print(result)
        return json.dumps(result)


    def checkAccess(d,groups):
        permission = access(d)
        for g in groups:
            if g in permission:
                return True;
        
        return False

    def checkWriteAccess(d,groups):
        permission = write_access(d)
        for g in groups:
            if g in permission:
                return True;
        return False

    
    def checkDir(subdir):

        d=os.path.abspath(dir+"/"+subdir).removeprefix(dirprefix)
        status = db.get("LTArchive_"+d+"_version")
        if status == None:
            status = "-1"
        else:
            status = status.decode("utf-8")
        print("Current status:",d,status)
        if status != VERSION:

            with dir_creation_lock:
                status = db.get("LTArchive_"+d+"_version")
                if status == None:
                    status = "-1"
                else:
                    status = status.decode("utf-8")
                if (status == VERSION):
                    return
                createArchive(subdir)
                db.set("LTArchive_"+d+"_version",VERSION)
                print("New Status:",status,"of","LTArchive_"+d+"_version")

    @app.route('/ltarchive/messages', methods=["GET", "POST"])
    def messages():
        # Get the 'page' and 'limit' parameters from the request
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 100))

        print("Page:",page,"Limit:",limit)
        
        start = (page - 1) * limit
        end = start + limit

        
        #Show content of directory
        data = json.loads(request.json);
        subdir=data["directory"]
        print("Checking messages for",subdir)
        if(not os.path.isdir(dir+"/"+subdir)):
            print("Directory",subdir,"does not exits")
            return "Directory not found", 400
        checkDir(subdir)

        with open(dir+"/"+subdir+"/messages.json", 'r') as openfile:
            result = json.load(openfile)

        result = jsonify({
            'data': result[start:end],
            'total': len(result)
        })
        print("Sending messages")
        return result


        #return json.dumps(result)
        #    result = openfile.read()
            
        #return result


    @app.route('/ltarchive/languages', methods=["GET", "POST"])
    def languages():
        
        #Show content of directory
        data = json.loads(request.json);
        subdir=data["directory"]
        print("Checking messages for",subdir)
        if(not os.path.isdir(dir+"/"+subdir)):
            print("Directory",subdir,"does not exits")
            return "Directory not found", 400
        checkDir(subdir)

        with open(dir+"/"+subdir+"/languages.json", 'r') as openfile:
       #     result = json.load(openfile)
        #return json.dumps(result)
            result = openfile.read()
        return result

    
    def createArchive(subdir):
        worker = storeMessages(subdir)
        storeMedia(subdir)
        storeAudioVideo(subdir, worker)
        #propagateAccess(subdir)


    #def propagateAccess(subdir):
    #    #partens need to have the same access
    #    print ("Propagate access for subdir")
    #    parent=os.path.dirname(subdir)
    #    if(parent != subdir):
    #        print("Propagate "+subdir+" to "+parent)
    #        a1 = access(subdir)
    #        print("A1:",a1)
    #        a2 = access(parent)
    #        print("A2:",a2)
    #        union = list(set(a1) | set(a2))
    #        print("Union:",union)
    #        if(len(a2) != len(union)):
    #            print("Updating access for "+parent+" to ",union)
    #            with open(dir+"/"+parent+"/access.json", "w") as outfile:
    #                outfile.write(json.dumps(union))
    #            propagateAccess(parent)


        
    def storeMessages(subdir):
        print("Store media")
        msg=[]
        for d in os.listdir(dir+"/"+subdir):
            if len(d.split(":")) == 2 and not d.endswith(".wav") and not d.endswith(".mp4"):
                print("Checking:",d)
                if(os.path.isfile(dir+"/"+subdir+"/"+d)):
                    f = open(dir+"/"+subdir+"/"+d)
                    msg += f.readlines()
                    f.close()

        g = graph(subdir)
        comp = json.loads(g[0])[1:]
        gr = json.loads(g[0])[0]["graph"]
        languages=[]
        worker={}
        result={}
        store_messages= []
        for c in comp:
            if "display_language" in c and c["display_language"] !=None and "api" in gr[c["component"]]:
                lang = lang_name(c["display_language"])
                if lang not in languages:
                    languages.append(lang)
                    result[lang] = []
                worker[c["component"]] = lang
        #print (lang_names)
        #print (worker)
        for m in msg:
            d = json.loads(m)
            if(d["sender"] in worker and "seq" in d and (d["sender"].startswith("asr") or d["sender"].startswith("mt"))):
                if("unstable" not in d or not d["unstable"]):
                    result[worker[d["sender"]]].append((d["seq"],d["start"],d["end"]))
            if(d["sender"] in worker):
                if("unstable" not in d or not d["unstable"]):
                    if "start" in d:
                        store_messages.append((float(d["start"]),worker[d["sender"]],m.strip()))
                    else:
                        store_messages.append((0,worker[d["sender"]],m.strip()))
                        
        store_messages = [[s[1],s[2]] for s in sorted(store_messages)]
        print("Number of language in result:",len(result))
        for lang in result:
            print("Generate VTT for ",lang)
            generateVTT(lang,subdir,result[lang]);
            
        with open(dir+"/"+subdir+"/messages.json", "w") as outfile:
            json.dump(store_messages, outfile)

        with open(dir+"/"+subdir+"/languages.json", "w") as outfile:
            json.dump(languages, outfile)

        return worker

    def generateVTT(lang,subdir,messages):
        print(lang)
        r = []
        outfile = open(dir+"/"+subdir+"/"+lang+".vtt", "w")
        outfile.write("WEBVTT\n\n")
        for msg in messages:
            s = re.sub(r'<[^>]*>', '',msg[0])
            s = s.strip()
            words = s.split()
            start = 0
            duration = float(msg[2])- float(msg[1])
            for w in words:
                ws=float(msg[1])+start/len(s)*duration
                start+=len(w)+1
                we=float(msg[1])+start/len(s)*duration
                
                r.append((w,ws,we))
                if(w[-1] in [".","!","?",";"]):
                    processSentence(r,outfile)
                    r=[]
        outfile.close()

    def processSentence(sentence,outfile):
        segment = ""
        firstsegment=""
        start = sentence[0][1]
        end = sentence[0][2]
        for w in sentence:
            if(len(segment) < 42):
                segment += " "+w[0]
                end = w[2]
            elif(len(firstsegment) == 0):
                firstsegment=segment
                segment=w[0]
                end = w[2]
            else:
                dt= datetime.timedelta(seconds=start)
                outfile.write("{}:{:02d}:{:02d}.{:03d}".format(dt.seconds // 3600, dt.seconds // 60 % 60,dt.seconds % 60, dt.microseconds//1000))
                outfile.write(" --> ")
                dt= datetime.timedelta(seconds=end)
                outfile.write("{}:{:02d}:{:02d}.{:03d}\n".format(dt.seconds // 3600, dt.seconds // 60 % 60,dt.seconds % 60, dt.microseconds//1000))
                outfile.write(firstsegment.strip()+"\n")
                outfile.write(segment.strip()+"\n")
                outfile.write("\n")
                firstsegment=""
                segment = w[0]
                start = w[1]
                end = w[2]
        if len(segment) > 0:
            dt= datetime.timedelta(seconds=start)
            outfile.write("{}:{:02d}:{:02d}.{:03d}".format(dt.seconds // 3600, dt.seconds // 60 % 60,dt.seconds % 60, dt.microseconds//1000))
            outfile.write(" --> ")
            dt= datetime.timedelta(seconds=end)
            outfile.write("{}:{:02d}:{:02d}.{:03d}\n".format(dt.seconds // 3600, dt.seconds // 60 % 60,dt.seconds % 60, dt.microseconds//1000))
            if(len(firstsegment) > 0):
                outfile.write(firstsegment.strip()+"\n")
            outfile.write(segment.strip()+"\n")
            outfile.write("\n")
            

        
    @app.route('/ltarchive/mediatype', methods=["GET", "POST"])
    def mediatype():
        start = time.time()
        data = json.loads(request.json);
        subdir=data["directory"]
        print("Show messages of dir:",subdir," in dir: ",dir)
        checkDir(subdir)
        end = time.time()
        print("Time for mediatype:",end - start,flush=True)
        d=os.path.abspath(dir+"/"+subdir).removeprefix(dirprefix)
        type = db.get("LTArchive_"+d+"_mediatype").decode("utf-8")
        if(type == "link"):
            return "video/mp4"
        else:
            return type
                    
    @app.route('/ltarchive/media', methods=["GET", "POST"])
    def media():


        data = json.loads(request.json);
        subdir=data["directory"]
        print("Show media of dir:",subdir," in dir: ",dir,flush=True)
        checkDir(subdir)

        d=os.path.abspath(dir+"/"+subdir).removeprefix(dirprefix)
        type = db.get("LTArchive_"+d+"_mediatype").decode("utf-8")
        print("Type:",type)
        if(type == "audio/wav"):
            filename = "sound.wav" if not "filename" in data else data["filename"]
            return send_file(dir+"/"+subdir+'/'+filename)
        elif(type == "video/mp4"):
            filename = "video.mp4" if not "filename" in data else data["filename"]
            return send_file(dir+"/"+subdir+'/'+filename)
        elif(type == "link"):
            return redirect(db.get("LTArchive_"+d+"_link").decode("utf-8"), code=302)
        
#            with open(dir+"/"+subdir+"/sound.wav", "wb") as binary_file:
#                # Write bytes to file
#                binary_file.write(base64.b64decode(file.encode("ascii")))
#                #buf = io.BytesIO(base64.b64decode(file.encode("ascii")))
#            
#            response = make_response(buf.getvalue())
#            buf.close()
#            response.headers['Content-Type'] = 'audio/wav'
#            response.headers['Content-Disposition'] = 'attachment; filename=sound.wav'
#            return response
#

#        buf = io.BytesIO(base64.b64decode(file.encode("ascii")))
#
#            response = make_response(buf.getvalue())
#            buf.close()
#            response.headers['Content-Type'] = 'video/mp4'
#            response.headers['Content-Disposition'] = 'attachment; filename=video.mp4'
#            return response


    @app.route('/ltarchive/delete_recording', methods=["GET", "POST"])
    def delete_recording():
        data = json.loads(request.json);
        subdir=data["directory"]
        checkDir(subdir)
        print("Deleete recording:",subdir)
        print("Dir exists:",os.path.exists(dir+"/"+subdir))
        print("Contains subdir:",contains_subdirectory(dir+"/"+subdir))
        if(os.path.exists(dir+"/"+subdir) and not contains_subdirectory(dir+"/"+subdir)):
            shutil.rmtree(dir+"/"+subdir)
        return "OK"


    def contains_subdirectory(directory):
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                return True
        return False

    @app.route('/ltarchive/meta', methods=["GET", "POST"])
    def meta():
        data = json.loads(request.json);
        subdir=data["directory"]
        checkDir(subdir)
        return meta(subdir)


    def meta(subdir):
        if not os.path.exists(dir+"/"+subdir+"/meta.json"):
            return "{}";
        with open(dir+"/"+subdir+"/meta.json", 'r') as openfile:
            result = json.load(openfile)
        return json.dumps(result)

    def thumbnail(subdir):
        return os.path.exists(dir+"/"+subdir+"/thumb.png")



    @app.route('/ltarchive/access', methods=["GET", "POST"])
    def access():
        data = json.loads(request.json);
        subdir=data["directory"]
        return json.dumps(access(subdir))

    def access(subdir):
        print("Check if:",dir+"/"+subdir+"/access.json")
        if not os.path.exists(dir+"/"+subdir+"/access.json"):
            print("No")
            return ["kitall"];
        print ("Yes")
        with open(dir+"/"+subdir+"/access.json", 'r') as openfile:
            result = json.load(openfile)
            print(result)
            result = [r if r!="kit" else "kitall" for r in result]
            print (result)
        print("Result")
        return result

    @app.route('/ltarchive/write_access', methods=["GET", "POST"])
    def write_access():
        data = json.loads(request.json);
        subdir=data["directory"]
        return json.dumps(write_access(subdir))

    def write_access(subdir):
        print("Check if:",dir+"/"+subdir+"/write_access.json")
        if not os.path.exists(dir+"/"+subdir+"/write_access.json"):
            return ["presenter"];
        with open(dir+"/"+subdir+"/write_access.json", 'r') as openfile:
            result = json.load(openfile)
            result = [r if r!="kit" else "kitall" for r in result]
        return result
 

    @app.route('/ltarchive/change_access', methods=["GET", "POST"])
    def change_access():
        data = json.loads(request.json);
        subdir=data["directory"]
        readAccess = data["readAccess"]
        writeAccess = data["writeAccess"]
        print("Set read access: ",readAccess)
        print("Set write access: ",writeAccess)
        with open(dir+"/"+subdir+"/access.json", "w") as outfile:
            outfile.write(json.dumps(readAccess))
        with open(dir+"/"+subdir+"/write_access.json", "w") as outfile:
            outfile.write(json.dumps(writeAccess))

        resp = jsonify(success=True)
        return resp

    @app.route('/ltarchive/setAIAssistant', methods=["GET", "POST"])
    def setAIAssistant():
        data = json.loads(request.json);
        subdir=data["directory"]
        aiassistant = data["aiassistant"]

        print("Set aiassitant for ",subdir,aiassistant)
        if(aiassistant and not os.path.exists(dir+"/"+subdir+"/bot")):    
            with open(dir+"/"+subdir+"/bot", "w") as outfile:
                outfile.write("")
        elif(not aiassistant and os.path.exists(dir+"/"+subdir+"/bot")):
            os.remove(dir+"/"+subdir+"/bot")

        resp = jsonify(success=True)
        return resp


    @app.route('/ltarchive/thumb', methods=["GET", "POST"])
    def thumb():

        data = json.loads(request.json);
        subdir=data["directory"]
        print("Show messages of dir:",subdir," in dir: ",dir)
        checkDir(subdir)
        return send_file(dir+"/"+subdir+'/thumb.png')

        
    @app.route('/ltarchive/vtt', methods=["GET", "POST"])
    def vtt():


        data = json.loads(request.json);
        subdir=data["directory"]
        lang=data["language"]
        print("Show messages of dir:",subdir," in dir: ",dir)
        checkDir(subdir)

        d=os.path.abspath(dir+"/"+subdir)
        print("Return vtt file:",d+"/"+lang+".vtt")
        f = open(d+"/"+lang+".vtt")
        print(f.readlines()[:5])
        f.close()
        return send_file(d+"/"+lang+".vtt")



    @app.route('/ltarchive/use_aiassistant', methods=["GET", "POST"])
    def use_aiassistant():


        data = json.loads(request.json);
        subdir=data["directory"]
        print("Show messages of dir:",subdir," in dir: ",dir)

        d=os.path.abspath(dir+"/"+subdir)
        #update dir for sessions
        if(os.path.exists(d+"/sessionGraph")):
            checkDir(subdir)

        print("Check ai_assistant:",d+"/bot")
        if os.path.exists(d+"/bot"):
            print("TRUE")
            return "TRUE"
        else:
            print("FALSE")
            return "FALSE"
    
    def storeMedia(subdir):
        msg=[]

        if(os.path.isfile(dir+"/"+subdir+"/user:0")):
            f = open(dir+"/"+subdir+"/user:0")
            msg += f.readlines()
            f.close()
            print("read file")
        else:
            print("Could not open file:"+dir+"/"+subdir+"/user:0")

        file=""
        type=""
        d=os.path.abspath(dir+"/"+subdir).removeprefix(dirprefix)
        access = []
        write_access = []
        for m in msg:
            data = json.loads(m)
            if("b64_enc_pcm_s16le" in data):
                file += data["b64_enc_pcm_s16le"]
                type="wav"
            elif("b64_enc_audio" in data):
                file += data["b64_enc_audio"]
                type="video"
            elif("url" in data):
                type="link"
                db.set("LTArchive_"+d+"_link",data["url"])
            if("meta" in data):
                with open(dir+"/"+subdir+"/meta.json", "w") as outfile:
                    outfile.write(data["meta"])
            if("access" in data):
                if(data["access"] == "public"):
                    data["access"] = "all";
                access.append(data["access"])
            if ("host" in data):
                access.append(data["host"])
                write_access.append(data["host"])


        if not os.path.exists(dir+"/"+subdir+"/access.json"):
            with open(dir+"/"+subdir+"/access.json", "w") as outfile:
                outfile.write(json.dumps(access))
        if not os.path.exists(dir+"/"+subdir+"/write_access.json") and len(write_access) > 0:
            with open(dir+"/"+subdir+"/write_access.json", "w") as outfile:
                outfile.write(json.dumps(write_access))
        print("Length of file in string",len(file))
        print("Type",type, flush=True)

        if(type == "wav"):
            
            with open(dir+"/"+subdir+"/sound.wav", "wb") as binary_file:
                # Write bytes to file
                binary_file.write(base64.b64decode(file.encode("ascii")))
                #buf = io.BytesIO(base64.b64decode(file.encode("ascii")))
            db.set("LTArchive_"+d+"_mediatype","audio/wav")
        elif(type == "video"):
            with open(dir+"/"+subdir+"/video.mp4", "wb") as binary_file:
                # Write bytes to file
                binary_file.write(base64.b64decode(file.encode("ascii")))
                #buf = io.BytesIO(base64.b64decode(file.encode("ascii")))
            db.set("LTArchive_"+d+"_mediatype","video/mp4")
        elif(type == "link"):
            db.set("LTArchive_"+d+"_mediatype","link")            

        if(type == "video" or type == "link"):
            if (type == "video"):
                f = dir+"/"+subdir+"/video.mp4"
            else:
                f = db.get("LTArchive_"+d+"_link").decode("utf-8")
            process = subprocess.run(("ffmpeg -y -i "+f+' -vf thumbnail -frames:v 1 '+dir+"/"+subdir+"/thumb.png").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            if process.returncode != 0:
                print("ERROR: Could not create thumb")
            
    def concatenate_videos(video_bytes_list, output_path):
        # Create temporary directory to store individual video files
        temp_dir = tempfile.mkdtemp()

        try:
            # Write each video byte data to a temporary file
            input_files = []
            for i, video_bytes in enumerate(video_bytes_list):
                input_file = os.path.join(temp_dir, f'input_{i}.mp4')
                with open(input_file, 'wb') as f:
                    f.write(video_bytes)
                input_files.append(input_file)

            # Create a file list to be used as input for FFmpeg
            file_list_path = os.path.join(temp_dir, 'file_list.txt')
            with open(file_list_path, 'w') as file_list:
                for input_file in input_files:
                    file_list.write(f"file '{input_file}'\n")

            # Run FFmpeg command to concatenate videos
            ffmpeg_cmd = f"ffmpeg -y -f concat -safe 0 -i {file_list_path} \"{output_path[:-3]}mov\""
            subprocess.run(ffmpeg_cmd, shell=True) # Hack: First encode as mov and then as mp4 (otherwise somehow the video is not played correctly in the frontend)

            ffmpeg_cmd = f"ffmpeg -y -i \"{output_path[:-3]}mov\" \"{output_path}\""
            subprocess.run(ffmpeg_cmd, shell=True)
        finally:
            # Clean up temporary files and directory
            for input_file in input_files:
                os.remove(input_file)
            os.remove(file_list_path)
            os.remove(f"\"{output_path[:-3]}mov\"")
            os.rmdir(temp_dir)

    def storeAudioVideo(subdir, worker_dict):
        for worker in glob(dir+"/"+subdir+"/tts:*"):
            if worker.endswith(".wav") or worker.endswith(".mp4"):
                continue
            worker_s = worker.split("/")
            w = "/".join(worker_s[:-1])
            w_ = worker_s[-1]
            if w_ not in worker_dict:
                print("WARNING: Did not find",w_,"in",worker_dict)
                continue
            with wave.open(w+"/"+worker_dict[w_]+".wav", 'w') as wav_file:
                # Set WAV file parameters
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                for m in open(worker):
                    data = json.loads(m)
                    if "b64_enc_pcm_s16le" in data:
                        if "linkedData" in data and data["linkedData"]:
                            pcm_data = base64.b64decode(db.get(data["b64_enc_pcm_s16le"]))
                        else:
                            pcm_data = base64.b64decode(data["b64_enc_pcm_s16le"])
                        wav_file.writeframes(pcm_data)
        for worker in glob(dir+"/"+subdir+"/lip:*"):
            if worker.endswith(".wav") or worker.endswith(".mp4"):
                continue
            worker_s = worker.split("/")
            w = "/".join(worker_s[:-1])
            w_ = worker_s[-1]
            if w_ not in worker_dict:
                print("WARNING: Did not find",w_,"in",worker_dict,"2")
                continue
            video_bytes_list = []
            for m in open(worker):
                data = json.loads(m)
                if "b64_enc_video" in data:
                    try:
                        if "linkedData" in data and data["linkedData"]:
                            video = base64.b64decode(db.get(data["b64_enc_video"]))
                        else:
                            video = base64.b64decode(data["b64_enc_video"])
                    except Exception as e:
                        print(e)
                        continue
                    if len(video) == 0:
                        continue
                    video_bytes_list.append(video)

            if len(video_bytes_list) > 0:
                print("Concatenating generated videos")
                concatenate_videos(video_bytes_list,w+"/"+worker_dict[w_]+".mp4")
        print("DONE")

    def graph(subdir):
        print("Show messages of dir:",subdir," in dir: ",dir)
        result=[]
        if(os.path.isfile(dir+"/"+subdir+"/sessionGraph")):
                f = open(dir+"/"+subdir+"/sessionGraph")
                result += f.readlines()
                f.close()

        return result

    @app.route('/ltarchive/delete_session', methods=["GET", "POST"])
    def delete_session():
        data = json.loads(request.json);
        sessionID = data["sessionID"]
        user = data["user"]

        dir = None
        for file in glob(f"/data/home/{user}/*/user:0"):
            for line in open(file):
                data = json.loads(line.strip())
                if "session" in data and data["session"] == sessionID:
                    dir = file[:-len("/user:0")]
                break

        if dir is None:
            message = f"Could not find sessionID {sessionID} for user {user}"
            print(message)
            return message, 500

        time.sleep(10) # Wait for all messages to have been processed
        shutil.rmtree(dir)

        return "Success"

    return app
