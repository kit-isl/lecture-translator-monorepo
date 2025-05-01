#!/usr/bin/env python3
import argparse
import json
import jsons

import base64
import requests
import subprocess
import tempfile
import os
import time

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_from_db, set_in_db, delete_in_db
from qbmediator.Database import get_best_database
from qbmediator.Interface import MediatorInterface, ActiveSessionStorage

import imageio.v3 as iio

property_to_default = {
    "fg_server_default": "http://192.168.0.67:5052/fg/infer/",
    "version": "online",
    "language": "en",
    "display_language": "en_video",
    "facedubbing_video": "None",
}

def get_fps(video):
    meta = iio.immeta(video, extension=".mp4")
    return str(meta["fps"])

def run_facedetection(server, video, fps):
    try:
        res = requests.post(server, files={"video":video, "fps": str(fps)})
        if res.status_code != 200:
            print(res.text)
            raise requests.exceptions.ConnectionError
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not get id of video from server",server)
        return
    try:
        video_id = res.json()["video_id"]
    except:
        video_id = res.text
    print("Got video_id",video_id)
    return video_id

class OfflineStreamingLipSync(MediatorInterface):
    def __init__(self, name, session, tag, lip_sync_server, facedubbing_video="None", *args, **kwargs):
        super().__init__(name, session, tag, *args, **kwargs)

        self.lip_sync_server = lip_sync_server
        self.video_name = facedubbing_video
        self.start_frame = "0"

        self.video = None
        self.video_id = None
        self.fps = None

    def read_video(self, stream=None):
        if stream is None:
            if len(self.streams)==0:
                print("WARNING: Did not yet get audio to return for session", self.session)
                return bytes()
            stream = self.streams[0]

        num_chunks = int(get_from_db(self.db, self.name, self.session+"/"+self.tag, "VNumChunks").decode("utf-8"))
        index_next_chunk = int(get_from_db(self.db, self.name, self.session+"/"+self.tag, "VIndexNextChunk").decode("utf-8"))

        if index_next_chunk >= num_chunks:
            return None

        next_chunk = get_from_db(self.db, self.name, self.session+"/"+self.tag, "VChunk"+str(index_next_chunk))
        delete_in_db(self.db, self.name, self.session+"/"+self.tag, "VChunk"+str(index_next_chunk))

        set_in_db(self.db, self.name, self.session+"/"+self.tag, "VIndexNextChunk", index_next_chunk+1)

        return next_chunk

    def run_facedetection(self, video, fps):
        server = self.lip_sync_server[:-1]+"-fd/"
        return run_facedetection(server, video, fps)

    def get_video(self):
        if self.video is not None:
            return

        if self.video is None and self.video_name != "None":
            try:
                self.video = base64.b64decode(self.db.db.hget("facedubbing_videos",self.video_name))
            except Exception as e:
                print(e)
            else:
                print("Using video", self.video_name)

        if self.video is None:
            try:
                self.video = self.read_video()
                print("Using input video for facedubbing.")
            except:
                pass

        if not self.video:
            print("WARNING: Could not read video, using default video!")
            self.video = open("video.mp4","rb").read()

        if self.fps is None:
            self.fps = get_fps(self.video)

        if self.video_id is None:
            self.video_id = self.run_facedetection(self.video,self.fps)

    def combine_video_and_audio(self, audio, video):
        video = base64.b64decode(video)
        length = len(audio) / 32000

        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp, tempfile.NamedTemporaryFile() as tmp2:
            tmp.write(video)
            tmp.flush()
            tmp2.write(audio)
            tmp2.flush()
            #print(tmp.name, tmp2.name)
            #import time
            #time.sleep(1000)
            process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -i "+tmp.name+" -ar 16000 -ac 1 -f s16le -i "+tmp2.name+" -c:v copy -c:a aac -map 0:v -map 1:a -t "+str(length)+" -f matroska -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        #print("Combined video and audio")

        if process.returncode != 0:
            print("WARNING: Could not join video and audio, return only video")
            return video
        return process.stdout

    #def generate_video(self,audio,video_id,fps,length): # Dummy for testing
    #    output_video = base64.b64encode(self.video)
    #    return self.combine_video_and_audio(audio, output_video)

    def generate_video(self,audio,video_id,fps,length):
        try:
            print("Sending request with start frame", self.start_frame)
            r = requests.post(self.lip_sync_server, files={"pcm_s16le":audio, "video_id": video_id, "fps": fps, "start_frame": self.start_frame, "video_length_seconds": str(length)})
            print("Request finised.")
            if r.status_code != 200:
                print(r.text)
                raise requests.exceptions.ConnectionError
        except requests.exceptions.ConnectionError:
            print("ERROR in lip syncronization, returning empty bytes.")
            return bytes()
        else:
            print("Output video received from model.")
            r = r.json()
            output_video = r["output_video"]
            self.start_frame = r["end_frame"]

            return self.combine_video_and_audio(audio, output_video)
    
    def lip_sync_and_send(self):
        if not self.running:
            return

        try:
            self.get_video()
        except OSError:
            print("ERROR: Could not read video")
            return

        while self.running:
            print("READ AUDIO",flush=True)
            audio = self.read_audio(only_one_chunk=True)
            if audio is None or len(audio) == 0:
                return

            length = len(audio) / 32000

            t = time.time()
            print("Generating video...")
            output_video = self.generate_video(audio, self.video_id, self.fps, length)
            print("Generating",length,"seconds of video took",time.time()-t,"seconds.")

            self.send_video(output_video, length=length)

    def process(self):
        pass

    def stop_working(self):
        if not self.running:
            return

        self.lip_sync_and_send()

        self.running = False
        self.send_end()

