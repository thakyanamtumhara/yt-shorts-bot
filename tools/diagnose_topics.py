#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Review topic candidates without media or publishing.')
    parser.add_argument('--max-reviews', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--compare-original', action='store_true',
                        help='Also make one diagnostic call with the old ten-brief/2400-token limits.')
    args = parser.parse_args(argv)
    if not os.environ.get('ANTHROPIC_API_KEY'):
        parser.error('ANTHROPIC_API_KEY is required')

    import anthropic
    import daily_short
    from tools.daily_topic_selection import (
        TopicHold, choose_topic, load_bank, load_topic_history, validate_brief,
        response_json, safe_failure_details,
    )

    history = load_topic_history(ROOT / 'topic_history.json')
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
            'up to three DISTINCT lesson briefs', 'up to ten DISTINCT lesson briefs')
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
            max_candidates=args.max_reviews)
    except TopicHold as error:
        print(json.dumps({'approved': False, 'reason': str(error)}))
        return 1
    print(json.dumps({'approved': True, 'selected': selected.brief}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
