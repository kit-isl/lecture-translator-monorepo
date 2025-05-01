#!/usr/bin/env python3
import argparse
import json
import jsons

import requests

import io
import os
import torchaudio
import torchaudio.transforms as T
import torch
import numpy as np
import math

from se_infer_utils import init_df, enhance

from qbmediator.Connection import get_best_connector
from qbmediator.Database import get_best_database
from qbmediator.Interface import MediatorInterface, ActiveSessionStorage
from qbmediator.Session import Session
from src.model.modeling_enh import VoiceFilter
from voice_filter_utils import cal_xvector_sincnet_embedding, compute_voice_filter
import moviepy.editor as mp
import subprocess
import tempfile
import traceback
import base64
import time
import pickle

def pcm_s16le_to_tensor(pcm_s16le):
    audio_tensor = np.frombuffer(pcm_s16le, dtype=np.int16)
    audio_tensor = torch.from_numpy(audio_tensor)
    audio_tensor = audio_tensor.float() / math.pow(2, 15)
    audio_tensor = audio_tensor.unsqueeze(1) # shape: frames x 1 (1 channel)
    return audio_tensor

def tensor_to_pcm_s16le(audio_tensor):
    audio_tensor = audio_tensor.squeeze(1)
    audio_tensor = (audio_tensor * math.pow(2, 15)).to(torch.int16)
    audio_tensor = audio_tensor.numpy()
    audio_tensor = audio_tensor.tobytes()
    return audio_tensor

class StreamingPrep(MediatorInterface):
    def __init__(self, name, session, tag, *args, **kwargs):
        super().__init__(name, session, tag)

    def stop_working(self):
        print("Stopping working.")
        self.running = False

        self.send_end()

    def process(self):
        while True:
            audio_in = self.read_audio(0.25)
            if len(audio_in)==0:
                return

            print("SENDING",len(audio_in))
            self.send_audio(audio_in) # Dummy 

class StreamingPrepNoiseCancelling(StreamingPrep):

    def __init__(self, name, session, tag, se_model, df_state, *args, **kwargs):
        super().__init__(name, session, tag, *args, **kwargs)

        self.frame_duration = 0.25 # in seconds
        self.chunk_size=0.25 # in seconds
        self.left_chunk_size=2.5 # in seconds
        self.right_chunk_size=0.25 # in seconds
        self.num_chunks_process = int((self.chunk_size + self.left_chunk_size + self.right_chunk_size) / self.frame_duration)
        self.num_chunks_pass = int((self.left_chunk_size + self.chunk_size) / self.frame_duration)
        self.num_chunks_future = int(self.right_chunk_size / self.frame_duration)
        self.se_model, self.df_state = se_model, df_state
        self.resampler = T.Resample(16000, 48000, dtype=torch.float32)
        self.iresampler = T.Resample(48000, 16000, dtype=torch.float32)
        self.debug_mode = os.environ.get('DEBUG', 'False') == 'True'
        print("Start online Noise Cancelling service")

        self.queue_chunk = []
        self.count_wav = 0

    def process(self):
        while True:
            ########################debug##################################
            # if self.debug_mode:
            #     import shutil
            #     if os.path.exists('./cache/audio'):
            #         shutil.rmtree('./cache/audio', ignore_errors=False, onerror=None)
            #     os.makedirs('./cache/audio')
            #     queue_output = []
            ###############################################################

            audio_in = self.read_audio(self.frame_duration)
            if len(audio_in)==0:
                return

            self.count_wav += 1
            audio_tensor = pcm_s16le_to_tensor(audio_in).squeeze().unsqueeze(0)
            print("Recieve: {:.2f}. Count: {}".format(audio_tensor.size(1) / 16_000, self.count_wav))
            if audio_tensor.size(1) < int(self.frame_duration * 16_000):
                print("Warning: append to audio")
                remain_len = int(self.frame_duration * 16_000) - audio_tensor.size(1)
                audio_tensor = torch.cat([audio_tensor, torch.zeros(1, remain_len)], dim=-1)
            self.queue_chunk.append(audio_tensor)
            self.queue_chunk = self.queue_chunk[-self.num_chunks_process:]
            
            if len(self.queue_chunk) < self.num_chunks_process:
                left_chunk_pad = self.num_chunks_pass - len(self.queue_chunk)
                right_chunk_pad = self.num_chunks_process - self.num_chunks_pass
                pad_tensor = torch.zeros(1, int(self.frame_duration * 16000))
                process_audio = torch.cat([pad_tensor] * left_chunk_pad + self.queue_chunk + [pad_tensor] * right_chunk_pad, dim=-1)
            else:
                process_audio = torch.cat(self.queue_chunk, dim=-1)
                if self.count_wav == self.num_chunks_process:
                    # this chunk has already processed
                    return
            
            process_audio = self.resampler(process_audio)
            process_audio = enhance(self.se_model, self.df_state, process_audio)
            process_audio = self.iresampler(process_audio)
            current_output = process_audio[:, int(self.left_chunk_size * 16000): int((self.left_chunk_size + self.chunk_size) * 16000)]

            #############################debug#################################
            # if self.debug_mode:
            #     queue_output.append(current_output)
            #     torchaudio.save('./cache/audio/{}.wav'.format(count_wav), current_output, 16_000, encoding="PCM_S", bits_per_sample=16)
            #     torchaudio.save('./cache/audio/{}_raw.wav'.format(count_wav), audio_tensor, 16_000, encoding="PCM_S", bits_per_sample=16)
            #     print("\nSave segment {}\n".format(count_wav))
            ###################################################################
            
            audio = tensor_to_pcm_s16le(current_output)
            self.send_audio(audio)

            #############################debug#################################
            # if self.debug_mode:
            #     torchaudio.save('./cache/audio/output_enhanced.wav', torch.cat(queue_output, dim=-1), 16_000, encoding="PCM_S", bits_per_sample=16)
            ###################################################################

