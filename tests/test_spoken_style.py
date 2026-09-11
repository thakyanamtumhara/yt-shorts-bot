import copy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.spoken_style import (
    SpokenStyleError, document_scripts, load_reference, require_spoken_style,
    style_prompt, vocabulary_issues, eligible_recorded_source, corpus_source_excluded,
)


ROOT = Path(__file__).resolve().parents[1]


class SpokenVocabularyTest(unittest.TestCase):
    def test_rejected_words_fail_in_hindi_and_roman_before_audio(self):
        for word in ('हिदायत', 'हिदायतें', 'हिदायतों', 'मुलायमियत', 'मुलायमीयत',
                     'मालूमियत', 'मलूमियत', 'hidayat', 'HIDAAYAT', 'mulayamiyat', 'malumiyat'):
            with self.subTest(word=word), self.assertRaises(SpokenStyleError):
                require_spoken_style('कपड़े की ' + word + ' पर ध्यान दो।')

    def test_zero_width_joiner_does_not_hide_rejected_word(self):
        self.assertTrue(vocabulary_issues('मुलाय\u200dमियत'))

    def test_useful_technical_words_and_ordinary_register_remain_allowed(self):
        script = 'देखो, इलास्टेन और कम्पैक्शन अलग बातें हैं। फैब्रिक का फील समझो। सॉफ्ट लगता है तो उसे बोलो।'
        self.assertEqual(require_spoken_style(script), script)
        self.assertEqual(vocabulary_issues('सब्लिमेशन, ओवरलॉक, जीएसएम और प्री-श्रंक समझ लो।'), [])

    def test_words_are_not_substring_bans_and_checker_does_not_rewrite(self):
        script = 'Hidayatullah की दुकान पर देखो।'
        self.assertEqual(require_spoken_style(script), script)
        self.assertEqual(vocabulary_issues('soft software feel feeling'), [])

    def test_information_and_softness_are_not_silently_conflated(self):
        info = vocabulary_issues('malumiyat')[0]
        soft = vocabulary_issues('मुलायमियत')[0]
        self.assertEqual(info['rule'], 'dictation_variant')
        self.assertEqual(soft['rule'], 'formal_softness')
        self.assertIn('जानकारी', info['suggestion'])
        self.assertIn('soft', soft['suggestion'])


class RealSourceReferenceTest(unittest.TestCase):
    def test_reference_has_three_reviewed_raw_sources_and_eight_timed_fragments(self):
        data = load_reference()
        self.assertEqual(len(data['sources']), 3)
        self.assertEqual(len(data['examples']), 8)
        self.assertEqual({s['related_main_video_id'] for s in data['sources'].values()},
                         {'UQBlxoDQ2Dw', 'rEOtLcnHTG8', 'N-BkGLrnvyY'})
        self.assertTrue(all(s['timestamp_basis'] == 'original_raw_seconds_not_edited_youtube_timeline'
                            for s in data['sources'].values()))

    def test_synthetic_dialogue_daily_or_description_cannot_enter_reference(self):
        original = load_reference()
        for updates in ({'media_kind': 'ai-dialogue'}, {'media_kind': 'daily-bot'},
                        {'media_kind': 'description'}, {'synthetic': True},
                        {'synthetic': 'false'}, {'reviewed_source': False}):
            data = copy.deepcopy(original)
            data['sources']['warehouse'].update(updates)
            with TemporaryDirectory() as folder:
                path = Path(folder) / 'reference.json'; path.write_text(json.dumps(data))
                with self.subTest(updates=updates), self.assertRaises(SpokenStyleError):
                    load_reference(path)

    def test_unattributed_or_untimed_fragment_is_rejected(self):
        for updates in ({'source_id': 'synthetic-preview'}, {'end_seconds': 1}, {'start_seconds': '24.42'}):
            data = copy.deepcopy(load_reference()); data['examples'][0].update(updates)
            with TemporaryDirectory() as folder:
                path = Path(folder) / 'reference.json'; path.write_text(json.dumps(data))
                with self.subTest(updates=updates), self.assertRaises(SpokenStyleError):
                    load_reference(path)

    def test_prompt_selects_roman_or_devanagari_without_claiming_training(self):
        roman = style_prompt('roman'); hindi = style_prompt()
        self.assertIn('Toh aapko zyada samajh mein aayega.', roman)
        self.assertIn('तो आपको ज़्यादा समझ में आएगा।', hindi)
        self.assertIn('not model training', roman)
        self.assertIn('not pronunciation approval', roman)
        self.assertIn('Do not force ornate Hindi', hindi)
        self.assertIn('A new word is not forbidden', hindi)


