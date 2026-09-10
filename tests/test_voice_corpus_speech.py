import ast
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_voice_namespace(source=None):
    source = source or (ROOT / 'daily_short.py').read_text()
    start = source.index('# ╔══════════════════════════════════════════════════════════════════════╗\n# ║                   TTS PRE-PROCESSING')
    end = source.index('def sarvam_tts_to_mp3')
    namespace = {'os': os, 're': re, 'json': json, '__file__': str(ROOT / 'daily_short.py'),
                 '_LEARNED_PRON_CACHE': None}
    exec(source[start:end], namespace)
    wanted = {'_PRON_DENYLIST', '_DEVA_CONSONANT_ROMAN', '_DEVA_NUKTA_OPTIONS',
              '_DEVA_MATRA_OPTIONS', '_DEVA_VOWEL_OPTIONS', '_load_expected_english',
              '_devanagari_to_roman_variants', '_voice_corpus_speech_body',
              'build_voice_models', '_get_learned_pronunciations',
              'extract_voice_corpus_style_hints'}
    nodes = [node for node in ast.parse(source).body
             if (isinstance(node, ast.FunctionDef) and node.name in wanted)
             or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in wanted
                                                      for t in node.targets))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'voice-corpus-test-slice', 'exec'), namespace)
    return namespace


class SpeechCorpusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / 'voice_corpus'
        self.corpus.mkdir()
        self.vocab = self.root / 'voice_vocab.json'
        self.learned = self.root / 'learned_pronunciations.json'
        self.ns = load_voice_namespace()
        self.ns.update({'VOICE_CORPUS_DIR': str(self.corpus), 'VOICE_VOCAB_FILE': str(self.vocab),
                        'LEARNED_PRON_FILE': str(self.learned)})

    def put(self, name, source=None, body='वास्तविक वास्तविक वास्तविक', extra=''):
        header = '# Source fixture\n'
        if source is not None:
            header += f'# source: {source}\n'
        (self.corpus / name).write_text(header + extra + '\n' + body)

    def build(self):
        with redirect_stdout(io.StringIO()):
            self.ns['build_voice_models']()

    def populate_descriptions(self):
        for i in range(10):
            self.put(f'description-{i}.txt', 'description', 'बनावटी बनावटी बनावटी')
        self.put('legacy.txt', body='विज्ञापन विज्ञापन विज्ञापन')

    def test_one_or_two_speech_files_never_learn_description_words(self):
        self.populate_descriptions()
        for count in (1, 2):
            with self.subTest(speech_files=count):
                self.put(f'speech-{count}.txt', 'captions')
                self.build()
                vocab = json.loads(self.vocab.read_text())
                learned = json.loads(self.learned.read_text())
                self.assertEqual(vocab['top_words'], ['वास्तविक'])
                self.assertTrue(learned)
                self.assertEqual(set(learned.values()), {'वास्तविक'})

    def test_no_eligible_speech_retains_existing_artifacts_and_cache_exactly(self):
        self.populate_descriptions()
        self.put('ai.txt', 'ai-dialogue', 'कृत्रिम कृत्रिम कृत्रिम')
        self.vocab.write_bytes(b'{"prior":"vocabulary"}\n')
        self.learned.write_bytes(b'{"prior":"learned"}\n')
        cache = {'existing': 'स्थायी'}
        self.ns['_LEARNED_PRON_CACHE'] = cache
        self.build()
        self.assertEqual(self.vocab.read_bytes(), b'{"prior":"vocabulary"}\n')
        self.assertEqual(self.learned.read_bytes(), b'{"prior":"learned"}\n')
        self.assertIs(self.ns['_LEARNED_PRON_CACHE'], cache)
        self.assertEqual(self.ns['extract_voice_corpus_style_hints'](), '')

    def test_no_speech_does_not_create_empty_replacement_artifacts(self):
        self.populate_descriptions()
        self.build()
        self.assertFalse(self.vocab.exists())
        self.assertFalse(self.learned.exists())

    def test_source_labels_are_exact_and_body_cannot_supply_missing_provenance(self):
        reject = [
            '# source: description\n\nआवाज़ आवाज़',
            '# source: captions-ai-dialogue\n\nआवाज़ आवाज़',
            '# source: whisper-ai\n\nआवाज़ आवाज़',
            '# source: ai-dialogue\n\nआवाज़ आवाज़',
            '# title only\n\nआवाज़ आवाज़\n# source: captions',
            '# source: captions\n# source: description\n\nआवाज़ आवाज़',
            '# source: captions\n\nEnglish translated captions only.',
            '# source: whisper\n\n   ',
        ]
        for raw in reject:
            with self.subTest(raw=raw):
                self.assertIsNone(self.ns['_voice_corpus_speech_body'](raw))

    def test_explicit_synthetic_media_never_counts_as_recorded_speech(self):
        for source in ('captions', 'captions-oauth', 'whisper'):
            for header in ('# media_kind: ai-dialogue\n', '# media_kind: ai-generated\n',
                           '# synthetic: true\n', '# synthetic: yes\n'):
                with self.subTest(source=source, header=header):
                    raw = f'# source: {source}\n' + header + '\nआवाज़ आवाज़'
                    self.assertIsNone(self.ns['_voice_corpus_speech_body'](raw))
        self.put('flagged-ai.txt', 'captions', 'कृत्रिम कृत्रिम कृत्रिम', '# media_kind: ai-dialogue\n')
        self.build()
        self.assertFalse(self.vocab.exists())

    def test_reviewed_raw_whisper_format_and_legacy_captions_remain_eligible(self):
        for source in ('captions', 'captions-oauth', 'whisper'):
            with self.subTest(source=source):
                self.assertEqual(self.ns['_voice_corpus_speech_body'](
                    f'# title\n# source: {source}\n\nआवाज़ आवाज़'), 'आवाज़ आवाज़')
        reviewed = ('# New genuine raw recording\n# source: whisper\n'
                    '# media_kind: real-recording\n# synthetic: false\n# speaker: Ketu\n'
                    '# source_sha256: ' + 'a' * 64 + '\n# reviewed: true\n\nआवाज़ आवाज़')
        self.assertEqual(self.ns['_voice_corpus_speech_body'](reviewed), 'आवाज़ आवाज़')

    def test_style_requires_four_speech_files_not_four_description_files(self):
        self.populate_descriptions()
        body = 'पहला पूरा वाक्य है। असली दूसरा वाक्य है। असली तीसरा वाक्य है। आखिरी पूरा वाक्य है।'
        for count in (1, 2, 3):
            self.put(f'speech-{count}.txt', 'captions', body)
            self.assertEqual(self.ns['extract_voice_corpus_style_hints'](), '')
        self.put('speech-4.txt', 'captions', body)
        hints = self.ns['extract_voice_corpus_style_hints']()
        self.assertIn('4 recent main-channel video transcripts', hints)
        self.assertIn('असली', hints)
        self.assertNotIn('बनावटी', hints)
        self.assertNotIn('विज्ञापन', hints)

    def test_new_dated_speech_has_priority_and_descriptions_never_fill_style_slots(self):
        self.populate_descriptions()
        for i in range(4):
            self.put(f'backfill-{i}.txt', 'captions',
                     'यह पहला वाक्य है। पुराने शब्द सही हैं। पुरानी बात याद रखो। यह आखिरी वाक्य है।')
        self.put('2026-09-10.txt', 'whisper',
                 'यह पहला वाक्य है। नई बात ध्यान रखो। नया कपड़ा समझ लो। यह आखिरी वाक्य है।')
        hints = self.ns['extract_voice_corpus_style_hints'](max_entries=12)
        self.assertIn('5 recent main-channel video transcripts', hints)
        self.assertLess(hints.index('नया कपड़ा'), hints.index('पुरानी बात'))
        self.assertNotIn('बनावटी', hints)

    def test_hand_curated_words_and_english_homographs_remain_protected(self):
        hand = dict(self.ns['_TTS_HINGLISH_DEVANAGARI'])
        self.put('speech.txt', 'captions',
                 'ग्रीन ग्रीन शिप शिप पेंट पेंट गलत गलत सीखा सीखा दिल्ली दिल्ली बंद बंद वास्तविक वास्तविक')
        self.build()
        learned = json.loads(self.learned.read_text())
        self.assertFalse({'green', 'ship', 'paint', 'galat', 'seekha', 'delhi', 'band'} & set(learned))
        self.assertEqual(self.ns['_TTS_HINGLISH_DEVANAGARI'], hand)
        normalized = self.ns['normalize_for_tts']('the best green ship paint. hum saath the. combed galat seekha delhi')
        self.assertEqual(normalized, 'the best green ship paint. हम साथ थे. कोम्ड ग़लत सीखा दिल्ली')

    def test_current_real_corpus_rebuild_matches_committed_learning_artifacts(self):
        self.ns['VOICE_CORPUS_DIR'] = str(ROOT / 'voice_corpus')
        self.build()
        self.assertEqual(self.vocab.read_bytes(), (ROOT / 'voice_vocab.json').read_bytes())
        self.assertEqual(self.learned.read_bytes(), (ROOT / 'learned_pronunciations.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