class OfflinePrepNoiseCancelling(StreamingPrep):

    def __init__(self, name, session, tag, se_model, df_state, *args, **kwargs):
        super().__init__(name, session, tag, *args, **kwargs)

        self.batch_size = 2
        self.se_model, self.df_state = se_model, df_state
        self.resampler = T.Resample(16000, 48000, dtype=torch.float32)
        self.iresampler = T.Resample(48000, 16000, dtype=torch.float32)
        self.debug_mode = False #os.environ.get('DEBUG', 'False') == 'True'
        print("Start offline Noise Cancelling service")

    def batch_audio(self, list_audio, max_duration=30, sr=48000):
        max_size = max_duration*sr
        batch_max_size = max([item.size(-1) for item in list_audio])
        max_size = min(max_size, batch_max_size)
        max_size = math.ceil(max_size / sr) * sr
        audio_lengths = []
        for i, audio in enumerate(list_audio):
            remain = max_size - audio.size(-1)
            if remain > 0:
                list_audio[i] = torch.cat([audio, torch.zeros(1, remain)], dim=-1)
                audio_lengths.append(audio.size(-1))
            else:
                list_audio[i] = audio[:, :max_size]
                audio_lengths.append(max_size)
        return torch.cat(list_audio, dim=0), audio_lengths

    def process(self):
        pass

    def stop_working(self):
        self.running = False

        ########################debug##################################
        # if self.debug_mode:
        #     import shutil
        #     if os.path.exists('./cache/audio'):
        #         shutil.rmtree('./cache/audio', ignore_errors=False, onerror=None)
        #     os.makedirs('./cache/audio')
        ###############################################################
        queue_chunk = []
        count_wav = 0
        while True:
            audio_in = self.read_audio(30)
            if len(audio_in) > 0:
                count_wav += 1
                audio_tensor = pcm_s16le_to_tensor(audio_in).squeeze().unsqueeze(0)
                print("Recieve: {:.2f}. Count: {}".format(audio_tensor.size(1) / 16_000, count_wav))
                queue_chunk.append(audio_tensor)
            
                if len(queue_chunk) < self.batch_size:
                    # add more audio to process
                    continue
            
            if len(queue_chunk) > 0:
                audio_tensor_raw, lengths = self.batch_audio(queue_chunk)
                audio_tensor = self.resampler(audio_tensor_raw)
                enhanced_wavforms = enhance(self.se_model, self.df_state, audio_tensor)
                enhanced_wavforms = self.iresampler(enhanced_wavforms)
                enhanced_wavforms = [enhanced_wavforms[i][:l] for i, l in enumerate(lengths)]
                for idx in range(len(enhanced_wavforms)) :
                    current_output = enhanced_wavforms[idx].unsqueeze(0)
                    #############################debug#################################
                    # if self.debug_mode:
                    #     audio_input = queue_chunk[idx][:, :int(lengths[idx]*16_000)]
                    #     torchaudio.save('./cache/audio/{}_{}.wav'.format(count_wav, idx), current_output, 16_000, encoding="PCM_S", bits_per_sample=16)
                    #     torchaudio.save('./cache/audio/{}_{}_raw.wav'.format(count_wav, idx), audio_input, 16_000, encoding="PCM_S", bits_per_sample=16)
                    #     print("\nSave segment {}_{}, length: {:.2f}\n".format(count_wav, idx, current_output.size(1) / 16_000))
                    ###################################################################
                    
                    audio = tensor_to_pcm_s16le(current_output)
                    self.send_audio(audio)
                queue_chunk = []

            if len(audio_in)==0 and not self.running:
                break

        self.send_end()

