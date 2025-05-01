import os
os.environ['CUDA_VISIBLE_DEVICES'] = '0'
from src.model.modeling_enh import VoiceFilter
import torch
import numpy as np
import torchaudio

use_gpu = True
if use_gpu:
    if not torch.cuda.is_available():
        use_gpu = False
print('use_gpu: {}'.format(use_gpu))
def cal_xvector_sincnet_embedding(xvector_model, ref_wav, max_length=5, sr=16000):
    wavs = []
    for i in range(0, len(ref_wav), max_length*sr):
        wav = ref_wav[i:i + max_length*sr]
        wav = np.concatenate([wav, np.zeros(max(0, max_length * sr - len(wav)))])
        wavs.append(wav)
    wavs = torch.from_numpy(np.stack(wavs))
    if use_gpu:
        wavs = wavs.cuda()
    embed = xvector_model(wavs.unsqueeze(1).float())
    return torch.mean(embed, dim=0).detach().cpu()
    
    
def compute_voice_filter(raw_audio_data, 
                         spk_embedding,
                         model,
                         samplerate=16000):
    assert samplerate == 16000, 'sample rate must be 16000'
    print('recieved raw audio {}: {}s'.format(raw_audio_data.shape, raw_audio_data.size(-1)/samplerate))
    xvector = spk_embedding    
    print('xvector: {}'.format(xvector.shape))
    # Speech enhancing
    mixed_wav = raw_audio_data
    max_amp = torch.abs(mixed_wav).max()
    mix_scaling = 1 / max_amp
    mixed_wav = mix_scaling * mixed_wav
    # mixed_wav_tf = torch.from_numpy(mixed_wav)
    if use_gpu:
        mixed_wav = mixed_wav.cuda()
        xvector= xvector.cuda()
        
    est_wav = model.do_enh_batch(mixed_wav, xvector)
    output_wav = []
    # multi-speaker
    for idx, spk_est_wav in enumerate(est_wav):
        # Normalize estimated wav
        max_amp = torch.abs(spk_est_wav).max()
        mix_scaling = 1 / max_amp
        spk_est_wav = mix_scaling * spk_est_wav
        output_wav.append(spk_est_wav)
        print('output audio {}: {}s'.format(idx, len(spk_est_wav)/samplerate))
    
    return torch.stack(output_wav)


if __name__ == "__main__":
    repo_id = 'nguyenvulebinh/voice-filter'
    enh_model = VoiceFilter.from_pretrained(repo_id, cache_dir='./cache').eval()
    if use_gpu:
        enh_model = enh_model.cuda()
    # print(enh_model)
    # exit()
    
    output_wav_path = "./cache/audio/output.wav"
    mix_wav_path = "./cache/audio/binh_nam.wav"
    # mix_wav_path = "./cache/audio/binh_linh_newspaper_music_noise.wav"
    # ref_wav_path = "./cache/audio/leonard_ref.wav"
    ref_wav_paths = [ "./cache/audio/linh_ref_long.wav", "./cache/audio/leonard_ref.wav"]
    
    
    
    mixed_wav= torchaudio.load(mix_wav_path)[0].squeeze()
    ref_wavs = [torchaudio.load(ref_wav_path)[0].squeeze() for ref_wav_path in ref_wav_paths]
    spk_embedding = torch.stack([cal_xvector_sincnet_embedding(enh_model.xvector_model, ref_wav, max_length=5, sr=16000) for ref_wav in ref_wavs])
    
    
    frame_duration = 1.0 # in seconds
    chunk_size = 1.0 # in seconds
    left_chunk_size= 3.0 # in seconds
    right_chunk_size= 1.0 # in seconds
    window_size = int(frame_duration * 16000)
    num_chunks_process = int((chunk_size + left_chunk_size + right_chunk_size) / frame_duration)
    num_chunks_pass = int((left_chunk_size + chunk_size) / frame_duration)
    num_chunks_future = int(right_chunk_size / frame_duration)
    
    do_streaming = True
    
    import time
    if do_streaming:
        enhanced_wav = []
        start_time = time.time()

        count_wav = 0
        queue_chunk = []
        for i in range(0, len(mixed_wav), window_size):
            count_wav += 1
            audio_tensor = mixed_wav[i:i+window_size].unsqueeze(0)
            print("Recieve: {:.2f}. Count: {}".format(audio_tensor.size(1) / 16_000, count_wav))
            if audio_tensor.size(1) < int(frame_duration * 16_000):
                print("Warning: append to audio")
                remain_len = int(frame_duration * 16_000) - audio_tensor.size(1)
                audio_tensor = torch.cat([audio_tensor, torch.zeros(1, remain_len)], dim=-1)
                
            queue_chunk.append(audio_tensor)
            queue_chunk = queue_chunk[-num_chunks_process:]
            
            if len(queue_chunk) < num_chunks_process:
                left_chunk_pad = num_chunks_pass - len(queue_chunk)
                right_chunk_pad = num_chunks_process - num_chunks_pass
                pad_tensor = torch.zeros(1, int(frame_duration * 16000))
                process_audio = torch.cat([pad_tensor] * left_chunk_pad + queue_chunk + [pad_tensor] * right_chunk_pad, dim=-1)
            else:
                process_audio = torch.cat(queue_chunk, dim=-1)
                if count_wav == num_chunks_process:
                    # this chunk has already processed
                    continue
            process_audio = process_audio.squeeze()
            # make process_audio same batch like spk_embedding
            process_audio = process_audio.unsqueeze(0).repeat(spk_embedding.size(0), 1)
            process_audio = compute_voice_filter(process_audio, spk_embedding, enh_model)
            current_output = process_audio[:, int(left_chunk_size * 16000): int((left_chunk_size + chunk_size) * 16000)]
            # replace nan with 0
            current_output[current_output != current_output] = 0.
            enhanced_wav.append(current_output)

        output_wav = torch.cat(enhanced_wav, dim=-1)
        output_wav_path = output_wav_path.replace('.wav', '_streaming_batch.wav')
    else:
        pass
        # start_time = time.time()
        # output_wav = compute_voice_filter(mixed_wav, ref_wav, enh_model)
    
    print("RTF: {}".format((time.time() - start_time) / (output_wav.size(-1)/16000)))
    print('time: {}s'.format(time.time() - start_time))
    for idx, wav in enumerate(output_wav):
        torchaudio.save(output_wav_path.replace('.wav', '_{}.wav'.format(idx)), wav.unsqueeze(0).float(), 16000)
    