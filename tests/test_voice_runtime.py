import ast
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest
from unittest.mock import Mock, patch

from tools.voice_runtime import load_normalize_for_tts, production_voice_config

ROOT=Path(__file__).resolve().parents[1]


class VoiceRuntimeTest(unittest.TestCase):
    def test_daily_workflow_model_settings_and_learned_aliases_are_loaded(self):
        config=production_voice_config()
        self.assertEqual(config['model_id'],'eleven_v3')
        self.assertEqual(config['voice_tempo'],1.0)
        normalize=load_normalize_for_tts()
        self.assertEqual(normalize('saimpal order GSM combed galat seekha'),'सैंपल order GSM कोम्ड ग़लत सीखा')
        self.assertEqual(normalize('ऑर्डर सैंपल एम मीडियम बायो वॉश'),'ऑर्डर सैंपल एम मीडियम बायो वॉश')

    def test_complete_normalizer_matches_independent_production_slice(self):
        spec=importlib.util.spec_from_file_location('corpus_fixture',ROOT/'tests/test_voice_corpus_speech.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        namespace=module.load_voice_namespace();namespace['LEARNED_PRON_FILE']=str(ROOT/'learned_pronunciations.json')
        actual=load_normalize_for_tts()
        for text in ('saimpal ardar jiesem 240 GSM ₹190','the best green ship paint. hum saath the.',
                     'combed galat seekha delhi','ऑर्डर करो।','Sample ka print dekh ke decide karo...'):
            with self.subTest(text=text):self.assertEqual(actual(text),namespace['normalize_for_tts'](text))

    def fixture(self, folder):
        root=Path(folder);(root/'.github/workflows').mkdir(parents=True)
        for rel in ['daily_short.py','learned_pronunciations.json','.github/workflows/daily_short.yml']:
            (root/rel).write_bytes((ROOT/rel).read_bytes())
        return root

    def test_config_follows_explicit_daily_workflow_not_stale_v2_default(self):
        with TemporaryDirectory() as folder:
            root=self.fixture(folder);path=root/'.github/workflows/daily_short.yml'
            path.write_text(path.read_text().replace('ELEVENLABS_MODEL: eleven_v3','ELEVENLABS_MODEL: test_production_model').replace('VOICE_TEMPO: "1.0"','VOICE_TEMPO: "0.94"'))
            self.assertEqual(production_voice_config(root)['model_id'],'test_production_model')
            self.assertEqual(production_voice_config(root)['voice_tempo'],0.94)
            path.write_text(path.read_text().replace('ELEVENLABS_MODEL: test_production_model','ELEVENLABS_MODEL: ${{ secrets.MODEL }}'))
            with self.assertRaises(ValueError):production_voice_config(root)

    def test_learned_loader_uses_requested_repo_and_production_validation(self):
        with TemporaryDirectory() as folder:
            root=self.fixture(folder)
            (root/'learned_pronunciations.json').write_text(json.dumps({'realalias':'असल','invalid':'hello','combed':'बदला'}))
            self.assertEqual(load_normalize_for_tts(root)('realalias invalid combed'),'असल invalid कोम्ड')

    def test_qa_loop_calls_production_config_without_network_in_loader(self):
        spec=importlib.util.spec_from_file_location('qa_runtime_test',ROOT/'.github/qa_loop.py')
        module=importlib.util.module_from_spec(spec)
        with patch('requests.post') as post:
            spec.loader.exec_module(module)
            text=module.load_normalize_for_tts()('saimpal')
            self.assertEqual(text,'सैंपल');post.assert_not_called()
            post.return_value=Mock(content=b'fake-audio')
            with TemporaryDirectory() as folder:module.elevenlabs_tts(text,Path(folder)/'test.mp3','not-real')
            self.assertEqual(post.call_args.kwargs['json']['model_id'],'eleven_v3')
            self.assertEqual(post.call_args.kwargs['json']['text'],'सैंपल')
            self.assertEqual(post.call_args.kwargs['json']['voice_settings'],production_voice_config()['voice_settings'])

    def test_audio_sample_embedded_python_compiles_and_initializes_same_full_normalizer(self):
        workflow=(ROOT/'.github/workflows/audio_sample.yml').read_text()
        script=textwrap.dedent(workflow.split("python3 - << 'PY'\n",1)[1].split('\n          PY',1)[0])
        tree=ast.parse(script);compile(tree,'audio_sample.yml','exec')
        stop=next(index for index,node in enumerate(tree.body) if isinstance(node,ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id=='text' for t in node.targets))
        namespace={}
        with patch.dict('os.environ',{'INPUT_MODEL':''}):
            exec(compile(ast.Module(body=tree.body[:stop],type_ignores=[]),'audio-sample-config','exec'),namespace)
        self.assertEqual(namespace['model'],production_voice_config()['model_id'])
        self.assertEqual(namespace['normalize_for_tts']('saimpal'),'सैंपल')
        self.assertNotIn('WITHOUT listening',workflow)
        self.assertIn('actual-audio review required',workflow)


if __name__=='__main__':unittest.main()
