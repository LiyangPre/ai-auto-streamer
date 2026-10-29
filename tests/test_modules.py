import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.config import Config
from src.modules.core import ModuleSpec, ModuleRegistry
from src.stage_activity import StageActivity
import src.server as server


class ModuleLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_lifecycle_and_failed_start_cleanup(self):
        events = []
        class Service:
            def __init__(self, name, fail=False): self.name, self.fail = name, fail
            async def start(self):
                events.append('start '+self.name)
                if self.fail: raise RuntimeError('test startup failure')
            async def close(self): events.append('close '+self.name)
        specs = (ModuleSpec('chat','Chat','','',lambda state:Service('chat')),
                 ModuleSpec('story','Story','','',lambda state:Service('story',True)))
        with self.assertRaises(RuntimeError): await ModuleRegistry(specs,None).start()
        self.assertEqual(events,['start chat','start story','close story','close chat'])
        with self.assertRaises(ValueError): ModuleRegistry((specs[0],specs[0]),None)
        with self.assertRaises(ValueError): ModuleSpec('bad','Bad','','',lambda state:None,output='https://example.com/code.js')

    async def test_stage_ownership_cannot_be_released_by_another_module(self):
        stage=StageActivity();stage.acquire('story')
        with self.assertRaises(ValueError): stage.acquire('singing')
        self.assertFalse(stage.release('singing'));self.assertFalse(stage.idle.is_set())
        self.assertTrue(stage.release('story'));self.assertTrue(stage.idle.is_set())


class ModuleAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'studio-settings.json'
        cfg=Config();cfg.llm.provider='mock';cfg.tts.engine='none';cfg.idle.enabled=False
        self.patches=[patch('src.server.Config.load',return_value=cfg),patch('src.server.settings_path',return_value=self.path),
                      patch('src.danmaku.sys.stdin.isatty',return_value=False)]
        for item in self.patches:item.start()
        self.client=TestClient(server.app, base_url='http://localhost', headers={'X-Studio-API':'1','Origin':'http://localhost'});self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def save_look(self,module_id,pose,expression):
        context=self.client.get('/api/modules').json()
        return self.client.post('/api/modules/'+module_id+'/presentation',json={
            'profile_id':context['active_profile'],'revision':context['revision'],
            'presentation':{'pose':pose,'expression':expression,'motion_strength':.3}})

    def test_module_looks_are_per_character_survive_normal_save_and_do_not_change_stage(self):
        before=self.client.get('/api/studio').json()['settings']
        result=self.save_look('singing','nod','happy');self.assertEqual(result.status_code,200,result.text)
        original=server.app.state.characters.profiles['astra'].modules['singing'].model_dump()
        self.assertEqual(self.save_look('singing','present','thinking').status_code,422)
        self.assertEqual(self.client.post('/api/studio',json=before).status_code,200)
        self.assertEqual(server.app.state.characters.profiles['astra'].modules['singing'].model_dump(),original)
        context=self.client.get('/api/characters').json()
        other=self.client.post('/api/characters',json={'name':'Other','source_id':'astra','revision':context['revision']}).json()['id']
        context=self.client.get('/api/characters').json()
        self.assertEqual(self.client.post('/api/characters/activate',json={'profile_id':other,'revision':context['revision']}).status_code,200)
        self.assertEqual(self.save_look('singing','shy','neutral').status_code,200)
        self.assertEqual(server.app.state.characters.profiles['astra'].modules['singing'].model_dump(),original)
        self.assertEqual(self.client.get('/api/studio').json()['settings']['scene'],before['scene'])
        saved=json.loads(server.app.state.character_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['profiles'][other]['modules']['singing']['pose'],'shy')
        self.assertEqual(self.client.get('/api/modules',headers={'Origin':'https://other.example'}).status_code,403)

    def test_song_library_and_backup_are_scoped_and_restore_without_credentials(self):
        studio=server.app.state.modules.service('singing');studio.root.mkdir(parents=True)
        for token,owner in [('a'*32,'astra'),('b'*32,'someone-else')]:
            item={'id':token,'profile_id':owner,'title':'Original','created':0,'duration':2,'bytes':5,'voice':'冰糖'}
            (studio.root/(token+'.json')).write_text(json.dumps(item),encoding='utf-8')
            (studio.root/(token+'.mp3')).write_bytes(b'audio')
        self.assertEqual(len(self.client.get('/api/singing').json()['songs']),1)
        response=self.client.post('/api/singing/backup',json={});self.assertEqual(response.status_code,200,response.text)
        backup=self.client.get(response.json()['url']);self.assertEqual(backup.status_code,200)
        with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['profile_id'],'astra');self.assertEqual(len(manifest['songs']),1)
            self.assertFalse(any('b'*32 in name or '.env' in name for name in archive.namelist()))
            for name,digest in manifest['sha256'].items():self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(),digest)
        self.assertFalse(server.pipeline.performance_active)
