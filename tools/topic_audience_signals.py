import math
import re
from datetime import datetime, timezone


MIN_SNAPSHOT_HOURS = 48
MAX_SNAPSHOT_HOURS = 96
MIN_REACH = 500
MAX_PUBLICATION_AGE_DAYS = 60
SUBJECTS = {
    'fabric_weight_gsm': r'\bgsm\b|जी\s*एस\s*एम|जीएसएम',
    'collar_construction': r'collar|कॉलर|कालर|polo|पोलो',
    'garment_labelling': r'\bmrp\b|label|tag|लेबल|टैग',
    'biowash_surface_finishing': r'bio.?wash|बायो.?वॉश|बायो.?वाश',
    'fit_and_proportions': r'oversiz|boxy|drop.?shoulder|ओवरसाइज|बॉक्सी',
    'printing_process': r'\bdtf\b|\bdtg\b|sublimat|screen.?print|प्रिंट',
    'shrinkage_and_washing': r'shrink|wash|सिकुड़|सिकुड|धुलाई',
    'terry_and_fleece': r'terry|fleece|टेरी|फ्लीस',
}


def prompt_signals(report):
    return {**report, 'leads': [{key: value for key, value in row.items()
                               if key != 'historical_title_unverified'} for row in report['leads']]}


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp needs a timezone')
    return parsed.astimezone(timezone.utc)


def _count(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value != int(value):
        raise ValueError('Invalid observed count')
    return int(value)


def topic_interest(records, n=5, *, now=None, excluded_media_ids=()):
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('Instagram history must be an array of records')
    if type(n) is not int or n < 1:
        raise ValueError('Requested count must be positive')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Analysis timestamp needs a timezone')
    rejected = set(excluded_media_ids)
    excluded = dict(unchecked=0, invalid=0, old_publication=0, known_rejected=0,
                    outside_window=0, low_reach=0, trial=0, no_interactions=0)
    seen = set()
    candidates = []
    for row in records:
        if row.get('checked') is not True or row.get('check_failed'):
            excluded['unchecked'] += 1
            continue
        try:
            media_id = row['media_id']
            if not isinstance(media_id, str) or not media_id.isdigit() or media_id in seen:
                raise ValueError('Invalid or duplicate media ID')
            seen.add(media_id)
            published, checked = _time(row['published_at']), _time(row['checked_at'])
            age = (checked - published).total_seconds() / 3600
            if age < 0 or checked > now:
                raise ValueError('Measurement dates are inconsistent')
            recorded_age = row.get('hours_since_publish')
            if recorded_age is not None and (type(recorded_age) not in (int, float)
                    or not math.isfinite(recorded_age) or abs(recorded_age - age) > 1):
                raise ValueError('Measurement-age fields disagree')
            reach, shares, saves = (_count(row.get(key)) for key in ('reach', 'shares', 'saves'))
            if not isinstance(row.get('title'), str) or not row['title'].strip():
                raise ValueError('Missing subject title')
            trial = row.get('trial')
            if trial is not None and type(trial) is not bool:
                raise ValueError('Invalid trial status')
        except (KeyError, ValueError, TypeError, OverflowError):
            excluded['invalid'] += 1
            continue
        if media_id in rejected:
            excluded['known_rejected'] += 1
        elif (now - published).total_seconds() > MAX_PUBLICATION_AGE_DAYS * 86400:
            excluded['old_publication'] += 1
        elif not MIN_SNAPSHOT_HOURS <= age < MAX_SNAPSHOT_HOURS:
            excluded['outside_window'] += 1
        elif reach < MIN_REACH:
            excluded['low_reach'] += 1
        elif trial is True:
            excluded['trial'] += 1
        elif shares + saves == 0:
            excluded['no_interactions'] += 1
        else:
            candidates.append({
                'media_id': media_id, 'historical_title_unverified': row['title'],
                'subject_tags_from_title': [tag for tag, pattern in SUBJECTS.items()
                                            if re.search(pattern, row['title'], re.I)],
                'incident_framing_unverified': bool(re.search(
                    r'₹|\bloss\b|\blost\b|rejected|wasted|नुकसान|रिजेक्ट|बर्बाद', row['title'], re.I)),
                'published_at': published.isoformat(), 'checked_at': checked.isoformat(),
                'measurement_age_hours': round(age, 2), 'reach': reach,
                'shares': shares, 'saves': saves,
                'shares_per_1000_reached': round(1000 * shares / reach, 3),
                'saves_per_1000_reached': round(1000 * saves / reach, 3),
                'trial_status': 'not_trial' if trial is False else 'unknown',
                'paid_organic_split': 'unknown',
            })
    candidates.sort(key=lambda row: (row['shares'] / row['reach'], row['saves'] / row['reach'], row['media_id']), reverse=True)
    return {
        'format': 'topic-audience-signals-v1',
        'checked_at': now.isoformat(),
        'purpose': 'Historical subject-interest leads only; not a MAIN selection or proof of a claim.',
        'policy': {'measurement_age_hours': [MIN_SNAPSHOT_HOURS, MAX_SNAPSHOT_HOURS],
                   'upper_age_bound_exclusive': True,
                   'publication_lookback_days': MAX_PUBLICATION_AGE_DAYS,
                   'minimum_reach': MIN_REACH, 'known_trials_excluded': True,
                   'chosen_analysis_window_not_platform_rule': True, 'raw_views_fallback': False},
        'input_count': len(records), 'eligible_count': len(candidates), 'excluded': excluded,
        'leads': candidates[:n],
        'limits': [
            '48–96 hours is an approximate early-life window, not matched exact-day observations.',
            'Missing trial status remains unknown; paid and organic traffic are not separated.',
            'Shares and saves are event counts, not unique buyers, conversion rates or causal effects.',
            'Historical titles can contain false claims. Reuse the subject only with reviewed facts.',
            'Title-derived subject tags are approximate; incident-framed posts cannot isolate interest in the teaching from interest in the drama.',
            'Known rejected source identities are excluded; this does not prohibit teaching their subject with new evidence.',
            'Return fewer leads when evidence is sparse; never replace missing evidence with raw view totals.',
        ],
    }