def video_to_wav_array(video_bytes, sample_rate=16000):
    # Create a temporary file and write the video bytes to it
    print("video_to_wav_array")
    print("Recieve: {:.2f}MB".format(len(video_bytes) / 1024 / 1024))
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
        temp_filename = temp_file.name
        # temp_file.write(video_bytes)
        temp_file.write(base64.b64decode(video_bytes))
    try:
        # Load the video from the temporary file
        video = mp.VideoFileClip(temp_filename)
        # Extract the audio
        audio = video.audio
        audio_array = audio.to_soundarray(fps=sample_rate)
        print("Decoded audio shape:", audio_array.shape)
        audio_array = audio_array[:, 0] # only get the first channel
    except:
        traceback.print_exc()
        audio_array = None
    finally:
        # Delete the temporary file
        os.remove(temp_filename)
    if audio_array is None:
        print("Error: audio_array is None")
    else:
        print("return audio_array shape:", audio_array.shape)
    return audio_array

# def video_to_wav_array(video_bytes):
#     with tempfile.NamedTemporaryFile() as tmp:
#         tmp.write(base64.b64decode(video_bytes))
#         tmp.flush()
#         process = subprocess.run(("ffmpeg -hide_banner -loglevel warning -vn -i "+tmp.name+" -map 0:a -ac 1 -f s16le -ar 16000 -c:a pcm_s16le -").split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
#         if process.returncode != 0:
#             print("ERROR: Could not convert input to raw audio")
#             return
#         raw_wav = process.stdout
#     return pcm_s16le_to_tensor(raw_wav)

def load_audio_reference(sample_names):
    # return None
    try:
        print("load_audio_reference", sample_names)
        # sample_names = ['Alexander Waibel']
        sample_videos = [db.db.hget('facedubbing_videos', n) for n in sample_names]
        sample_audios = [video_to_wav_array(v) for v in sample_videos]
        return sample_audios # list of bytes of videos
    except:
        traceback.print_exc()
        return None

