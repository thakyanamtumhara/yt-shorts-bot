"""transcribe.py <16 kHz mono wav> <duration s>: the cloud stand-in for prep.sh's whisper-cli loop.

Same Whisper large-v3-turbo model and the same overlapping 12 s windows every 10 s, printed as
"[start s-end s] text" lines, so the reviewer reads the same transcript.txt on the Mac and in the cloud.
"""
import sys

from faster_whisper import WhisperModel, decode_audio

RATE = 16000


def windows(duration, size=12, step=10):
    start = 0
    while start < duration:
        yield start, start + size
        start += step


def main(path, duration):
    audio = decode_audio(path, sampling_rate=RATE)
    model = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
    for start, end in windows(float(duration)):
        chunk = audio[start * RATE:end * RATE]
        segments, _ = model.transcribe(chunk, language='hi', beam_size=5, condition_on_previous_text=False)
        text = ' '.join(segment.text.strip() for segment in segments)
        print(f'[{start}s-{end}s] {" ".join(text.split())}', flush=True)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
