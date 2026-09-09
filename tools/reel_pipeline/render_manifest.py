import argparse
import json
import math
import subprocess
from pathlib import Path


def run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)]).stdout)


def validate_keeps(keeps, duration):
    if not keeps:
        raise ValueError("The reviewed edit must contain at least one keep")
    previous = 0.0
    for start, end in keeps:
        if not all(math.isfinite(t) for t in (start, end)):
            raise ValueError("Non-finite cut time")
        if start < previous or end <= start or end > duration + 0.02:
            raise ValueError(f"Overlapping, unordered or out-of-source cut: {start}, {end}")
        previous = end
    return sum(end - start for start, end in keeps)


def audio_graph(keeps):
    parts = []
    for i, (start, end) in enumerate(keeps):
        parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]")
    parts.append("".join(f"[a{i}]" for i in range(len(keeps))) +
                 f"concat=n={len(keeps)}:v=0:a=1,highpass=f=60[voice]")
    return ";".join(parts)


def caption_sequence(captions, duration, directory, font_path, width, height):
    from PIL import Image, ImageDraw, ImageFont, features
    if not features.check("raqm"):
        raise RuntimeError("RAQM is required for correctly shaped Hindi")
    directory.mkdir(parents=True, exist_ok=True)
    strip_height = height // 6
    font = ImageFont.truetype(str(font_path), int(width * 0.064),
                              layout_engine=ImageFont.Layout.RAQM)
    try:
        font.set_variation_by_axes([700])
    except AttributeError:
        pass
    blank = directory / "blank.png"
    Image.new("RGBA", (width, strip_height)).save(blank)
    previous_end = 0
    states = []
    for i, caption in enumerate(captions):
        start, end, text = caption["start"], caption["end"], caption["text"].strip()
        if not text or start < previous_end or end <= start or end > duration + 0.02:
            raise ValueError(f"Invalid caption interval at {i}")
        previous_end = end
        im = Image.new("RGBA", (width, strip_height))
        draw = ImageDraw.Draw(im)
        lines, line = [], ""
        for word in text.split():
            candidate = (line + " " + word).strip()
            if draw.textlength(candidate, font=font) > width * 0.82 and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
        if len(lines) > 2 or any(draw.textlength(line, font=font) > width * 0.86 for line in lines):
            raise ValueError(f"Caption {i} is too long for a readable two-line phrase")
        line_height = int(width * 0.085)
        box_height = len(lines) * line_height + int(width * 0.025)
        draw.rounded_rectangle((width * 0.055, (strip_height - box_height) / 2,
                                width * 0.945, (strip_height + box_height) / 2),
                               radius=width * 0.014, fill=(10, 18, 28, 205))
        for j, line in enumerate(lines):
            draw.text((width / 2, strip_height / 2 + (j - (len(lines) - 1) / 2) * line_height),
                      line, anchor="mm", font=font, fill="white", stroke_width=1,
                      stroke_fill=(0, 0, 0, 220))
        path = directory / f"state-{i:04d}.png"
        im.save(path)
        states.append((start, end, path))
    cursor = 0
    for frame in range(math.ceil(duration * 30)):
        time = frame / 30
        while cursor < len(states) and states[cursor][1] <= time:
            cursor += 1
        source = states[cursor][2] if cursor < len(states) and states[cursor][0] <= time else blank
        path = directory / f"{frame:06d}.png"
        if path.exists():
            path.unlink()
        path.hardlink_to(source)
    return directory / "%06d.png", strip_height


