"""Load production voice configuration and text preprocessing without provider imports."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _literal_workflow_value(workflow, name, required=True):
    values = re.findall(r'^\s+' + re.escape(name) + r':\s*[\"\']?([A-Za-z0-9_.-]+)[\"\']?\s*(?:#.*)?$', workflow, re.M)
    if len(set(values)) != 1:
        if not values and not required and not re.search(r'^\s+' + re.escape(name) + r':', workflow, re.M):
            return None
        raise ValueError('Daily workflow must have one explicit literal ' + name)
    return values[0]


def production_voice_config(root=ROOT):
    root = Path(root)
    source = (root/'daily_short.py').read_text()
    workflow = (root/'.github/workflows/daily_short.yml').read_text()
    assignments = {target.id: node.value for node in ast.parse(source).body
                   if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)}
    voice = _literal_workflow_value(workflow, 'ELEVENLABS_VOICE_ID', required=False)
    if voice is None:
        default = assignments['ELEVENLABS_VOICE_ID']
        if not (isinstance(default, ast.Call) and isinstance(default.func, ast.Attribute)
                and ast.unparse(default.func) == 'os.environ.get' and len(default.args) == 2
                and ast.literal_eval(default.args[0]) == 'ELEVENLABS_VOICE_ID'):
            raise ValueError('Daily voice ID source changed; inspect before generating QA audio')
        voice = ast.literal_eval(default.args[1])
    settings = ast.literal_eval(assignments['ELEVENLABS_VOICE_SETTINGS'])
    tempo = float(_literal_workflow_value(workflow, 'VOICE_TEMPO'))
    if not isinstance(voice, str) or not voice or not isinstance(settings, dict) or not 0.5 <= tempo <= 2:
        raise ValueError('Invalid production voice configuration')
    return {'voice_id': voice, 'model_id': _literal_workflow_value(workflow, 'ELEVENLABS_MODEL'),
            'voice_settings': settings, 'voice_tempo': tempo,
            'source': '.github/workflows/daily_short.yml + daily_short.py',
            'daily_short_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'learned_pronunciations_sha256': hashlib.sha256((root/'learned_pronunciations.json').read_bytes()).hexdigest()}


def load_normalize_for_tts(root=ROOT):
    root = Path(root)
    source = (root/'daily_short.py').read_text()
    start = source.index('# ╔══════════════════════════════════════════════════════════════════════╗\n# ║                   TTS PRE-PROCESSING')
    end = source.index('def sarvam_tts_to_mp3', start)
    namespace = {'os': os, 're': re, 'json': json, '__file__': str(root/'daily_short.py'),
                 'LEARNED_PRON_FILE': str(root/'learned_pronunciations.json'), '_LEARNED_PRON_CACHE': None}
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name == '_get_learned_pronunciations']
    if len(nodes) != 1:
        raise ValueError('Production learned-pronunciation loader missing or ambiguous')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root/'daily_short.py'), 'exec'), namespace)
    exec(compile(source[start:end], str(root/'daily_short.py'), 'exec'), namespace)
    return namespace['normalize_for_tts']