class OnlineStreamingLipSync(OfflineStreamingLipSync):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.m3u8 = tempfile.NamedTemporaryFile(suffix=".m3u8")
        self.base = "/".join(self.m3u8.name.split("/")[:-1])
        self.line_next = 0

        self.chunk_counter = 0
        self.sample_rate = 16000  # 16 kHz
        self.bytes_per_sample = 2  # Assuming 16-bit audio
        self.num_channels = 1
        self.chunk_duration = 4
        # initialise the m3u8 wavheader
        self.init_m3u8()

    """
    def convert_to_ts(self, video):
        with tempfile.NamedTemporaryFile("wb") as temp:
            temp.write(video)
            temp.flush()
            #cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list+split_by_time -hls_playlist_type event "+self.m3u8.name
            #cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -c:v copy -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list "+self.m3u8.name
            cmd = "ffmpeg -hide_banner -loglevel warning -i "+temp.name+" -preset ultrafast -f hls -hls_time 1 -hls_list_size 0 -hls_flags append_list "+self.m3u8.name
            proc = subprocess.run(cmd.split())
    """

    def process(self):
        self.lip_sync_and_send()

    def send_video_old(self, output_video):
        t = time.time()

        output_video = self.convert_to_ts(output_video)

        print("Converting video to ts took",time.time()-t,"seconds.")

        with open(self.m3u8.name,"r") as f:
            lines = f.readlines()[self.line_next:]

        for line in lines:
            line = line.strip()
            if line == "#EXT-X-ENDLIST":
                break
            #print(line)
            if line.startswith("#EXTINF"):
                length = float(line[len("#EXTINF:"):-1])
            elif not line.startswith("#"):
                with open(self.base+"/"+line,"rb") as f2:
                    output_video = f2.read()
                super().send_video(output_video, additional_dict={"length":length})
            self.line_next += 1

    def send_video(self, output_video, length=None):
        self.chunk_counter += 1
        chunk_filename = f"chunk_{self.chunk_counter}.ts"
        chunk_path = os.path.join(self.base, chunk_filename)
        # Convert and write the chunk to a file
        self.convert_to_ts(chunk, chunk_path)
        # Update the playlist
        self.update_playlist(chunk_filename, length)
        # Read the chunk file and send it
        with open(chunk_path, "rb") as f:
            ts_data = f.read()
        self.send_video(ts_data, additional_dict={"length":length})

    def convert_to_ts(self, audio_data, output_ts_path):
        """
        Converts raw audio data to MPEG-TS format using ffmpeg.

        :param audio_data: The raw audio data (bytes).
        :param output_ts_path: Path where the MPEG-TS file will be written.
        """
        # Write WAV data with a dynamic header to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
            wav_path = temp_wav.name
            self.write_wav_with_dynamic_header(audio_data, wav_path)

        try:
            # Convert the WAV file to MPEG-TS format using ffmpeg
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-y",  # Automatically overwrite existing files
                "-i", wav_path, "-c:a", "aac", "-b:a", "96k", "-f", "mpegts", output_ts_path
            ]
            subprocess.run(cmd, check=True)
            print(f"MPEG-TS file written to {output_ts_path}")
        except subprocess.CalledProcessError as e:
            print(f"Error during ffmpeg conversion: {e}")
            raise
        finally:
            # Clean up the temporary WAV file
            if os.path.exists(wav_path):
                os.remove(wav_path)

    def write_wav_with_dynamic_header(self, audio_data, output_path):
        """
        Writes audio data to a WAV file with a dynamically calculated header.
    
        :param audio_data: The raw audio data (bytes).
        :param sample_rate: The sample rate (e.g., 16000 for 16 kHz).
        :param num_channels: Number of audio channels (1 for mono, 2 for stereo).
        :param bits_per_sample: Number of bits per sample (e.g., 16 for 16-bit audio).
        :param output_path: Path where the WAV file will be written.
        """
        audio_data_size = len(audio_data)  # Size of raw audio data
        byte_rate = self.sample_rate * self.num_channels * self.bytes_per_sample  # Bytes per second
        block_align = self.num_channels * self.bytes_per_sample # Block align (bytes per frame)
        riff_size = 4 + (8 + 16) + (8 + audio_data_size)  # Total size minus the RIFF descriptor (8 bytes)

        # Create the WAV header dynamically
        wav_header = struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF', riff_size, b'WAVE',  # RIFF chunk descriptor
            b'fmt ', 16, 1, self.num_channels, self.sample_rate, byte_rate, block_align, self.bytes_per_sample*8,  # fmt chunk
            b'data', audio_data_size  # data chunk descriptor
        )

        # Write the WAV file
        with open(output_path, 'wb') as wav_file:
            wav_file.write(wav_header)  # Write the header
            wav_file.write(audio_data)  # Write the audio data

        print(f"WAV file written to {output_path}")

    def update_playlist(self, new_chunk, chunk_duration):
        with open(self.m3u8.name, "a") as f:
            f.write(f"#EXTINF:{chunk_duration:.3f},\n")
            f.write(f"{new_chunk}\n")

    def init_m3u8(self):
        with open(self.m3u8.name, "w") as f:
            f.write("#EXTM3U\n")
            f.write("#EXT-X-VERSION:3\n")
            f.write(f"#EXT-X-TARGETDURATION:{self.chunk_duration}\n")
            f.write("#EXT-X-MEDIA-SEQUENCE:0\n")

    def stop_working(self):
        if not self.running:
            return

        self.running = False
        self.send_end()

        self.remove_links()

