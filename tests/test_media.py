import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import lameenc
from PIL import Image
from fastapi.testclient import TestClient

from src.config import Config
from src.media import media_directory
import src.server as server


def png(width=40, height=30):
    stream=io.BytesIO()
    Image.new('RGB',(width,height),'lavender').save(stream,format='PNG')
    return stream.getvalue()


def mp3():
    encoder=lameenc.Encoder()
    encoder.set_bit_rate(64);encoder.set_in_sample_rate(22050);encoder.set_channels(1)
    return bytes(encoder.encode(b'\0'*22050)+encoder.flush())


class MediaAPITests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'studio-settings.json'
        config=Config();config.llm.provider='mock';config.tts.engine='none';config.idle.enabled=False
        self.patches=[patch('src.server.Config.load',return_value=config),
                      patch('src.server.settings_path',return_value=self.path),
                      patch('src.danmaku.sys.stdin.isatty',return_value=False)]
        for p in self.patches:p.start()
        self.client=TestClient(server.app).__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in self.patches:p.stop()
        self.directory.cleanup()

    def upload(self,kind,name,content):
        return self.client.post('/api/media/'+kind,content=content,
                                headers={'X-Filename':quote(name),'Content-Type':'application/octet-stream'})

    def test_png_and_mp3_upload_preview_apply_restart_and_shared_character_switch(self):
        image=self.upload('background','我的背景.PNG',png())
        audio=self.upload('music','轻音乐.mp3',mp3())
        self.assertEqual(image.status_code,200,image.text);self.assertEqual(audio.status_code,200,audio.text)
        image=image.json()['asset'];audio=audio.json()['asset']
        self.assertEqual((image['width'],image['height']),(40,30));self.assertGreater(audio['duration'],0)
        self.assertEqual(self.client.get(image['url']).content,png())
        served=self.client.get(audio['url'],headers={'Range':'bytes=0-99'})
        self.assertEqual(served.status_code,206);self.assertEqual(len(served.content),100)
        self.assertEqual(served.headers['content-type'],'audio/mpeg')
        settings=self.client.get('/api/studio').json()['settings']
        self.assertIsNone(settings['scene']['music_asset'],'upload alone must not change live output')
        settings['scene'].update(background='custom',background_asset=image['id'],music_asset=audio['id'],music_volume=.2)
        response=self.client.post('/api/studio',json=settings)
        self.assertEqual(response.status_code,200,response.text)
        context=self.client.get('/api/characters').json()
        new=self.client.post('/api/characters',json={'name':'Mira','revision':context['revision']}).json()
        switched=self.client.post('/api/characters/activate',json={'profile_id':new['id'],'revision':new['profile_context']['revision']})
        self.assertEqual(switched.json()['settings']['scene'],settings['scene'])
        self.client.__exit__(None,None,None)
        self.client=TestClient(server.app).__enter__()
        self.assertEqual(self.client.get('/api/config').json()['scene'],settings['scene'])
        self.assertEqual(len(self.client.get('/api/media').json()['assets']),2)
        self.assertEqual(self.client.get(image['url']).headers['content-type'],'image/png')

    def test_bad_formats_empty_truncated_and_oversized_images_leave_no_files(self):
        for kind,name,data in [('music','bad.mp3',b'ID3bad'),('background','bad.png',b'not a picture'),
                               ('background','bad.png',png()[:-20]),('background','bad.jpg',png()),
                               ('background','empty.png',b''),('background','too-wide.png',png(8193,1))]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(kind,name,data).status_code,422)
        self.assertEqual(self.client.get('/api/media').json()['assets'],[])
        self.assertFalse(media_directory(self.path).exists())

    def test_byte_limits_check_both_declared_size_and_streamed_data(self):
        response=self.client.post('/api/media/music',content=b'a',headers={'X-Filename':'x.mp3','Content-Length':str(51*1024*1024)})
        self.assertEqual(response.status_code,413)
        with patch.dict('src.server.LIMITS',{'background':8}):
            response=self.upload('background','x.png',iter([b'123456',b'789']))
            self.assertEqual(response.status_code,413)
        self.assertEqual(self.client.get('/api/media').json()['assets'],[])

    def test_storage_uses_unique_ids_and_selection_rejects_foreign_paths_or_wrong_types(self):
        asset=self.upload('background','../../test.png',png()).json()['asset']
        other=self.upload('background','../../test.png',png()).json()['asset']
        self.assertEqual(asset['name'],'test.png');self.assertNotEqual(asset['id'],other['id'])
        self.assertEqual(len(list(media_directory(self.path).glob('*.png'))),2)
        self.assertEqual(self.client.get('/media/'+asset['id']+'.mp3').status_code,404)
        self.assertEqual(self.client.get('/media/secret.png').status_code,404)
        initial=self.client.get('/api/studio').json()['settings']
        for field,value in [('music_asset',asset['id']),('background_asset','f'*32),('background_asset','../../secret')]:
            draft={**initial,'scene':{**initial['scene'],field:value}}
            self.assertEqual(self.client.post('/api/studio',json=draft).status_code,422)
        draft={**initial,'scene':{**initial['scene'],'background':'custom'}}
        self.assertEqual(self.client.post('/api/studio',json=draft).status_code,422)

    def test_failed_disk_write_leaves_no_half_uploaded_asset(self):
        with patch('src.media.Path.replace',side_effect=OSError('disk full')):
            self.assertEqual(self.upload('background','test.png',png()).status_code,500)
        self.assertEqual(list(media_directory(self.path).iterdir()),[])


if __name__=='__main__':unittest.main()