class PreparationAndGenerationIntegrationTest(unittest.TestCase):
    def test_episode_json_extraction_ignores_captions_and_source_descriptions(self):
        document = {'episodes': [{'id': 'new', 'script': 'देखो, कपड़ा ऐसा है।',
                                 'caption': 'हिदायत', 'captions': ['मुलायमियत', 'हिदायत'],
                                 'source': {'description': 'मुलायमियत'}}]}
        self.assertEqual(document_scripts(document), [('new', 'देखो, कपड़ा ऐसा है।')])

    def test_read_only_cli_reports_error_without_changing_script(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'drafts.json'
            original = json.dumps({'episodes': [{'id': 'future', 'script': 'मुलायमियत देखो।'}]}, ensure_ascii=False)
            path.write_text(original)
            result = subprocess.run([sys.executable, str(ROOT / 'tools/spoken_style.py'), '--check', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(json.loads(result.stdout)['scripts'][0]['id'], 'future')

    def test_private_manifest_refuses_rejected_vocabulary_without_provider_call(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        import private_dialogue_episodes as pilot
        script = ' '.join(['कपड़ा'] * 45) + ' की मुलायमियत चेक करो।'
        episode = {'id': 'fit', 'source_key': 'p/fit.enc', 'source_sha256': 'a' * 64,
                   'source_has_original_audio': True, 'source_encrypted': True, 'script': script}
        with patch.object(pilot, 'request') as request:
            with self.assertRaisesRegex(ValueError, 'familiar spoken wording'):
                pilot.validate_manifest({'format': pilot.FORMAT, 'episodes': [episode]})
            request.assert_not_called()


class SourceIngestionTest(unittest.TestCase):
    VIDEO = '123456789ab'
    CHANNEL = 'UC_MAIN_TEST'

    def response(self, synthetic=False, **changes):
        item = {'id': self.VIDEO, 'snippet': {'channelId': self.CHANNEL},
                'status': {'privacyStatus': 'public', 'containsSyntheticMedia': synthetic}}
        item.update(changes)
        from unittest.mock import Mock
        return Mock(status_code=200, json=lambda: {'items': [item]})

    def test_native_synthetic_flag_blocks_main_caption_collection(self):
        self.assertFalse(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key',
                                                  lambda *a, **k: self.response(True)))

    def test_other_channel_is_never_owner_speech(self):
        self.assertFalse(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key',
                         lambda *a, **k: self.response(snippet={'channelId': 'UC_BOT_TEST'})))

    def test_real_main_source_is_eligible_without_changing_its_text(self):
        self.assertTrue(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key',
                                                 lambda *a, **k: self.response(False)))

    def test_known_synthetic_id_blocks_even_if_native_flag_is_later_removed(self):
        from unittest.mock import Mock
        with patch('tools.spoken_style.excluded_source_ids', return_value={self.VIDEO}):
            request = Mock()
            self.assertFalse(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key', request))
            self.assertTrue(corpus_source_excluded('# https://www.youtube.com/watch?v=' + self.VIDEO + '\n# source: captions\n\nआवाज़'))
            request.assert_not_called()

    def test_missing_native_flag_is_unknown_unless_original_recording_was_reviewed(self):
        response = self.response(status={'privacyStatus': 'public'})
        request = lambda *a, **k: response
        self.assertFalse(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key', request))
        with patch('tools.spoken_style.load_reference', return_value={
                'sources': {'reviewed': {'related_main_video_id': self.VIDEO}}}):
            self.assertTrue(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key', request))
            with patch('tools.spoken_style.excluded_source_ids', return_value={self.VIDEO}):
                self.assertFalse(eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key', request))

    def test_auth_network_schema_errors_do_not_fall_back_to_captions(self):
        from unittest.mock import Mock
        for request in (Mock(side_effect=RuntimeError('Unavailable')),
                        Mock(return_value=Mock(status_code=403)),
                        Mock(return_value=Mock(status_code=200, json=lambda: {})),
                        Mock(return_value=self.response('true'))):
            with self.subTest(request=request), self.assertRaises(SpokenStyleError):
                eligible_recorded_source(self.VIDEO, self.CHANNEL, 'fake-key', request)
        with self.assertRaises(SpokenStyleError):
            eligible_recorded_source(self.VIDEO, self.CHANNEL, None, Mock())


if __name__ == '__main__':
    unittest.main()
