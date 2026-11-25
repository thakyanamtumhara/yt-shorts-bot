"""The daily answer Short, start to finish.

    python -m tools.answer_short.run            plan → voice → real-picture video → final look → publish
    ANSWER_DRY=1 ...                            everything except publishing (files stay in answer_out/)
    ANSWER_PLAN=path.json ...                   use a written plan instead of asking the writer (still checked)

A searched buyer question is answered from today's live facts over our own product videos and photos. No AI picture
is generated, so there is no clip bill and no "the picture does not match" stop. Publishing reuses the old daily
pipeline's upload code (same accounts, same AI-voice disclosure, same cover, same owner comment).
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'answer_out'
HISTORY = ROOT / 'answer_history.json'
IST = timezone(timedelta(hours=5, minutes=30))
HOLD_DAYS = 14
BANK_MAX_AGE = 21


class AnswerHold(RuntimeError):
    pass


def flag(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes')


def load_history(path=HISTORY):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except OSError:
        return {'format': 'answer-history-v1', 'published': [], 'refused': []}
    if data.get('format') != 'answer-history-v1' or not isinstance(data.get('published'), list):
        raise AnswerHold('answer_history.json is unreadable; repeats cannot be ruled out')
    data.setdefault('refused', [])
    return data


def save_history(data, path=HISTORY):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def taken(history, today):
    """Published answers, plus questions a run refused in the last HOLD_DAYS (so one hard question cannot take
    the Short day after day)."""
    recent = [item for item in history['refused']
              if (today - datetime.strptime(item['date'], '%Y-%m-%d').date()).days < HOLD_DAYS]
    return history['published'] + recent


def topic_for(plan):
    from tools.daily_topic_selection import SelectedTopic
    return SelectedTopic({'topic': plan['title_youtube'], 'buyer_question': plan['search_phrase'],
                          'intent_key': plan['intent_key'], 'lesson': ' '.join(line['say'] for line in plan['lines']),
                          'buyer_decision': plan['comment_question'], 'fact_ids': plan['fact_ids'],
                          'evidence': plan['evidence']})


def publish_check(plan, log=print):
    """A dry run cannot upload, but it can prove every piece of the upload path is there and the logins work."""
    sys.path.insert(0, str(ROOT))
    import daily_short as ds
    topic = topic_for(plan)
    for name in ('upload_to_youtube', 'upload_thumbnail', 'pin_comment', 'add_to_playlist', 'cross_post_to_instagram',
                 'save_ig_upload_record', 'publish_fb_reel', 'post_telegram_channel',
                 'check_past_engagement', 'check_instagram_engagement', 'refresh_instagram_token_if_needed',
                 'get_youtube_service'):
        if not callable(getattr(ds, name, None)):
            raise AnswerHold(f'upload code changed: daily_short.{name} is missing')
    for name in ('TOPIC_HISTORY_FILE', 'SCHEDULE_PUBLISH', 'COVER_META'):
        if not hasattr(ds, name):
            raise AnswerHold(f'upload code changed: daily_short.{name} is missing')
    tags, hashtags = ds.get_topic_tags(topic), ds.get_topic_hashtags(topic)
    ds.campaign_description_line(topic)
    youtube = ds.get_youtube_service()
    log(f"   upload path: code present, {len(tags)} topic tags, {len(hashtags)} hashtags, YouTube login "
        f"{'OK' if youtube else 'NOT working'}, Instagram token {'set' if os.environ.get('INSTAGRAM_ACCESS_TOKEN') else 'MISSING'}")
    return bool(youtube)


def publish(plan, report, log=print):
    """Each place on its own: one failing never stops the others. Returns what went where."""
    sys.path.insert(0, str(ROOT))
    import daily_short as ds
    from tools.daily_topic_selection import consume_uploaded_topic
    from tools.youtube_status import post_daily_ai_comment
    final, cover = report['final'], report['cover']
    topic = topic_for(plan)
    description = plan['description'].strip() + '\n\n' + plan['comment_question'].strip()
    went = {'youtube': None, 'instagram': None, 'facebook': None, 'telegram': False}
    youtube = ds.get_youtube_service()
    for label, job in (('YouTube numbers', lambda: youtube and ds.check_past_engagement(youtube)),
                       ('Instagram numbers', ds.check_instagram_engagement)):
        try:
            job()
        except Exception as error:
            log(f'   ⚠️ {label} not refreshed ({type(error).__name__})')
    if youtube:
        try:
            video_id, url = ds.upload_to_youtube(youtube, final, plan['title_youtube'], description, plan['tags'], topic=topic)
            if re.fullmatch(r'[A-Za-z0-9_-]{11}', str(video_id)):
                went['youtube'] = video_id
                consume_uploaded_topic(ds.TOPIC_HISTORY_FILE, topic, video_id)
                for label, job in (
                        ('owner comment', lambda: post_daily_ai_comment(
                            youtube, video_id, plan['comment_question'],
                            lambda: ds.pin_comment(youtube, video_id, comment_text=plan['comment_question']),
                            scheduled=ds.SCHEDULE_PUBLISH)),
                        ('cover', lambda: ds.upload_thumbnail(youtube, video_id, cover)),
                        ('playlist', lambda: ds.add_to_playlist(youtube, video_id, topic))):
                    try:
                        job()
                    except Exception as error:
                        log(f'   ⚠️ YouTube {label} skipped ({type(error).__name__}); upload kept')
        except Exception as error:
            log(f'   ❌ YouTube upload failed ({type(error).__name__}: {str(error)[:160]})')
    else:
        log('   ❌ YouTube login not available')
    try:
        if os.environ.get('INSTAGRAM_ACCESS_TOKEN'):
            ds.refresh_instagram_token_if_needed()
        media = ds.cross_post_to_instagram(final, plan['title_instagram'], description, topic, thumbnail_path=cover)
        if media and not str(media).startswith('test:'):
            went['instagram'] = str(media)
            ds.COVER_META.update({'cover_text': ' '.join(plan['cover']), 'cover_path': 'answer-short-real-frame',
                                  'cover_face': False, 'cover_color': None})
            ds.save_ig_upload_record(media, plan['title_instagram'], topic, cover_meta=ds.COVER_META,
                                     bot_youtube_id=went['youtube'])
    except Exception as error:
        log(f'   ❌ Instagram post failed ({type(error).__name__}: {str(error)[:160]})')
    caption = f"{plan['title_instagram']}\n\n{plan['description'].strip()}\n\n📦 Order: Sale91.com"
    try:
        went['facebook'] = ds.publish_fb_reel(final, caption, cover_path=cover) or None
    except Exception as error:
        log(f'   ❌ Facebook reel failed ({type(error).__name__})')
    try:
        went['telegram'] = bool(ds.post_telegram_channel(final, caption))
    except Exception as error:
        log(f'   ⚠️ Telegram post failed ({type(error).__name__})')
    return went


def main(log=print):
    from tools import voice_runtime
    from tools.answer_short import demand, facts, render, review, voice, write
    now = datetime.now(IST)
    today = now.date()
    dry = flag('ANSWER_DRY')
    OUT.mkdir(exist_ok=True)
    log(f"🎬 Answer Short — {now:%d %b %Y %H:%M} IST{'  [DRY: nothing is published]' if dry else ''}")
    sheet = facts.build(today.isoformat())
    sheet['normalize'] = voice_runtime.load_normalize_for_tts()
    for note in sheet['notes']:
        log(f'   ⚠️ {note}')
    log(f"   facts: {len(sheet['facts'])} ({sum(key.startswith('rate_') for key in sheet['facts'])} live rates), "
        f"products with real pictures: {len(sheet['visuals'])}")
    bank = demand.load()
    if demand.age_days(bank, now) > BANK_MAX_AGE:
        log(f"   ⚠️ searched questions were last read {bank['updated_on']}; refreshing")
        try:
            bank = demand.refresh()
        except Exception as error:
            log(f'   ⚠️ refresh failed ({error}); using the saved list')
    history = load_history()
    import anthropic
    client = anthropic.Anthropic()
    plan_file = os.environ.get('ANSWER_PLAN', '').strip()
    if plan_file:
        plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
        problems = write.code_problems(plan, sheet, bank['queries'], taken(history, today))
        if problems:
            raise AnswerHold('the written plan fails the checks: ' + '; '.join(problems))
        plan['model'] = 'written plan'
        plan['evidence'] = {key: sheet['facts'][key] for key in plan['fact_ids']}
    else:
        plan = write.make_plan(client, sheet, bank, taken(history, today), log=log)
    (OUT / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f"   question: \"{plan['search_phrase']}\"")
    for line in plan['lines']:
        log(f"      {line['say']}")
    key = os.environ.get('ELEVENLABS_API_KEY', '').strip()
    if not key:
        raise AnswerHold('no ElevenLabs key: the voice cannot be made')
    spoken = voice.speak(plan, OUT, key, sheet['normalize'])
    log(f"   voice: {spoken['duration']:.1f} s on {spoken['model_id']}, every word timed")
    slug = re.sub(r'[^a-z0-9]+', '-', plan['search_phrase'].lower()).strip('-')[:60]
    # Pictures of the last 12 published Shorts, oldest first: the render shows the least recently used ones first.
    recent = [key for item in history['published'][-12:] for key in item.get('pictures') or []]
    report = render.render(plan, sheet, spoken, OUT, OUT / f'{today.isoformat()}-{slug}.mp4', recent=recent)
    log(f"   video: {report['seconds']} s, {sum(item['kind'] == 'video' for item in report['pictures'])} real clips + "
        f"{sum(item['kind'] == 'photo' for item in report['pictures'])} real photos, music {'on' if report['music'] else 'OFF'}")
    model = plan['model'] if plan['model'].startswith('claude-') else write.pick_model(client)
    verdict = review.check(client, model, plan, report, spoken, OUT)
    log(f"   final look: {verdict['decision']} — {verdict['notes']}")
    for problem in verdict['problems']:
        log(f'      - {problem}')
    entry = {'date': today.isoformat(), 'search_phrase': plan['search_phrase'], 'intent_key': plan['intent_key'],
             'title': plan['title_youtube'], 'fact_ids': plan['fact_ids'], 'also_matches': plan.get('also_matches') or []}
    if verdict['decision'] != 'approve':
        if not dry:
            history['refused'] = (history['refused'] + [{**entry, 'why': '; '.join(map(str, verdict['problems']))[:300]}])[-40:]
            save_history(history)
        raise AnswerHold('review: rejected by the final look; nothing was published')
    if dry:
        publish_check(plan, log=log)
        log(f"   DRY run complete: {report['final']}")
        return 0
    went = publish(plan, report, log=log)
    log(f'   published: {json.dumps(went)}')
    if not (went['youtube'] or went['instagram']):
        raise AnswerHold('the video was made and approved but neither YouTube nor Instagram took it')
    history['published'].append({**entry, **went, 'seconds': report['seconds'],
                                 'pictures': [item['key'] for item in report['pictures']]})
    save_history(history)
    log('✅ Answer Short published')
    return 0


if __name__ == '__main__':
    from tools import content_watch
    try:
        code = main()
    except BaseException as error:
        content_watch.record_run(error)
        raise
    content_watch.record_run(None)
    sys.exit(code)