def render(manifest_path, name, draft=False):
    manifest = json.loads(manifest_path.read_text())
    source = Path(manifest["source"])
    item = next(x for x in manifest["exports"] if x["name"] == name)
    info = probe(source)
    duration = validate_keeps(item["keeps"], float(info["format"]["duration"]))
    source_offset = item["keeps"][0][0]
    keeps = [[start - source_offset, end - source_offset] for start, end in item["keeps"]]
    source_args = ["-ss", str(source_offset), "-t", str(keeps[-1][1]), "-i", str(source)]
    destination = Path(item["output"])
    if draft:
        destination = destination.with_stem(destination.stem + "-draft")
    if destination.resolve() == source.resolve():
        raise ValueError("The source must never be overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = destination.parent / (destination.stem + "-build")
    work.mkdir(exist_ok=True)
    first_pass = audio_graph(keeps) + ";[voice]loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json[a]"
    measured_log = run(["ffmpeg", "-hide_banner", "-nostats", *source_args,
                        "-filter_complex", first_pass, "-map", "[a]", "-f", "null", "-"]).stderr
    measurement = json.loads(measured_log[measured_log.rfind("{"):measured_log.rfind("}") + 1])
    (work / "voice-measurement.json").write_text(json.dumps(measurement, indent=2))
    normalizer = ("loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:"
                  f"measured_I={measurement['input_i']}:measured_TP={measurement['input_tp']}:"
                  f"measured_LRA={measurement['input_lra']}:measured_thresh={measurement['input_thresh']}:"
                  f"offset={measurement['target_offset']},aresample=48000")
    width, height = (540, 960) if draft else (1080, 1920)
    command = ["ffmpeg", "-hide_banner", "-nostats", "-y", "-threads", "2", *source_args,
               "-loop", "1", "-framerate", "30", "-t", "0.5", "-i", item["cover"]]
    graph = []
    graph.append(f"[0:v]scale={width}:{height}:flags=lanczos,setsar=1,split={len(item['keeps'])}" +
                 "".join(f"[source{i}]" for i in range(len(item["keeps"]))))
    for i, (start, end) in enumerate(keeps):
        graph.append(f"[source{i}]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]")
        graph.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]")
    graph.append("".join(f"[v{i}][a{i}]" for i in range(len(item["keeps"]))) +
                 f"concat=n={len(item['keeps'])}:v=1:a=1[bodyvideo][bodyaudio]")
    captions = item.get("captions", [])
    if isinstance(captions, str):
        captions = json.loads(Path(captions).read_text())
    if captions:
        caption_y = float(item.get("caption_y", 0.74))
        if not 0.2 <= caption_y <= 0.85:
            raise ValueError("Caption centre must stay inside the reviewed video area")
        sequence, strip_height = caption_sequence(captions, duration, work / "captions",
                                                  Path(manifest["font"]), width, height)
        command += ["-framerate", "30", "-i", str(sequence)]
        graph.append(f"[bodyvideo]fps=30[bodyfps];[bodyfps][2:v]overlay=0:{int(height * caption_y) - strip_height // 2}:shortest=1[captioned]")
    else:
        graph.append("[bodyvideo]fps=30[captioned]")
    graph += [f"[bodyaudio]highpass=f=60,{normalizer}[cleanvoice]",
              f"[1:v]scale={width}:{height},setsar=1,format=yuv420p[cover]",
              "anullsrc=r=48000:cl=stereo,atrim=duration=0.5[silence]",
              "[cover][silence][captioned][cleanvoice]concat=n=2:v=1:a=1[v][a]"]
    graph_path = work / "render.ffgraph"
    graph_path.write_text(";".join(graph))
    command += ["-filter_complex_threads", "2", "-filter_complex_script", str(graph_path),
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-threads", "2",
                "-preset", "fast", "-crf", "19", "-profile:v", "high", "-level", "4.1",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart", "-t", str(duration + 0.5), str(destination)]
    (work / "command.json").write_text(json.dumps(command, indent=2))
    with (work / "encode.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)
    result = probe(destination)
    actual = float(result["format"]["duration"])
    if abs(actual - duration - 0.5) > 0.12:
        raise RuntimeError(f"Rendered duration mismatch: {actual} vs {duration + 0.5}")
    (work / "output-probe.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"output": str(destination), "duration": actual,
                      "removed_seconds": float(info["format"]["duration"]) - duration}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("name")
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()
    render(args.manifest, args.name, args.draft)
