"""transcribe.py <16 kHz mono wav> <duration s>: the cloud stand-in for prep.sh's whisper-cli loop.

Same Whisper large-v3-turbo model and the same overlapping 12 s windows every 10 s, printed as
"[start s-end s] text" lines, so the reviewer reads the same transcript.txt on the Mac and in the cloud.
"""
import sys
import wave

import numpy
from faster_whisper import WhisperModel

RATE = 16000


def windows(duration, size=12, step=10):
    start = 0
    while start < duration:
        yield start, start + size
        start += step


def read_wav(path):
    """prep.sh writes 16 kHz mono 16-bit PCM; read it directly (faster-whisper's PyAV loader breaks on new PyAV)."""
    with wave.open(path) as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (RATE, 1, 2):
            raise SystemExit('transcribe.py needs 16 kHz mono 16-bit WAV')
        frames = source.readframes(source.getnframes())
    return numpy.frombuffer(frames, dtype='<i2').astype(numpy.float32) / 32768.0


def main(path, duration):
    audio = read_wav(path)
    model = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
    for start, end in windows(float(duration)):
        chunk = audio[start * RATE:end * RATE]
        segments, _ = model.transcribe(chunk, language='hi', beam_size=5, condition_on_previous_text=False)
        text = ' '.join(segment.text.strip() for segment in segments)
        print(f'[{start}s-{end}s] {" ".join(text.split())}', flush=True)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