class StreamingPrepSpeakerFiltering(StreamingPrep):
    def __init__(self, name, session, tag, sample_names, voice_filter_model, voice_filter_service_endpoint, *args, **kwargs):
        super().__init__(name, session, tag)

        """if sample_names == 'None':
            sample_names = None
        else:
            sample_names = json.loads(sample_names)
        print(voice_filter_model)
        print("Start StreamingPrepSpeakerFiltering sample_names",sample_names)
    
        self.audio_samples = load_audio_reference(sample_names) if sample_names is not None else None
        self.enh_model = voice_filter_model
        self.voice_filter_service_endpoint = voice_filter_service_endpoint
        # extracting the speaker embedding here
        # audio.shape = (length,)
        if self.audio_samples is not None:
            self.spk_embedding = torch.stack([cal_xvector_sincnet_embedding(voice_filter_model.xvector_model, audio) for audio in self.audio_samples])
        else:
            self.spk_embedding = None"""

        s = db.db.get("Session" + session)
        if s is None:
            print("ERROR: Session not found (1)")
            return

        s = jsons.load(json.loads(s.decode("utf-8")), Session)
        if s is None:
            print("ERROR: Session not found (2)")
            return

        receivers = s.get_receivers(name+":"+tag)
        self.receivers = [r for r in receivers if r.startswith("asr")]
        
        
        self.frame_duration = 1.0 # in seconds
        self.chunk_size=1.0 # in seconds
        self.left_chunk_size=3.0 # in seconds
        self.right_chunk_size=1.0 # in seconds
        self.num_chunks_process = int((self.chunk_size + self.left_chunk_size + self.right_chunk_size) / self.frame_duration)
        self.num_chunks_pass = int((self.left_chunk_size + self.chunk_size) / self.frame_duration)
        self.num_chunks_future = int(self.right_chunk_size / self.frame_duration)
        self.queue_chunk = []
        self.count_wav = 0

    def call_service_voice_filter(self, audio_in, spk_embedding):
        """
        audio_in: tensor (batch, length)
        spk_embedding: tensor (batch, feature_dim)
        """
        print("call_service_voice_filter", audio_in.size(), spk_embedding.size())
        bytes_audio = pickle.dumps(audio_in)
        stream = io.BytesIO(bytes_audio)

        bytes_spk_embedding = pickle.dumps(spk_embedding)
        stream_spk_embedding = io.BytesIO(bytes_spk_embedding)

        files = {"audio_input": stream, "speaker_embedding": stream_spk_embedding}

        output = requests.post(f'{self.voice_filter_service_endpoint}/enh/infer', files=files).json()
        
        file = output['enhanced_signal_base64']
        file = base64.b64decode(file.encode('ascii'))
        audio_output = pickle.loads(file)
        print("audio_output", audio_output.shape)
        return audio_output

    def process(self):
        while True:
            
            audio_in = self.read_audio(return_start_time=True)
            if type(audio_in) is tuple:
                audio_in, start_time = audio_in
            else:
                start_time = "None"

            if len(audio_in)==0:
                return
            
            if audio_in == b'PAUSE':
                print("SENDING PAUSE")
                for receiver in self.receivers:
                    self.send_pause(additional_dict={"send_to":receiver})
            else:
                if False: #self.spk_embedding is not None:
                    # Do the speaker filtering here
                    audio_tensor = pcm_s16le_to_tensor(audio_in).squeeze().unsqueeze(0)
                    print("audio_tensor in",audio_tensor.size())
                    self.queue_chunk.append(audio_tensor)
                    self.queue_chunk = self.queue_chunk[-self.num_chunks_process:]
                                    
                    
                    if len(self.queue_chunk) < self.num_chunks_process:
                        left_chunk_pad = self.num_chunks_pass - len(self.queue_chunk)
                        right_chunk_pad = self.num_chunks_process - self.num_chunks_pass
                        pad_tensor = torch.zeros(1, int(self.frame_duration * 16000))
                        process_audio = torch.cat([pad_tensor] * left_chunk_pad + self.queue_chunk + [pad_tensor] * right_chunk_pad, dim=-1)
                    else:
                        process_audio = torch.cat(self.queue_chunk, dim=-1)
                        if self.count_wav == self.num_chunks_process:
                            # this chunk has already processed
                            return
                    
                    process_audio = process_audio.squeeze()
                    # make process_audio same batch like spk_embedding
                    process_audio = process_audio.unsqueeze(0).repeat(self.spk_embedding.size(0), 1)
                    start_time = time.time()
                    
                    # call voice filter service for faster processing
                    # process_audio_base6 = pcm_s16le_to_tensor(tensor_to_pcm_s16le(process_audio)) 
                    process_audio = self.call_service_voice_filter(process_audio, self.spk_embedding)
                    # process_audio = compute_voice_filter(process_audio, self.spk_embedding, self.enh_model)
                    
                    
                    print("RTF: {}".format((time.time() - start_time) / self.frame_duration))
                    current_output = process_audio[:, int(self.left_chunk_size * 16000): int((self.left_chunk_size + self.chunk_size) * 16000)]
                    # replace nan with 0
                    current_output[current_output != current_output] = 0.
                    
                    for audio_out_tensor,receiver in zip(current_output,self.receivers):
                        pcm_s16le = tensor_to_pcm_s16le(audio_out_tensor.unsqueeze(1))
                        print("SENDING filter",len(pcm_s16le),"to",receiver)
                        self.send_audio(pcm_s16le, additional_dict={"send_to":receiver, "start":start_time})
                else:
                    for receiver in self.receivers:
                        print("SENDING origin",len(audio_in),"to",receiver)
                        self.send_audio(audio_in, additional_dict={"send_to":receiver, "start":start_time})
                        
                        
# class StreamingPrepSpeakerFilteringServiceCall(StreamingPrep):
#     def __init__(self, name, session, tag, sample_names, service_endpoint, *args, **kwargs):
#         super().__init__(name, session, tag)

