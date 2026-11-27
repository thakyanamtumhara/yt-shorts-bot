"""Picks one searched buyer question and writes its answer Short from today's facts.

Two model calls: the writer (question, script, pictures, titles) and a separate auditor that reads
every spoken line against the cited facts and their limits. Code checks sit around both: rupee
amounts, lengths, known facts, real pictures, banned claims, no repeat. A plan that fails goes back
to the writer with the exact reasons, at most ROUNDS times; then the run stops without any spend on
voice or video.
"""

import json
import os
import re

from tools.current_rates import loose_rupee_words, money_amounts
from tools.script_length import voice_text_feedback
from tools.answer_short.facts import allowed_amounts
from tools.answer_short import screens

ROUNDS = 4
MODEL_LINES = ('fable', 'opus')
FALLBACK_MODEL = 'claude-opus-5-5'
BANNED = re.compile(
    r'cheapest|sabse sast|lowest price|best quality|no\.?\s?1\b|number one|guarantee|guaranteed|pakka milega|'
    r'free delivery|free sample|100% original|limited offer|hurry|varsity|boxy|loss ho gaya|barbaad|scam|fraud|'
    r'dosto|doston|namaskar|swagat', re.I)
STYLE = """VOICE: Ketu, owner of a T-shirt wholesale business in Delhi, talking to a buyer across the counter.
Roman Hinglish exactly as he speaks: short plain sentences, Hindi grammar with the English business words buyers
use (wholesale, rate, GSM, order, sample, delivery, website, print, bulk, piece, size, colour). First person:
"hamara", "hum", "main". No greeting, no "dosto", no hype, no formal Hindi (never: uplabdh, gunvatta, kripya,
vishesh), no English sentences, no emoji, no brackets. Numbers as digits; money always with the ₹ sign ("₹205").
He answers first and explains after. He never bad-mouths other sellers and never promises what the facts do not say."""


class WriteHold(RuntimeError):
    pass


def pick_model(client):
    wanted = os.environ.get('ANSWER_MODEL', '').strip()
    if wanted:
        return wanted
    try:
        names = [model.id for model in client.models.list(limit=100).data]
    except Exception as error:
        print(f'   model list failed ({type(error).__name__}); using {FALLBACK_MODEL}')
        return FALLBACK_MODEL
    for line in MODEL_LINES:
        best = sorted((name for name in names if re.fullmatch(rf'claude-{line}-\d+-\d+', name)),
                      key=lambda name: [int(part) for part in re.findall(r'\d+', name)], reverse=True)
        if best:
            return best[0]
    return FALLBACK_MODEL


def ask_json(client, model, prompt, max_tokens=20000):
    """One JSON object from the model. Newer models think first, so the allowance is generous; an answer with no
    JSON in it is asked for once more."""
    for attempt in (1, 2):
        response = client.messages.create(model=model, max_tokens=max_tokens, messages=[{'role': 'user', 'content': prompt}])
        text = '\n'.join(block.text for block in response.content if isinstance(getattr(block, 'text', None), str)).strip()
        start, end = text.find('{'), text.rfind('}')
        if getattr(response, 'stop_reason', None) != 'max_tokens' and start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
        print(f"   model answer {attempt} unreadable (stop {getattr(response, 'stop_reason', '?')}, {len(text)} chars)")
    raise WriteHold('the model gave no readable JSON twice')


def open_questions(bank, history, limit=70):
    done = {str(item.get('search_phrase', '')).strip().lower() for item in history}
    return [entry for entry in bank['queries'] if entry['q'] not in done][:limit]


