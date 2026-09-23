import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from tools.prepublication_audio import require_native_audio_review
from tools.audit_short_output import AuditError


class PrepublicationAudioTest(unittest.TestCase):
    def check(self, passed=True, test=False, published=False, error=None):
        with TemporaryDirectory() as directory:
            manifest=Path(directory)/'review_manifest.json'
            manifest.write_text(json.dumps({'test_mode':test,'source_posts':{'bot_youtube':'abcdefghijk' if published else None}}))
            value={'passed':passed,'review_type':'machine_native_audio_not_human_listening','model':'review','audio_sha256':'hash'}
            with patch('tools.prepublication_audio.probe_and_extract',return_value=(Path(directory)/'audio.wav',{'audio_seconds':36.5,'duration_seconds':39.2})) as probe, patch('tools.prepublication_audio.assess_audio',return_value=value,side_effect=error) as review:
                if error or not passed or published:
                    with self.assertRaises((AuditError,RuntimeError)):
                        require_native_audio_review('video.mp4',manifest)
                else:
                    result=require_native_audio_review('video.mp4',manifest)
                    self.assertEqual(result['passed'],None if test else True)
                if test or published:
                    probe.assert_not_called();review.assert_not_called()
                else:
                    self.assertEqual(review.call_args.args[2],36.5)
                if not error and not test and not published:
                    self.assertEqual(json.loads(manifest.read_text())['run_flags']['native_audio_review']['passed'],passed)
    def test_pass_records_real_audio_assessment(self):self.check()
    def test_failed_assessment_stops_publication(self):self.check(passed=False)
    def test_unavailable_provider_cannot_pass(self):self.check(error=RuntimeError('unavailable'))
    def test_test_only_run_does_not_spend_on_approval(self):self.check(test=True)
    def test_already_published_receipts_rejected(self):self.check(published=True)
    def test_gate_precedes_every_public_upload(self):
        source=(Path(__file__).resolve().parents[1]/'daily_short.py').read_text();main=source[source.index('def main():'):]
        gate=main.index("flag('native_audio_review', require_native_audio_review")
        for call in ('upload_to_youtube(', 'cross_post_to_instagram(', 'publish_fb_reel(', 'post_telegram_channel('):
            self.assertLess(gate,main.index(call))
        self.assertLess(main.index('review_path = save_review_archive('),gate)

if __name__=='__main__':unittest.main()