#         if sample_names == 'None':
#             sample_names = None
#         else:
#             sample_names = json.loads(sample_names)
        
#         self.service_endpoint = service_endpoint    
        
#         # print(voice_filter_model)
#         print("Start StreamingPrepSpeakerFiltering sample_names",sample_names)
    
#         self.audio_samples = load_audio_reference(sample_names) if sample_names is not None else None
#         # self.enh_model = voice_filter_model
#         # extracting the speaker embedding here
#         # audio.shape = (length,)
#         if self.audio_samples is not None:
#             # self.spk_embedding = torch.stack([cal_xvector_sincnet_embedding(voice_filter_model.xvector_model, audio) for audio in self.audio_samples])
#             self.spk_embedding = ...
#         else:
#             self.spk_embedding = None

#         s = db.db.get("Session" + session)
#         if s is None:
#             print("ERROR: Session not found (1)")
#             return

#         s = jsons.load(json.loads(s.decode("utf-8")), Session)
#         if s is None:
#             print("ERROR: Session not found (2)")
#             return

#         receivers = s.get_receivers(name+":"+tag)
#         self.receivers = [r for r in receivers if r.startswith("asr")]
        
        
#         self.frame_duration = 1.0 # in seconds
#         self.chunk_size=1.0 # in seconds
#         self.left_chunk_size=3.0 # in seconds
#         self.right_chunk_size=1.0 # in seconds
#         self.num_chunks_process = int((self.chunk_size + self.left_chunk_size + self.right_chunk_size) / self.frame_duration)
#         self.num_chunks_pass = int((self.left_chunk_size + self.chunk_size) / self.frame_duration)
#         self.num_chunks_future = int(self.right_chunk_size / self.frame_duration)
#         self.queue_chunk = []
#         self.count_wav = 0

#     def process(self):
#         while True:
            
#             audio_in = self.read_audio(self.frame_duration, return_start_time=True)
#             if type(audio_in) is tuple:
#                 audio_in, start_time = audio_in
#             else:
#                 start_time = "None"

#             if len(audio_in)==0:
#                 return
            
#             if audio_in == b'PAUSE':
#                 print("SENDING PAUSE")
#                 for receiver in self.receivers:
#                     self.send_pause(additional_dict={"send_to":receiver})
#             else:
#                 if self.spk_embedding is not None:
#                     # Do the speaker filtering here
#                     audio_tensor = pcm_s16le_to_tensor(audio_in).squeeze().unsqueeze(0)
#                     print("audio_tensor in",audio_tensor.size())
#                     self.queue_chunk.append(audio_tensor)
#                     self.queue_chunk = self.queue_chunk[-self.num_chunks_process:]
                                    
                    
#                     if len(self.queue_chunk) < self.num_chunks_process:
#                         left_chunk_pad = self.num_chunks_pass - len(self.queue_chunk)
#                         right_chunk_pad = self.num_chunks_process - self.num_chunks_pass
#                         pad_tensor = torch.zeros(1, int(self.frame_duration * 16000))
#                         process_audio = torch.cat([pad_tensor] * left_chunk_pad + self.queue_chunk + [pad_tensor] * right_chunk_pad, dim=-1)
#                     else:
#                         process_audio = torch.cat(self.queue_chunk, dim=-1)
#                         if self.count_wav == self.num_chunks_process:
#                             # this chunk has already processed
#                             return
                    
#                     process_audio = process_audio.squeeze()
#                     # make process_audio same batch like spk_embedding
#                     process_audio = process_audio.unsqueeze(0).repeat(self.spk_embedding.size(0), 1)
#                     start_time = time.time()
#                     process_audio = compute_voice_filter(process_audio, self.spk_embedding, self.enh_model)
#                     print("RTF: {}".format((time.time() - start_time) / self.frame_duration))
#                     current_output = process_audio[:, int(self.left_chunk_size * 16000): int((self.left_chunk_size + self.chunk_size) * 16000)]
#                     # replace nan with 0
#                     current_output[current_output != current_output] = 0.
                    