def writer_prompt(sheet, questions, history, feedback=None, previous=None):
    facts = {key: {'claim': fact['claim'], 'limits': fact['limits']} for key, fact in sheet['facts'].items()}
    pictures = {key: {'folder': folder, 'videos': len(sheet['gallery'][folder]['videos']),
                      'photos': len(sheet['gallery'][folder]['photos'])}
                for key, folder in sheet['visuals'].items()}
    recent = [{'search_phrase': item.get('search_phrase'), 'intent_key': item.get('intent_key'), 'title': item.get('title')}
              for item in history[-60:]]
    pages = screens.offered(sheet)
    redo = ''
    if feedback:
        redo = ('\nYOUR LAST PLAN WAS REFUSED. Fix exactly these points and return the whole corrected plan. '
                'Keep the same question unless a point says it cannot be answered from the facts:\n- '
                + '\n- '.join(feedback) + '\nLAST PLAN:\n' + json.dumps(previous, ensure_ascii=False) + '\n')
    return f"""You plan one 30 to 35 second vertical video (YouTube Short / Instagram Reel) for Own Knitted Blank Wears
(sale91.com), a plain T-shirt and hoodie wholesaler in Delhi. Today is {sheet['today']}.

GOAL: answer ONE thing Indian buyers are really typing into YouTube and Google, so the video is found by search and
is worth saving or forwarding. Viewers: people starting a T-shirt printing business or clothing brand, printers,
resellers, bulk buyers.

SEARCHED QUESTIONS (real autocomplete suggestions, most seen first; yt / google = where it appeared):
{json.dumps(questions, ensure_ascii=False)}

FACTS YOU MAY USE (nothing else is known; obey every "limits" line word for word):
{json.dumps(facts, ensure_ascii=False)}

REAL PICTURES (our own product videos and photos; a sentence can only show one of these fact ids):
{json.dumps(pictures, ensure_ascii=False)}

WEBSITE SCREENS (a real page of our website, shown as a phone screen while one sentence is spoken; fact id → what
the page shows):
{json.dumps(pages, ensure_ascii=False)}

ALREADY ANSWERED (do not answer the same buyer decision again, even under another phrase):
{json.dumps(recent, ensure_ascii=False)}

CHOOSING: pick the question with the most buyer intent that the facts above answer COMPLETELY and that has not
been answered. Prefer price, where-to-buy, how-to-start, how-to-order, delivery and "which one" questions over
definitions. A city question is fine only when the facts answer it (we stock in Delhi and courier across India).
If a question needs a fact that is not above, do not pick it. Other things equal, prefer a question whose main
product has videos in REAL PICTURES: moving pictures hold viewers, photos alone do not. A question about the website
itself (size chart, how to order, DTF) whose fact has a WEBSITE SCREEN is as good as one with videos.

{STYLE}

SCRIPT SHAPE (hard limits): 6 or 7 sentences, 72-88 words in all, each sentence at most 14 words.
1. HOOK: the viewer's own question in his words, with the key number when there is one. It must make sense alone.
2. The direct answer, immediately. No build-up.
3-5. The two or three facts that change what the buyer does (what the rate depends on, sample or bulk, delivery).
6. One practical next step.
Last: a real question the viewer can answer in one or two words in the comments (never "comment karo" alone).
Say "website" at most once and never spell a web address. No rupee amount unless it is written in a cited fact
(a rate, the difference of two cited rates, or a cited rate times 10, 20, 25, 50 or 100 pieces, which you must
multiply correctly). Never state a selling price, a margin, a profit or an investment figure.

Return ONE JSON object, no markdown:
{{
 "search_phrase": "<one phrase copied exactly from SEARCHED QUESTIONS>",
 "also_matches": ["<up to 4 other phrases from the list this same video answers>"],
 "intent_key": "<stable english snake_case for the buyer decision>",
 "fact_ids": ["<1-5 fact ids the script relies on>"],
 "hook_text": "<on-screen hook, at most 7 words, Roman letters, the question or its number>",
 "cover": ["<line 1: 1-3 words, the subject, CAPITALS>", "<line 2: the question, at most 5 words, ends with ?>"],
 "lines": [{{"say": "<one sentence>", "show": "<fact id from REAL PICTURES>", "card": "<rate fact id whose rate card is on screen during this sentence, or empty>", "screen": "<fact id from WEBSITE SCREENS whose page is on screen during this sentence, or empty>"}}],
 "comment_question": "<the closing question, as text for the pinned comment>",
 "title_youtube": "<at most 85 characters, starts with or contains the search phrase naturally, Hinglish/English>",
 "title_instagram": "<at most 90 characters, hook first>",
 "description": "<2-3 plain sentences in English repeating the answer with the search phrase and 2-3 of also_matches worked in naturally; no hashtags, no links>",
 "tags": ["<8-12 search phrases people would type, lower case>"]
}}
A "card" shows that product's live rates in big digits: put it only on the sentence that says that product's rate
(at most 3 lines in the whole video carry a card; every other line has "card": "").
"show" rules: a line that names a product shows THAT product. A line about ordering, delivery or the closing
question may show another product of the same kind of garment that has videos (a T-shirt for a T-shirt video,
never a hoodie), so the video is not photos only.
"screen" rules: when a cited fact has a WEBSITE SCREEN, put it on the ONE sentence that tells the buyer what that
page shows or what to do there (the size chart sentence, the "order on the website" sentence, the DTF sentence).
At most 2 lines carry a screen; never line 1, never the last line, never a line with a card; the fact id must be
in "fact_ids". Every other line has "screen": "". A screen line still needs its "show" (for a size chart, "show"
is the product whose chart it is).{redo}"""


