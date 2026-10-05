from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


BANK_PATH = Path(__file__).resolve().parents[1] / 'daily_topic_lessons.json'
VISUAL_HOLDS_PATH = BANK_PATH.parent / 'visual_holds.json'
LESSON_HISTORY_PATH = BANK_PATH.parent / 'lesson_history.json'
VISUAL_HOLD_DAYS = 21
IST = timezone(timedelta(hours=5, minutes=30))
DIMENSIONS = {'buyer_interest', 'freshness', 'learning_value', 'shareability'}
LIVE_FACTS = {}


class TopicHold(RuntimeError):
    pass


def response_json(response):
    stop = getattr(response, 'stop_reason', None)
    if stop == 'max_tokens':
        raise TopicHold('Model response hit its output-token limit; incomplete JSON rejected.')
    if stop not in (None, 'end_turn', 'stop_sequence'):
        raise TopicHold('Model did not complete an ordinary text response.')
    blocks = getattr(response, 'content', [])
    raw = '\n'.join(block.text for block in blocks
                    if isinstance(getattr(block, 'text', None), str)).strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw, count=1)
        raw = re.sub(r'\s*```\s*$', '', raw, count=1)
    if not raw:
        raise TopicHold('Model response contains no text JSON.')
    return json.loads(raw)


def safe_failure_details(error, response=None):
    parts = [type(error).__name__]
    status = getattr(error, 'status_code', None)
    if type(status) is int:
        parts.append(f'http_status={status}')
    if isinstance(error, json.JSONDecodeError):
        parts.append(f'json_line={error.lineno} column={error.colno}')
    if isinstance(error, TopicHold):
        parts.append(str(error))
    stop = getattr(response, 'stop_reason', None)
    if stop in ('end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'):
        parts.append(f'stop_reason={stop}')
    usage = getattr(response, 'usage', None)
    count = getattr(usage, 'output_tokens', None)
    if type(count) is int:
        parts.append(f'output_tokens={count}')
    return '; '.join(parts)


def retryable_failure(error):
    status = getattr(error, 'status_code', None)
    return status not in (400, 401, 402, 403, 404)


class SelectedTopic(str):
    def __new__(cls, brief):
        value = super().__new__(cls, brief['topic'])
        value.brief = brief
        return value


def normalized(text):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', text).casefold()))