#                     for audio_out_tensor,receiver in zip(current_output,self.receivers):
#                         pcm_s16le = tensor_to_pcm_s16le(audio_out_tensor.unsqueeze(1))
#                         print("SENDING filter",len(pcm_s16le),"to",receiver)
#                         self.send_audio(pcm_s16le, additional_dict={"send_to":receiver, "start":start_time})
#                 else:
#                     for receiver in self.receivers:
#                         print("SENDING origin",len(audio_in),"to",receiver)
#                         self.send_audio(audio_in, additional_dict={"send_to":receiver, "start":start_time})
            
        
property_to_default = {
    "version": "online", # Options: online, offline
    "task": "NoiseCancellation", # Options: NoiseCancellation, SpeakerFiltering
    "sample_names": "None",
}

class PrepActiveSessionStorage(ActiveSessionStorage):
    def __init__(self, name, con, db):
        super().__init__(name, con, db)

        self.se_model, self.df_state, _ = init_df('./pretrained', post_filter=True, config_allow_defaults=True)
    
        repo_id = 'nguyenvulebinh/voice-filter'
        self.voice_filter_model = VoiceFilter.from_pretrained(repo_id, cache_dir='./cache').eval()
        if torch.cuda.is_available():
            self.voice_filter_model = self.voice_filter_model.cuda()
        
        self.voice_filter_service_endpoint = os.environ.get('VOICE_FILTER_SERVICE_ENDPOINT', 'http://192.168.0.66:5055')

    def on_start(self, key, data):
        name, session, tag = key

        db.setContext(name,session,tag)

        # Read properties if needed
        version = db.getPropertyValues("version")
        task = db.getPropertyValues("task")
        sample_names = db.getPropertyValues("sample_names")

        streamingPrep = None
        if task == "NoiseCancellation":
            if version == "online":
                streamingPrep = StreamingPrepNoiseCancelling(name, session, tag, self.se_model, self.df_state)
            elif version == "offline":
                streamingPrep = OfflinePrepNoiseCancelling(name, session, tag, self.se_model, self.df_state)
        elif task == "SpeakerFiltering":
            # streamingPrep = StreamingPrepSpeakerFilteringServiceCall(name, session, tag, sample_names, self.voice_filter_service_endpoint)
            streamingPrep = StreamingPrepSpeakerFiltering(name, session, tag, sample_names, self.voice_filter_model, self.voice_filter_service_endpoint)
        
        if streamingPrep is None:
            streamingPrep = StreamingPrep(name, session, tag)

        self.set(key, streamingPrep)

        if sample_names == 'None':
            sample_names = None
        else:
            sample_names = json.loads(sample_names)

        data["sender"] = name+":"+tag
        if task != "SpeakerFiltering" or not "speaker" in data or not "speakerName" in data or type(data["speaker"]) is not list or type(data["speakerName"]) is not list:
            con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send START to further components
        else:
            speakers = data["speaker"]
            speakerNames = data["speakerName"]
            if sample_names is None or len(sample_names) != len(speakers):
                print("ERROR: Sample_names not set correctly",sample_names)
                for r,s,sn in zip(streamingPrep.receivers,speakers,speakerNames):
                    data["speaker"] = s
                    data["speakerName"] = sn
                    data["send_to"] = r
                    con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send START to further components
            else:
                for r,s,sn,sn2 in zip(streamingPrep.receivers,speakers,speakerNames,sample_names):
                    data["speaker"] = s
                    data["speakerName"] = sn
                    data["sample_name"] = sn2
                    data["send_to"] = r
                    con.publish("mediator", session+"/"+tag, jsons.dumps(data)) # Send START to further components

    def on_end(self, session):
        session.stop_working()

    def on_information(self, key):
        name, session, tag = key
        db.setContext(name,session,tag)

        print("Sending INFORMATION.")

        properties = db.getPropertyValues()
        print("PROPERTIES",properties)

        name += ":"+tag
        data={'session': session, 'controll': "INFORMATION", 'sender': name, name:properties}
        con.publish("mediator", session+"/"+tag, jsons.dumps(data))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--queue-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--queue-port', help='NATS Server address', type=str, default='5672')
    parser.add_argument('--redis-server', help='NATS Server address', type=str, default='localhost')
    parser.add_argument('--redis-port', help='NATS Server address', type=str, default='6379')
    args = parser.parse_args()

    name = "prep"

    db = get_best_database()(args.redis_server, args.redis_port)
    db.setDefaultProperties(property_to_default)

    con = get_best_connector()(args.queue_server,args.queue_port,db)
    #con.register(db, name, d)

    prep = PrepActiveSessionStorage(name, con, db)
    con.consume(name, prep, True)