class LipSyncActiveSessionStorage(ActiveSessionStorage):    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        try:
            self.initial_face_detection()
        except requests.exceptions.ConnectionError:
            print("ERROR: Could not detect faces in given videos, probably the model worker is not running!")

    def save_video(self, key, data):
        name, session, tag = key
        video_mp4 = data["video_mp4"]

        value = self.get(key)
        if value is None:
            print("WARNING: key not found, starting session.")
            self.on_start(key,data)
            if value is None:
                print("ERROR: starting session did not work, ignoring request.")
                return

        # Save video in database
        num_chunks = get_from_db(self.db, name, session+"/"+tag, "VNumChunks")
        if num_chunks is None:
            value.new_stream(tag)

            num_chunks = 0

            set_in_db(self.db, name, session+"/"+tag, "VIndexNextChunk", 0)
        else:
            num_chunks = int(num_chunks.decode("utf-8"))

        set_in_db(self.db, name, session+"/"+tag, "VChunk"+str(num_chunks), video_mp4)
        set_in_db(self.db, name, session+"/"+tag, "VNumChunks", num_chunks+1)

    def on_start(self, key, data):
        name, session, tag = key
        db.setContext(name,session,tag)

        lip_sync_server = self.get_lip_sync_server()
        version = db.getPropertyValues("version")
        facedubbing_video = db.getPropertyValues("facedubbing_video")
        
        if version == "offline":
            value = OfflineStreamingLipSync(name,session,tag,lip_sync_server,facedubbing_video=facedubbing_video,track_links=True)
        elif version == "online":
            value = OnlineStreamingLipSync(name,session,tag,lip_sync_server,facedubbing_video=facedubbing_video,track_links=True)
        else:
            print("ERROR: Did not start session because version is unknown:", version)
            return

        # Start decoding thread
        self.set(key, value)
        
        if version == "online":
            value.process()

        data["sender"] = name+":"+tag
        con.publish("mediator", session, jsons.dumps(data)) # Send START to further components
    
    def on_end(self, session):
        session.stop_working()

    def on_information(self, key):
        name, session, tag = key
        db.setContext(name,session,tag)

        print("Sending INFORMATION.")

        properties = db.getPropertyValues()
        print("PROPERTIES",properties)

        # Do LIP server version request
        url = self.get_lip_sync_server(version=False)

        try:
            r = requests.post(url)
            if r.status_code != 200:
                raise requests.exceptions.ConnectionError
        except requests.exceptions.ConnectionError:
            print("WARNING: Could not request lip_server version, ignoring.")
            version = "Request failed"
        else:
            version = res.text

        properties["lip_sync_server_version"] = version

        name += ":"+tag
        data={'session': session, 'controll': "INFORMATION", 'sender': name, name:properties}
        con.publish("mediator", session, jsons.dumps(data))
    
    def initial_face_detection(self):
        print("Running face detection")
        for video_name in db.db.hkeys('facedubbing_videos'):
            print("Working on",video_name)
            video = db.db.hget('facedubbing_videos', video_name)
            try:
                video = base64.b64decode(video)
            except:
                print("WARNING: Could not b64decode video")
                continue
            try:
                fps = get_fps(video)
            except:
                print("WARNING: Could not calculate fps")
                continue
            server = self.get_lip_sync_server()[:-1]+"-fd/"

            run_facedetection(server, video, fps)
        print("Done")

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
                    #print("WARNING: Session is already started!")
                    return
                self.on_start(key,data)
            elif data["controll"] == "END":
                if data["sender"].startswith("user"):
                    return
                self.delete(key)
            elif data["controll"] == "INFORMATION":
                self.on_information(key)
            elif data["controll"] == "new_video":
                self.initial_face_detection()
            else:
                print("WARNING: Found unknown controll request "+str(data["controll"])+", ignoring.")
            return

        if "b64_enc_pcm_s16le" in data and not data["sender"].startswith("user"):
            if("linkedData" in data):
                b64_enc_pcm_s16le = self.db.get(data["b64_enc_pcm_s16le"])
                if b64_enc_pcm_s16le is None:
                    return
                data["pcm_s16le"] = base64.b64decode(b64_enc_pcm_s16le)
            else:
                data["pcm_s16le"] = base64.b64decode(data["b64_enc_pcm_s16le"])
            #print("GOT AUDIO")
            self.save_audio(key,data)

        if "b64_enc_audio" in data:
            if("linkedData" in data):
                audio = base64.b64decode(self.db.get(data["b64_enc_audio"]))
            else:
                audio = base64.b64decode(data["b64_enc_audio"])

            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(audio)
                tmp.flush()
                process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -i "+tmp.name+" -an -f mp4 -vcodec copy -y -movflags frag_keyframe+empty_moov -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                if process.returncode != 0:
                    print("ERROR: Could not convert input to raw audio")
                    return
                video_mp4 = process.stdout
                data["video_mp4"] = video_mp4
            self.save_video(key,data)

        return key, data

    def get_lip_sync_server(self, version=False):
        lip_sync_server = "http://192.168.0.68:5052/fg/infer/"
        """try:
            lip_sync_server = requests.get(f"{property_provider_url}/get_server/lip/fg_server", verify=False).json()["data"]
        except:
            lip_sync_server = db.getPropertyValues("fg_server")
        if lip_sync_server is None:
            lip_sync_server = db.getPropertyValues("fg_server_default")"""
        if version:
            url = "/".join(lip_sync_server.split("/")[:-2])+"/version"
            return url
        return lip_sync_server

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    parser.add_argument('--property_provider_url', type=str, default='http://192.168.0.72:5000')
    args = parser.parse_args()

    name = "lip"
    property_provider_url = args.property_provider_url

    with open('config.json', 'r') as f:
        config = json.load(f)
    for k,v in config.items():
        property_to_default[k] = v
        
    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    con = get_best_connector()(args.queue_server,args.queue_port,db,manual_link_deletion=True)

    sessions = LipSyncActiveSessionStorage(name, con, db)
    con.consume(name, sessions, True)
