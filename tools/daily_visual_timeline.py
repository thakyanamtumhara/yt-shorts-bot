import math


def plan_visual_segments(durations, target_seconds, overlap_seconds=0.3):
    durations = [float(value) for value in durations]
    target = float(target_seconds)
    overlap = float(overlap_seconds)
    if not math.isfinite(target) or target <= 0:
        raise ValueError('Visual target must be positive and finite')
    if not math.isfinite(overlap) or overlap < 0:
        raise ValueError('Transition overlap must be nonnegative and finite')
    if not durations or any(not math.isfinite(value) or value <= overlap for value in durations):
        raise ValueError('Each visual source must be longer than the transition overlap')

    indices = list(range(len(durations)))
    while sum(durations[index] for index in indices) - overlap * (len(indices) - 1) < target - 1e-8:
        if len(indices) >= 1000:
            raise ValueError('Too many segments required for visual coverage')
        indices.append(len(indices) % len(durations))

    while len(indices) > 1 and target <= overlap:
        indices.pop()
    required = target + overlap * (len(indices) - 1)
    allocated = [0.0] * len(indices)
    remaining = set(range(len(indices)))
    while remaining:
        share = required / len(remaining)
        capped = [index for index in remaining if durations[indices[index]] < share]
        if not capped:
            for index in remaining:
                allocated[index] = share
            break
        for index in capped:
            allocated[index] = durations[indices[index]]
            required -= allocated[index]
            remaining.remove(index)

    result = []
    start = 0.0
    for index, seconds in zip(indices, allocated):
        result.append({'source_index': index, 'duration': seconds, 'start': start})
        start += seconds - overlap
    actual = sum(item['duration'] for item in result) - overlap * (len(result) - 1)
    if abs(actual - target) > 1e-6:
        raise ValueError('Visual plan does not cover the complete narration')
    return result


def verify_visual_coverage(actual_seconds, target_seconds, fps=30):
    actual, target = float(actual_seconds), float(target_seconds)
    if not all(math.isfinite(value) and value > 0 for value in (actual, target, fps)):
        raise ValueError('Invalid visual coverage values')
    if actual < target - 0.5 / fps:
        raise ValueError(f'Visuals end {target-actual:.3f}s before the narration buffer; stop before publishing')


def assemble_visual_timeline(video_objects, target_seconds, overlap_seconds=0.3):
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
    plan = plan_visual_segments([clip.duration for clip in video_objects], target_seconds, overlap_seconds)
    clips = [video_objects[item['source_index']].subclip(0, item['duration']).set_start(item['start']) for item in plan]
    result = CompositeVideoClip(clips, size=video_objects[0].size)
    verify_visual_coverage(result.duration, target_seconds)
    return result.subclip(0, target_seconds)


def screen_recording_rate(source_seconds, target_seconds, slowest=0.7, fastest=1.6):
    """Playback rate that lets ONE real screen recording run once over the whole narration (owner review of run 336:
    a looping recording that scrolled into unrelated sections was rejected)."""
    source, target = float(source_seconds), float(target_seconds)
    if not all(math.isfinite(value) and value > 0 for value in (source, target)) or not 0 < slowest <= 1 <= fastest:
        raise ValueError('Invalid screen recording timing')
    return min(fastest, max(slowest, source / target))


def fit_screen_recording(clip, target_seconds, slowest=0.7, fastest=1.6):
    """The recording sped up or slowed down to end with the narration. Outside the allowed rates it is cut at the end
    or holds its last frame; it never starts again from the beginning."""
    from moviepy.video.compositing.concatenate import concatenate_videoclips
    from moviepy.video.fx.all import speedx
    target = float(target_seconds)
    rate = screen_recording_rate(clip.duration, target, slowest, fastest)
    fitted = clip if abs(rate - 1) < 1e-6 else speedx(clip, factor=rate)
    if fitted.duration < target:
        still = fitted.to_ImageClip(t=max(0.0, fitted.duration - 0.05)).set_duration(target - fitted.duration + 0.05)
        fitted = concatenate_videoclips([fitted, still])
    fitted = fitted.subclip(0, target)
    verify_visual_coverage(fitted.duration, target)
    return fitted