def unsupported_shortcut(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    ear = r'\bear\b|\bkaan\b|\bkan ke paas\b|कान'
    yarn = r'carded|combed|ring[ -]?spun|open[ -]?end|कार्डेड|कॉम्ब्ड|कोम्ड'
    rubbing = r'rub|rag[ad]|रगड़|रगड|sound|awaaz|आवाज़'
    if re.search(ear, text) and re.search(yarn, text) and re.search(rubbing, text):
        return 'Ear rubbing is not supplied evidence identifying yarn preparation or spinning.'
    for sentence in re.split(r'[.!?।\n]', text):
        fuzz = re.search(r'fuzz|hair|रोएं|रोएँ|रुए|रुआ', sentence)
        bio = re.search(r'bio[ -]?wash|बायो.?वॉश|बायो.?वाश', sentence)
        negative = re.search(r'not|\bno\b|nahi|nahin|नहीं|नही', sentence)
        inference = re.search(r'means|proves|toh|\bto\b|तो|अगर|if\b', sentence)
        if fuzz and bio and negative and inference:
            return 'Visible fuzz does not establish the absence of a biowash process.'
        stretch = re.search(r'stretch|bounce|recovery|खिंच|खींच|स्ट्रेच', sentence)
        preshrunk = re.search(r'pre[ -]?shr[uia]nk|प्री.?श्रंक|प्री.?श्रिंक', sentence)
        if stretch and preshrunk and inference:
            return 'Stretch recovery does not certify preshrinking or laundering shrinkage.'
    return None


def _check_facts(facts):
    for fact_id, fact in facts.items():
        if not re.fullmatch(r'[a-z0-9_]+', fact_id) or not isinstance(fact, dict):
            raise TopicHold('Invalid topic fact.')
        if not all(isinstance(fact.get(key), str) and fact[key].strip()
                   for key in ('claim', 'source_url', 'source_title', 'checked_on', 'limits')):
            raise TopicHold('Topic fact is missing its evidence or limits.')
        if not fact['source_url'].startswith('https://'):
            raise TopicHold('Topic fact has no primary-source URL.')


def install_live_facts(facts):
    """Today's checked rate facts (tools/current_rates.py) join the reviewed bank for this run only."""
    facts = dict(facts or {})
    _check_facts(facts)
    if any(not key.startswith('rate_') for key in facts):
        raise TopicHold('Only live rate facts may be added at run time.')
    LIVE_FACTS.clear()
    LIVE_FACTS.update(facts)


def load_bank(path=BANK_PATH):
    bank = json.loads(Path(path).read_text(encoding='utf-8'))
    if bank.get('format') != 'daily-topic-facts-v1' or bank.get('reviewed') is not True:
        raise TopicHold('Topic evidence bank is not reviewed.')
    facts = bank.get('facts')
    if not isinstance(facts, dict) or not facts:
        raise TopicHold('Topic evidence bank has no facts.')
    _check_facts(facts)
    bank['facts'] = {**facts, **{key: fact for key, fact in LIVE_FACTS.items() if key not in facts}}
    return bank


def load_topic_history(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        history = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(history, list) or any(not isinstance(item, str) for item in history):
            raise ValueError('Wrong history schema')
        return history
    except (OSError, ValueError) as error:
        raise TopicHold('Topic history is unavailable or invalid; freshness cannot be verified.') from error


def consume_uploaded_topic(path, topic, video_id, *, test_mode=False,
                           new_test_mode=False, single_veo_test=False):
    if test_mode or new_test_mode or single_veo_test:
        return False
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return False
    if not isinstance(topic, str) or not topic.strip():
        raise TopicHold('An uploaded topic must have a nonempty title.')
    path = Path(path)
    history = load_topic_history(path)
    if normalized(topic) in {normalized(title) for title in history}:
        return False
    history.append(str(topic))
    _write_json(path, history)
    try:
        record_published_lesson(topic, video_id, path.parent / LESSON_HISTORY_PATH.name)
    except (OSError, TopicHold) as error:
        print(f'   ⚠️ Published lesson not recorded ({type(error).__name__})')
    return True


def _write_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_lesson_history(path=None):
    """Lessons already published, whatever title they went out under."""
    path = Path(path or LESSON_HISTORY_PATH)
    if not path.exists():
        return []
    try:
        lessons = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(lessons, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('fact_ids'), list)
                or not all(isinstance(item.get(key), str) and item[key].strip() for key in ('topic', 'intent_key'))
                for item in lessons):
            raise ValueError('Wrong lesson history schema')
        return lessons
    except (OSError, ValueError) as error:
        raise TopicHold('Lesson history is unavailable or invalid; freshness cannot be verified.') from error


def record_published_lesson(topic, video_id, path=None, today=None):
    brief = getattr(topic, 'brief', None)
    if not isinstance(brief, dict) or not isinstance(brief.get('intent_key'), str) or not brief['intent_key'].strip():
        return False
    path = Path(path or LESSON_HISTORY_PATH)
    lessons = load_lesson_history(path)
    if any(item.get('video_id') == video_id for item in lessons):
        return False
    lessons.append({'topic': str(topic), 'intent_key': brief['intent_key'],
                    'fact_ids': [key for key in brief.get('fact_ids') or [] if isinstance(key, str)],
                    'lesson': str(brief.get('lesson', '')), 'buyer_decision': str(brief.get('buyer_decision', '')),
                    'video_id': video_id, 'published_on': (today or datetime.now(IST).date()).isoformat()})
    _write_json(path, lessons)
    return True


def active_campaign(bank, today=None):
    """The bank's dated launch campaign while today (India time) is inside its from..until window and, when the
    campaign lists "weekdays" (Mon..Sat), is one of them; else None. DAILY_CAMPAIGN=off on a run switches it off.
    Only its fact ids that exist in the bank count; a campaign without any is ignored."""
    campaign = bank.get('campaign') if isinstance(bank, dict) else None
    if not isinstance(campaign, dict) or os.environ.get('DAILY_CAMPAIGN', '').strip().lower() == 'off':
        return None
    today = today or datetime.now(IST).date()
    day = today.isoformat()
    start, until = campaign.get('from'), campaign.get('until')
    if not (isinstance(start, str) and isinstance(until, str) and start <= day <= until):
        return None
    weekdays = campaign.get('weekdays')
    if isinstance(weekdays, list) and ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[today.weekday()] not in weekdays:
        return None
    facts = bank.get('facts') or {}
    ids = [key for key in campaign.get('fact_ids') or [] if isinstance(key, str) and key in facts]
    return {**campaign, 'fact_ids': ids} if ids else None


def cites_campaign(brief, campaign):
    if not campaign or not isinstance(brief, dict) or not isinstance(brief.get('fact_ids'), list):
        return False
    return any(key in campaign['fact_ids'] for key in brief['fact_ids'])


def campaign_text(topic, key, today=None):
    """The active campaign's text `key` when `topic` is one of its lessons, else ''."""
    campaign = active_campaign(load_bank(), today)
    if campaign and cites_campaign(getattr(topic, 'brief', None), campaign):
        value = campaign.get(key)
        return value.strip() if isinstance(value, str) else ''
    return ''


def campaign_clips(brief, campaign, assets_dir):
    """The ONE real screen recording for a campaign lesson: the clip of the first fact it cites, in its own order, with
    the campaign's "generic_clip_facts" (broad facts like the launch itself) used only when no more specific cited fact
    has a clip. Each clip is a complete demonstration of its fact and plays once over the whole narration, because the
    final reviews compare the picture with the spoken lesson. [] when no cited fact has a clip file."""
    if not cites_campaign(brief, campaign):
        return []
    mapping = campaign.get('clips') if isinstance(campaign.get('clips'), dict) else {}
    generic = set(campaign.get('generic_clip_facts') or [])
    cited = [key for key in brief['fact_ids'] if key not in generic] + [key for key in brief['fact_ids'] if key in generic]
    for key in cited:
        for name in mapping.get(key) or []:
            path = Path(assets_dir) / f'{name}.mp4'
            if isinstance(name, str) and re.fullmatch(r'[a-z0-9_]{1,40}', name) and path.is_file():
                return [str(path)]
    return []


def campaign_prompt(campaign):
    """The brainstorm section for a dated launch campaign (daily_topic_lessons.json "campaign"), or ''."""
    if not campaign:
        return ''
    return (f"\nLAUNCH CAMPAIGN ({campaign.get('label', 'campaign')}, until {campaign.get('until')}): {campaign.get('why', '')}\n"
            'In this period every brief must teach one real buyer lesson that cites at least one of these fact ids:\n'
            f"{json.dumps(campaign['fact_ids'], ensure_ascii=False)}\n"
            'Real problems buyers hit: a low-resolution sheet, a Canva PNG export, a background that is not\n'
            'transparent, many designs on one gang sheet, what "pieces" means, and pressing. Teach the reason\n'
            'and the decision. The spoken script stays a lesson with no sales line and no website name: the end\n'
            'card and the description carry the launch.\n')


def choose_with_campaign(candidates, *, bank, **options):
    """During an active campaign, first choose only among lessons that cite a campaign fact; when none of them
    passes the same quality gate, fall back to the ordinary choice. Without a campaign this is choose_topic."""
    campaign = active_campaign(bank)
    if campaign:
        launch = [brief for brief in candidates if cites_campaign(brief, campaign)]
        if launch:
            try:
                return choose_topic(launch, bank=bank, **options)
            except TopicHold as error:
                print(f"   {campaign.get('label', 'Campaign')}: no lesson passed ({error}); ordinary topic choice")
    return choose_topic(candidates, bank=bank, **options)


def brainstorming_context(bank, history, lessons=None):
    titles = {normalized(title) for title in history if isinstance(title, str)}
    completed = [brief for brief in bank.get('seed_lessons', [])
                 if {normalized(value) for value in (brief.get('topic'), brief.get('published_as'))
                     if isinstance(value, str) and value.strip()} & titles]
    known = {normalized(brief.get('intent_key', '')) for brief in completed}
    for item in load_lesson_history() if lessons is None else lessons:
        if normalized(item['topic']) in titles and normalized(item['intent_key']) not in known:
            known.add(normalized(item['intent_key']))
            completed.append({'lesson': '', 'buyer_decision': '', **item})
    used = {key for brief in completed for key in brief.get('fact_ids', [])}
    preferred = [key for key in bank['facts'] if key not in used]
    campaign = active_campaign(bank)
    if campaign:
        # A dated launch campaign (owner request) puts its own unused facts first; the rest keep their order.
        preferred = ([key for key in preferred if key in campaign['fact_ids']]
                     + [key for key in preferred if key not in campaign['fact_ids']])
    ordered = {key: bank['facts'][key] for key in preferred}
    ordered.update({key: fact for key, fact in bank['facts'].items() if key in used})
    return {'facts': ordered, 'preferred_fact_ids': preferred, 'campaign': campaign,
            'completed_lessons': [{key: brief[key] for key in (
                'topic', 'lesson', 'buyer_decision', 'intent_key', 'fact_ids')} for brief in completed]}


def validate_brief(brief, bank, history=()):
    if not isinstance(brief, dict):
        raise TopicHold('A bare topic has no reviewed lesson evidence.')
    for key in ('topic', 'buyer_question', 'lesson', 'buyer_decision', 'intent_key'):
        if not isinstance(brief.get(key), str) or not brief[key].strip() or len(brief[key]) > 1200:
            raise TopicHold('Topic brief is missing a specific lesson, question or decision.')
    if len(brief['lesson'].split()) < 12:
        raise TopicHold('Topic needs an explanation, not a checklist label.')
    ids = brief.get('fact_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(
            not isinstance(key, str) or key not in bank['facts'] for key in ids):
        raise TopicHold('Topic cites missing or unknown reviewed facts.')
    if len(set(ids)) != len(ids):
        raise TopicHold('Duplicate fact references.')
    if normalized(brief['topic']) in {normalized(t) for t in history if isinstance(t, str)}:
        raise TopicHold('Topic repeats an existing title.')
    completed = brainstorming_context(bank, history)['completed_lessons']
    if normalized(brief['intent_key']) in {normalized(item['intent_key']) for item in completed}:
        raise TopicHold('Topic repeats a completed reviewed lesson intent.')
    shortcut = unsupported_shortcut(' '.join(brief[key] for key in ('topic', 'lesson', 'buyer_decision')))
    if shortcut:
        raise TopicHold(shortcut)
    from tools.current_rates import allowed_amounts, unsupported_amounts
    evidence = {key: bank['facts'][key] for key in ids}
    text = ' '.join(brief[key] for key in ('topic', 'buyer_question', 'lesson', 'buyer_decision'))
    if unsupported_amounts(text, allowed_amounts(evidence)):
        raise TopicHold('Topic quotes a rupee amount that is not an exact cited website rate.')
    return {**brief, 'evidence': evidence}


def review_result(value):
    if not isinstance(value, dict):
        raise TopicHold('Topic review must be a JSON object.')
    scores = value.get('scores')
    if not isinstance(scores, dict) or set(scores) != DIMENSIONS or any(
            type(score) is not int or not 0 <= score <= 10 for score in scores.values()):
        raise TopicHold('Topic review needs four integer dimension scores.')
    total = value.get('score')
    if type(total) is not int or not 0 <= total <= 40 or total != sum(scores.values()):
        raise TopicHold('Topic score does not match the four dimensions.')
    if not isinstance(value.get('feedback'), str) or not value['feedback'].strip():
        raise TopicHold('Topic review feedback is missing.')
    if any(type(value.get(key)) is not bool for key in ('facts_supported', 'teaches_specific_lesson')):
        raise TopicHold('Topic review must explicitly assess support and learning.')
    duplicate = value.get('duplicate_of', False)
    if duplicate is not None and (not isinstance(duplicate, str) or not duplicate.strip()):
        raise TopicHold('Topic review must explicitly assess duplicate intent.')
    if duplicate is not None or not value['facts_supported'] or not value['teaches_specific_lesson']:
        return 0, value['feedback']
    return total, value['feedback']


def load_visual_holds(path=None, today=None):
    """Lessons whose AI visuals failed the technical review recently (AI video could not draw them)."""
    try:
        entries = json.loads(Path(path or VISUAL_HOLDS_PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    today = today or datetime.now(IST).date()
    active = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            age = (today - datetime.strptime(entry['date'], '%Y-%m-%d').date()).days
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= age < VISUAL_HOLD_DAYS:
            active.append(entry)
    return active


def visual_hold_reason(brief, holds):
    facts = {key for key in brief.get('fact_ids') or [] if not key.startswith('rate_')}
    for entry in holds:
        if facts & set(entry.get('fact_ids') or []) or normalized(brief.get('intent_key', '')) == normalized(entry.get('intent_key', '')):
            return f"Visuals for this lesson failed the technical review on {entry['date']}; held for {VISUAL_HOLD_DAYS} days."
    return None


def record_visual_hold(brief, report, path=None, today=None):
    """Hold a lesson only after a completed visual verdict found the technical demonstration wrong."""
    assessment = (report or {}).get('assessment') or {}
    if not isinstance(brief, dict) or (report or {}).get('state') != 'fail' \
            or assessment.get('technical_visuals_match_facts') is not False:
        return False
    path = Path(path or VISUAL_HOLDS_PATH)
    try:
        entries = json.loads(path.read_text(encoding='utf-8'))
        entries = entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        entries = []
    entries.append({'date': (today or datetime.now(IST).date()).isoformat(),
                    'topic': str(brief.get('topic', ''))[:200], 'intent_key': str(brief.get('intent_key', ''))[:120],
                    'fact_ids': [key for key in brief.get('fact_ids') or [] if not key.startswith('rate_')],
                    'reason': str(assessment.get('summary') or '')[:300]})
    path.write_text(json.dumps(entries[-60:], ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return True


def record_review_hold(brief, reason, path=None, today=None, source='final AI review'):
    """The final AI review (or the clip check) rejected this lesson's own footage: skip the lesson like a failed
    visual review."""
    if not isinstance(brief, dict) or not brief.get('intent_key'):
        return False
    path = Path(path or VISUAL_HOLDS_PATH)
    try:
        entries = json.loads(path.read_text(encoding='utf-8'))
        entries = entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        entries = []
    entries.append({'date': (today or datetime.now(IST).date()).isoformat(),
                    'topic': str(brief.get('topic', ''))[:200], 'intent_key': str(brief.get('intent_key', ''))[:120],
                    'fact_ids': [key for key in brief.get('fact_ids') or [] if not key.startswith('rate_')],
                    'reason': (f'{source}: ' + str(reason or ''))[:300]})
    path.write_text(json.dumps(entries[-60:], ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return True

def choose_topic(candidates, *, bank, history, review, viable, min_score=25, max_candidates=5, holds=()):
    pending = []
    seen = set()
    for raw in candidates:
        try:
            brief = validate_brief(raw, bank, history)
        except TopicHold as error:
            print(f'   Topic excluded: {error}')
            continue
        held = visual_hold_reason(brief, holds)
        if held:
            print(f'   Topic excluded: {held}')
            continue
        key = normalized(brief['intent_key'])
        if key in seen:
            continue
        seen.add(key)
        pending.append(brief)
    # Blog viability orders otherwise valid lessons; it cannot waive a content gate.
    ranked = sorted(pending[:10], key=lambda brief: not viable(brief['topic']))
    approved = []
    # A second round of reviews runs only when the first round approved nothing.
    for index, brief in enumerate(ranked):
        if index >= max_candidates * (1 if approved else 2):
            break
        try:
            score, feedback = review(brief)
        except Exception as error:
            score, feedback = 0, f'Topic review unavailable; held ({safe_failure_details(error)}).'
        print(f"   Topic review: {brief['topic'][:70]} → {score}/40 ({feedback})")
        if type(score) is int and min_score <= score <= 40:
            approved.append((score, {**brief, 'selection_review': {
                'score': score, 'minimum_score': min_score, 'feedback': feedback}}))
    if not approved:
        raise TopicHold('No distinct, evidence-supported topic passed the quality gate; no script or media should be generated.')
    return SelectedTopic(max(approved, key=lambda pair: pair[0])[1])


def evidence_prompt(topic):
    brief = getattr(topic, 'brief', None)
    if not brief:
        return ''
    from tools.current_rates import allowed_amounts
    rates = allowed_amounts(brief.get('evidence'))
    allowed = ('\nALLOWED RUPEE AMOUNTS (exact; anything else is rejected): '
               + ', '.join(f'₹{value}' for value in sorted(rates)) + '\n') if rates else ''
    return ('\nAPPROVED LESSON AND ITS PRIMARY-SOURCE FACTS:\n'
            + json.dumps(brief, ensure_ascii=False)
            + '\nTeach the mechanism or distinction, then its buyer consequence. '
            'Use only the supplied facts; historical titles and AI imagery are not proof. '
            'Do not replace the explanation with a list of things to check. '
            "Obey every cited fact's limits word for word. A rate_ fact names one of our own products: never say "
            'or imply that product has a drawback another fact describes (odour, pilling, skew, shrinkage), and '
            "never rank products by a property a cited fact's limits forbid ranking. "
            'A rupee amount may appear only exactly as written in a cited rate_ fact (the current '
            'website rate, before GST) or as the difference between two such amounts, always in digits '
            'with the ₹ sign. Never round it or add any other price, discount, threshold, guarantee '
            'or process-identification trick.\n' + allowed)
