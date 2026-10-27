"""Offline Bilibili web-protocol replay and local HTTP -> reply regressions.

No Bilibili connection, broadcast, purchase, cloud model or speech call is made.
These fixtures validate our integration with the pinned community client, not
authorization or delivery by the official Open Live service.
"""
import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from blivedm.models import pb

from src.config import Config
from src.danmaku import BilibiliDanmakuCollector
from src.server import app
import src.server as server


def chat_packet():
    info = [[] for _ in range(17)]
    info[0] = [0] * 16
    info[0][15] = {}
    info[1] = '你好呀'
    info[2] = [101, '离线弹幕测试', 0, 0, 0, 0, 0, '']
    info[4] = [1, 0, 0, 0]
    info[7] = 0
    info[16] = [0]
    return {'cmd': 'DANMU_MSG:4:0:2:2:2:0', 'info': info}


def gift_packet(coin_type='gold'):
    return {'cmd':'SEND_GIFT', 'data':{
        'giftName':'测试礼物','num':2,'uname':'离线礼物测试','uid':102,
        'face':'','guard_level':0,'timestamp':1,'giftId':1,'giftType':0,
        'gift_info':{'img_basic':''},'action':'赠送','price':1000,'rnd':'fixture',
        'coin_type':coin_type,'total_coin':2000,'tid':'offline-gift',
    }}


class BilibiliPacketTests(unittest.TestCase):
    def setUp(self):
        self.queue=asyncio.Queue()
        self.handler=BilibiliDanmakuCollector(Config().platform,self.queue)._make_handler()
        self.client=SimpleNamespace(room_id=1)

    def test_real_sdk_dispatches_chat_and_gold_silver_gifts_synchronously(self):
        for packet in [chat_packet(),gift_packet(),gift_packet('silver')]:
            self.handler.handle(self.client,packet)
        chat,gold,silver=[self.queue.get_nowait() for _ in range(3)]
        self.assertEqual((chat.content,chat.user_id),('你好呀',101))
        self.assertEqual((gold.gift_num,gold.price,gold.priority),(2,2.0,1))
        self.assertEqual((silver.price,silver.priority),(0,0))

    def test_new_protobuf_gift_packet_delivers_every_item(self):
        packet=pb.SendGiftBroadcast(uid=103,uname='V2礼物测试',gift_list=[
            pb.SendGiftV2GiftItem(gift_name='花',num=3,coin_type='gold',total_coin=3000),
            pb.SendGiftV2GiftItem(gift_name='星',num=1,coin_type='silver',total_coin=100),
        ])
        self.handler.handle(self.client,{'cmd':'SEND_GIFT_V2','data':{
            'pb':base64.b64encode(packet.dumps()).decode('ascii')}})
        first,second=self.queue.get_nowait(),self.queue.get_nowait()
        self.assertEqual((first.user_id,first.gift_name,first.gift_num,first.price),(103,'花',3,3))
        self.assertEqual((second.gift_name,second.price),('星',0))

    def test_enter_v1_v2_does_not_mislabel_follow_share_or_like(self):
        for kind in [1,2,3,6]:
            self.handler.handle(self.client,{'cmd':'INTERACT_WORD','data':{
                'uid':104,'uname':'进入测试','msg_type':kind}})
            body=pb.InteractWordV2(uid=105,uname='V2进入测试',msg_type=kind)
            self.handler.handle(self.client,{'cmd':'INTERACT_WORD_V2','data':{
                'pb':base64.b64encode(body.dumps()).decode('ascii')}})
        self.assertEqual(self.queue.qsize(),2)
        self.assertEqual(self.queue.get_nowait().msg_type,'enter')
        self.assertEqual(self.queue.get_nowait().user_name,'V2进入测试')

    def test_membership_quantity_is_included_in_total_value(self):
        self.handler.handle(self.client,{'cmd':'GUARD_BUY','data':{
            'uid':106,'username':'舰长测试','guard_level':3,'num':2,
            'price':198000,'gift_id':1,'gift_name':'舰长','start_time':1,'end_time':1}})
        event=self.queue.get_nowait()
        self.assertEqual((event.gift_num,event.price,event.priority),(2,396,3))


class BilibiliPipelineTests(unittest.TestCase):
    def setUp(self):
        config=Config()
        config.llm.provider='mock';config.tts.engine='none'
        config.reply_interval.enabled=False;config.danmaku_filter.time_window=.02
        config.idle.enabled=False;config.memory.gift_combo_window=.01
        # Welcome replies are opt-in in production; enable them in this test.
        config.router.routes['enter'].enabled=True
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for patcher in [patch('src.server.Config.load',return_value=config),
                        patch('src.server.settings_path',return_value=Path(self.directory.name)/'settings.json'),
                        patch('src.danmaku.sys.stdin.isatty',return_value=False)]:
            patcher.start();self.addCleanup(patcher.stop)
        self.client=TestClient(app);self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)

    def receive_reply(self,ws,name):
        saw_input=False
        # A status is emitted each second, bounding failure waits to ~10 seconds.
        for _ in range(20):
            event=ws.receive_json()
            if event['type']=='danmaku' and event.get('user_name')==name:saw_input=True
            if event['type']=='response':
                self.assertTrue(saw_input)
                self.assertTrue(event['text'])
                self.assertIn('name',event['action'])
                self.assertIsNone(event['audio_url'])
                return
            self.assertNotIn(event['type'],{'response_error','reply_dropped'})
        self.fail('No reply after local test event')

    def test_sdk_chat_packet_reaches_websocket_and_reply(self):
        with self.client.websocket_connect('/ws') as ws:
            ws.receive_json()
            def replay():
                collector=BilibiliDanmakuCollector(server.config.platform,server.pipeline.danmaku_queue)
                collector._make_handler().handle(SimpleNamespace(room_id=1),chat_packet())
            self.client.portal.call(replay)
            self.receive_reply(ws,'离线弹幕测试')

    def test_local_http_gift_superchat_guard_and_enter_reach_reply(self):
        cases=[
            {'msg_type':'gift','gift_name':'测试花束','gift_num':2,'price':2,'priority':1},
            {'msg_type':'gift','gift_name':'醒目留言','gift_num':1,'price':30,'priority':2},
            {'msg_type':'gift','gift_name':'舰长','gift_num':1,'price':198,'priority':3},
            {'msg_type':'enter'},
        ]
        for index,fields in enumerate(cases):
            with self.subTest(fields=fields),self.client.websocket_connect('/ws') as ws:
                ws.receive_json();name=f'本地事件测试{index}'
                response=self.client.post('/api/danmaku',json={
                    'content':'本地模拟互动事件','user_name':name,'user_id':200+index,**fields})
                self.assertEqual(response.status_code,200,response.text)
                self.receive_reply(ws,name)


if __name__=='__main__':
    unittest.main()
