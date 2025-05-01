
import requests
import sys
import time

s = "http://192.168.0.68:5052/fg/infer/"

def run_facedetection(video):
    print("Running face detection")
    res = requests.post(s[:-1]+"-fd/", files={"video":video, "fps": str(fps)})
    print("Face detection status code:",res.status_code)
    if res.status_code != 200:
        print(res.text)
        sys.exit()

    try:
        video_id = res.json()["video_id"]
    except:
        video_id = res.text

    print("Video id:",video_id)

    return video_id

def run_facegeneration(audio, length, video_id, fps, start_frame):
    t = time.time()

    print(f"Running video generation with length {length}")
    res = requests.post(s, files={"pcm_s16le":audio, "video_id":video_id, "fps": str(fps), "start_frame": start_frame, "video_length_seconds": str(length)})
    print("Face generation status code:",res.status_code)
    if res.status_code != 200:
        print(res.text)
        sys.exit()

    res = res.json()

    video_len = len(res["output_video"])

    #print("Length of generated video:",video_len)
    print("Next start frame:",res["end_frame"])

    print(f"RTF: {(time.time()-t)/length}")

    return res

if __name__ == "__main__":    
    video = open("video.mp4","rb").read()
    fps = 30

    video_id = run_facedetection(video)

    audio = open("audio.wav","rb").read()[78:]
    length = len(audio) / 32000

    start_frame = "0"
    while True:
        res = run_facegeneration(audio, length, video_id, fps, start_frame)
        start_frame = res["end_frame"]

