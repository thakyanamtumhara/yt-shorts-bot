import json
from pathlib import Path
import re
import unicodedata


BANK_PATH = Path(__file__).resolve().parents[1] / 'daily_topic_lessons.json'
DIMENSIONS = {'buyer_interest', 'freshness', 'learning_value', 'shareability'}


class TopicHold(RuntimeError):
    pass


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


def load_bank(path=BANK_PATH):
    bank = json.loads(Path(path).read_text(encoding='utf-8'))
    if bank.get('format') != 'daily-topic-facts-v1' or bank.get('reviewed') is not True:
        raise TopicHold('Topic evidence bank is not reviewed.')
    facts = bank.get('facts')
    if not isinstance(facts, dict) or not facts:
        raise TopicHold('Topic evidence bank has no facts.')
    for fact_id, fact in facts.items():
        if not re.fullmatch(r'[a-z0-9_]+', fact_id) or not isinstance(fact, dict):
            raise TopicHold('Invalid topic fact.')
        if not all(isinstance(fact.get(key), str) and fact[key].strip()
                   for key in ('claim', 'source_url', 'source_title', 'checked_on', 'limits')):
            raise TopicHold('Topic fact is missing its evidence or limits.')
        if not fact['source_url'].startswith('https://'):
            raise TopicHold('Topic fact has no primary-source URL.')
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
    shortcut = unsupported_shortcut(' '.join(brief[key] for key in ('topic', 'lesson', 'buyer_decision')))
    if shortcut:
        raise TopicHold(shortcut)
    return {**brief, 'evidence': {key: bank['facts'][key] for key in ids}}


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


def choose_topic(candidates, *, bank, history, review, viable, min_score=25, max_candidates=5):
    pending = []
    seen = set()
    for raw in candidates:
        try:
            brief = validate_brief(raw, bank, history)
        except TopicHold as error:
            print(f'   Topic excluded: {error}')
            continue
        key = normalized(brief['intent_key'])
        if key in seen:
            continue
        seen.add(key)
        pending.append(brief)
    # Blog viability orders otherwise valid lessons; it cannot waive a content gate.
    ranked = sorted(pending[:10], key=lambda brief: not viable(brief['topic']))
    approved = []
    for brief in ranked[:max_candidates]:
        try:
            score, feedback = review(brief)
        except Exception:
            score, feedback = 0, 'Topic review unavailable; held.'
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
    return ('\nAPPROVED LESSON AND ITS PRIMARY-SOURCE FACTS:\n'
            + json.dumps(brief, ensure_ascii=False)
            + '\nTeach the mechanism or distinction, then its buyer consequence. '
            'Use only the supplied facts; historical titles and AI imagery are not proof. '
            'Do not replace the explanation with a list of things to check. '
            'Do not add prices, thresholds, guarantees or process-identification tricks.\n')