def audit_prompt(plan, sheet):
    cited = {key: {'claim': sheet['facts'][key]['claim'], 'limits': sheet['facts'][key]['limits']}
             for key in plan['fact_ids']}
    spoken = {str(index + 1): line['say'] for index, line in enumerate(plan['lines'])}
    other = {key: plan[key] for key in ('hook_text', 'cover', 'comment_question', 'title_youtube', 'title_instagram', 'description')}
    return f"""You are the fact checker for a public business video. Be strict and literal.

CITED FACTS (the only things known to be true):
{json.dumps(cited, ensure_ascii=False)}

SPOKEN LINES (Roman Hinglish):
{json.dumps(spoken, ensure_ascii=False)}

OTHER PUBLIC TEXT:
{json.dumps(other, ensure_ascii=False)}

List every place where the video says or implies something a cited fact does not state, breaks a fact's "limits",
does wrong arithmetic, promises a result, date, stock, quality rank or saving, names a product or place the facts do
not, or is unnatural or wrong Hindi/Hinglish that a Delhi wholesaler would not say. A plain restatement of a fact is
fine. The closing question to viewers is not a claim. Also check the first line is a question a buyer would search
and the second line answers it directly.

Return ONE JSON object: {{"breaches": [{{"where": "<line number or field name>", "problem": "<what is wrong and the exact fix>"}}]}}
Return {{"breaches": []}} when everything is supported."""


