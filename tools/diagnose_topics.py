#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Review topic candidates without media or publishing.')
    parser.add_argument('--max-reviews', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--compare-original', action='store_true',
                        help='Also make one diagnostic call with the old ten-brief/2400-token limits.')
    parser.add_argument('--scripts', type=int, choices=range(0, 6), default=0,
                        help='Write this many first-attempt scripts for the approved topic and report their '
                             'spoken length, voice check and review; no voice, video or publishing.')
    args = parser.parse_args(argv)
    if not os.environ.get('ANTHROPIC_API_KEY'):
        parser.error('ANTHROPIC_API_KEY is required')

    import anthropic
    import daily_short
    from tools.daily_topic_selection import (
        TopicHold, choose_topic, load_bank, load_topic_history, load_visual_holds, validate_brief,
        response_json, safe_failure_details,
    )

    history = load_topic_history(ROOT / 'topic_history.json')
    daily_short._install_live_rates()
    bank = load_bank()
    remaining = []
    for seed in bank.get('seed_lessons', []):
        try:
            remaining.append(validate_brief(seed, bank, history))
        except TopicHold:
            pass
    print(json.dumps({'mode': 'topic-only', 'history_count': len(history),
                      'seed_count': len(bank['seed_lessons']),
                      'unused_seed_titles': [item['topic'] for item in remaining]}, ensure_ascii=False))
    api = anthropic.Anthropic(max_retries=1, timeout=90)
    if args.compare_original:
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text='[]')], stop_reason='end_turn')

        daily_short.search_trending_topics(SimpleNamespace(messages=SimpleNamespace(create=capture)), history)
        captured['max_tokens'] = 2400
        captured['messages'][0]['content'] = captured['messages'][0]['content'].replace(
            'up to five DISTINCT lesson briefs', 'up to ten DISTINCT lesson briefs')
        response = None
        try:
            response = api.messages.create(**captured)
            original = response_json(response)
            print(json.dumps({'original_limit_probe': 'complete',
                              'candidate_count': len(original) if isinstance(original, list) else None,
                              'stop_reason': response.stop_reason,
                              'output_tokens': response.usage.output_tokens}))
        except Exception as error:
            print(json.dumps({'original_limit_probe': 'failed',
                              'reason': safe_failure_details(error, response)}))
    generated = daily_short.search_trending_topics(api, history)
    print(json.dumps({'generated_count': len(generated), 'generated_briefs': generated}, ensure_ascii=False))
    try:
        selected = choose_topic(
            generated + remaining, bank=bank, history=history,
            review=lambda brief: daily_short.review_topic(api, brief, history),
            viable=lambda title: True, min_score=daily_short.TOPIC_MIN_SCORE,
            max_candidates=args.max_reviews, holds=load_visual_holds())
    except TopicHold as error:
        print(json.dumps({'approved': False, 'reason': str(error)}))
        return 1
    print(json.dumps({'approved': True, 'selected': selected.brief}, ensure_ascii=False))
    if args.scripts:
        script_check(api, daily_short, selected, args.scripts)
    return 0


def script_check(api, daily_short, topic, count):
    """Mirror the daily writer: a draft that fails the voice check gets one rewrite with the same feedback."""
    from tools.script_length import sentence_lengths, spoken_words
    daily_short.CITED_RATE_FACTS.clear()
    daily_short.CITED_RATE_FACTS.update(daily_short._cited_rate_facts(topic.brief))
    base = daily_short.get_script_prompt(topic)
    for index in range(1, count + 1):
        result, feedback = {'script_trial': index, 'attempts': []}, ''
        try:
            for attempt in (1, 2):
                prompt = base
                if feedback:
                    prompt += (f"\n\n━━━ IMPORTANT: PREVIOUS ATTEMPT WAS REJECTED ━━━\nReviewer feedback: {feedback}"
                               "\nFix these issues in your new script. Write a DIFFERENT and BETTER script.")
                response = api.messages.create(model='claude-opus-4-6', max_tokens=2500,
                                               messages=[{'role': 'user', 'content': prompt}])
                raw = response.content[0].text.strip()
                if raw.startswith('```'):
                    raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
                match = re.search(r'\{[\s\S]*\}', raw)
                draft = json.loads(match.group() if match else raw)
                voice, english = draft['script_voice'], draft['script_english']
                feedback = daily_short.script_voice_feedback(voice)
                result['attempts'].append({'roman_words': len(voice.split()),
                                           'spoken_words': spoken_words(daily_short.normalize_for_tts(voice)),
                                           'sentence_words': sentence_lengths(voice),
                                           'voice_check': feedback or 'ok', 'script_voice': voice,
                                           'script_english': english})
                if not feedback:
                    prompts = [draft.get(f'video_prompt_{i}', '') for i in range(1, daily_short.VEO_CLIPS_PER_VIDEO + 1)]
                    approved, score, weakest, review = daily_short.review_script(api, voice, english, topic, prompts)
                    result.update(approved=approved, score=score, weakest=weakest, review=review)
                    break
        except Exception as error:
            result['error'] = type(error).__name__
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
