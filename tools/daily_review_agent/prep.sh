#!/bin/bash
# prep.sh <run_id> <dir>: fetch a held Short's review copy and make what the reviewer reads.
set -euo pipefail
RUN=$1
D=$2
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$D/frames"
cd "$D"
aws s3 cp --only-show-errors "s3://bulkplaintshirt.com/p/review/$RUN/video.mp4" video.mp4
aws s3 cp --only-show-errors "s3://bulkplaintshirt.com/p/review/$RUN/cover.png" cover.png || true
aws s3 cp --only-show-errors "s3://bulkplaintshirt.com/p/review/$RUN/review.json" review.json
{
  echo "sha256 of video.mp4:      $(shasum -a 256 video.mp4 | cut -d' ' -f1)"
  echo "sha256 in review.json:    $(python3 -c "import json;print(json.load(open('review.json'))['video_sha256'])")"
  ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate:format=duration -of compact video.mp4
  ffmpeg -hide_banner -i video.mp4 -af loudnorm=print_format=summary -f null - 2>&1 | grep -E "Input Integrated|Input True Peak" || true
} > probe.txt
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 video.mp4)
python3 "$HERE/sheet.py" video.mp4 1.0 sheet.png 8 200 >/dev/null
for t in 1.3 $(python3 -c "d=$DUR;print(' '.join(f'{x:.1f}' for x in (d*0.25,d*0.5,d*0.75,d-3.5,d-1.0)))"); do
  ffmpeg -v error -y -ss "$t" -i video.mp4 -frames:v 1 -vf scale=540:960 "frames/t_${t}s.png"
done
ffmpeg -v error -y -i video.mp4 -ac 1 -ar 16000 audio16k.wav
: > transcript.txt
start=0
while python3 -c "import sys; sys.exit(0 if $start < $DUR else 1)"; do
  ffmpeg -v error -y -ss "$start" -t 12 -i audio16k.wav chunk.wav
  printf "[%ss-%ss] " "$start" "$((start + 12))" >> transcript.txt
  whisper-cli -m "$HOME/.whisper-models/ggml-large-v3-turbo.bin" -l hi -mc 0 -f chunk.wav -nt -np 2>/dev/null | tr -s " \n" " " >> transcript.txt
  echo >> transcript.txt
  start=$((start + 10))
done
rm -f chunk.wav