def code_problems(plan, sheet, questions, history):
    """Everything code can prove wrong, as sentences the writer can act on."""
    problems = []
    facts = sheet['facts']
    phrases = {entry['q'] for entry in questions}
    for key in ('search_phrase', 'intent_key', 'hook_text', 'comment_question', 'title_youtube', 'title_instagram', 'description'):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            problems.append(f'"{key}" is missing')
    if problems:
        return problems
    if plan['search_phrase'].strip().lower() not in phrases:
        problems.append('"search_phrase" must be copied exactly from SEARCHED QUESTIONS')
    if plan['intent_key'] in {item.get('intent_key') for item in history}:
        problems.append(f'intent_key "{plan["intent_key"]}" was already answered; pick another question')
    cited = plan.get('fact_ids')
    if not isinstance(cited, list) or not 1 <= len(cited) <= 5 or any(key not in facts for key in cited):
        return problems + ['"fact_ids" must be 1-5 ids that exist in FACTS']
    lines = plan.get('lines')
    if not isinstance(lines, list) or not 6 <= len(lines) <= 7 or any(
            not isinstance(line, dict) or not isinstance(line.get('say'), str) or not line['say'].strip() for line in lines):
        return problems + ['"lines" must be 6 or 7 objects with a "say" sentence']
    for index, line in enumerate(lines, 1):
        words = len(line['say'].split())
        if words > 15:
            problems.append(f'line {index} has {words} words; at most 14')
        if not re.search(r'[.!?]$', line['say'].strip()):
            problems.append(f'line {index} must end with . ? or !')
        if len(re.findall(r'[.!?](?:\s|$)', line['say'].strip())) > 1:
            problems.append(f'line {index} holds more than one sentence; one sentence per line')
        if line.get('show') not in sheet['visuals']:
            problems.append(f'line {index} "show" must be a fact id from REAL PICTURES')
        card = line.get('card') or ''
        if card and (card not in sheet['cards'] or card not in cited):
            problems.append(f'line {index} "card" must be a cited rate fact id')
    if sum(1 for line in lines if line.get('card')) > 3:
        problems.append('at most 3 lines may carry a rate card; keep it on the lines that say the rate')
    if not lines[0]['say'].strip().endswith('?'):
        problems.append('line 1 (the hook) must be the buyer question and end with ?')
    if not lines[-1]['say'].strip().endswith('?'):
        problems.append('the last line must be a question to the viewer and end with ?')
    script = ' '.join(line['say'].strip() for line in lines)
    spoken = sheet['normalize'](script) if callable(sheet.get('normalize')) else script
    length = voice_text_feedback(script, spoken)
    if length:
        problems.append(length)
    cover = plan.get('cover')
    if (not isinstance(cover, list) or len(cover) != 2 or any(not isinstance(part, str) or not part.strip() for part in cover)
            or len(cover[0].split()) > 3 or len(cover[1].split()) > 5 or not cover[1].strip().endswith('?')):
        problems.append('"cover" must be [1-3 word subject, a question of at most 5 words ending with ?]')
    if len(plan['hook_text'].split()) > 7:
        problems.append('"hook_text" is at most 7 words')
    if len(plan['title_youtube']) > 85 or len(plan['title_instagram']) > 90:
        problems.append('titles are too long')
    if not isinstance(plan.get('tags'), list) or not 6 <= len(plan['tags']) <= 14:
        problems.append('"tags" must be 8-12 phrases')
    public = ' '.join([script, plan['hook_text'], ' '.join(cover) if isinstance(cover, list) else '', plan['comment_question'],
                       plan['title_youtube'], plan['title_instagram'], plan['description'], ' '.join(map(str, plan.get('tags') or []))])
    allowed = allowed_amounts(facts, cited)
    wrong = sorted({value for value in money_amounts(public) if value not in allowed}, key=str)
    if wrong:
        problems.append(f'rupee amounts not in the cited facts: {wrong}; allowed today: {sorted(allowed)}')
    if loose_rupee_words(public):
        problems.append('write every rupee amount in digits with the ₹ sign')
    banned = sorted({match.group(0).lower() for match in BANNED.finditer(public)})
    if banned:
        problems.append(f'remove these words or claims: {banned}')
    if re.search(r'https?://|www\.|\.com|\.in\b', script, re.I):
        problems.append('never say a web address in the spoken lines; say "website"')
    if len(re.findall(r'\bwebsite\b', script, re.I)) > 1:
        problems.append('say "website" at most once')
    if re.search(r'[\[\]<>{}#*_]', script) or re.search(r'[ऀ-ॿ]', script):
        problems.append('spoken lines are plain Roman Hinglish: no Devanagari, brackets or symbols')
    return problems


def make_plan(client, sheet, bank, history, log=print, only=''):
    """`only` = one searched question to answer (a hand test run); empty = the writer chooses."""
    model = pick_model(client)
    log(f'   writer model: {model}')
    questions = open_questions(bank, history, limit=70 if not only else 10000)
    if len(questions) < 5:
        raise WriteHold('fewer than 5 searched questions are left unanswered; refresh the question bank')
    if only:
        questions = [entry for entry in questions if entry['q'] == only.strip().lower()]
        if not questions:
            raise WriteHold(f'"{only}" is not an open searched question')
        log(f'   hand run: only "{only}"')
    feedback, previous = None, None
    for attempt in range(1, ROUNDS + 1):
        plan = ask_json(client, model, writer_prompt(sheet, questions, history, feedback, previous))
        screens.tidy(plan, sheet)
        problems = code_problems(plan, sheet, questions, history)
        if not problems:
            verdict = ask_json(client, model, audit_prompt(plan, sheet))
            breaches = verdict.get('breaches') if isinstance(verdict, dict) else None
            if not isinstance(breaches, list):
                raise WriteHold('fact check gave no readable answer')
            problems = [f"{item.get('where')}: {item.get('problem')}" for item in breaches if isinstance(item, dict)]
        log(f"   plan {attempt}/{ROUNDS}: \"{plan.get('search_phrase')}\" → " + ('passed' if not problems else f'{len(problems)} point(s) to fix'))
        for problem in problems:
            log(f'      - {str(problem)[:260]}')
        if not problems:
            plan['model'] = model
            plan['evidence'] = {key: sheet['facts'][key] for key in plan['fact_ids']}
            return plan
        feedback, previous = problems, plan
    raise WriteHold(f'No plan passed the fact check after {ROUNDS} attempts')
