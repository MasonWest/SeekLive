import contextlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
import server
from test_access import setup_fixture


class FakeVision:
    def __init__(self):self.paused=False;self.active=0
    def snapshot(self):return dict(available=True,paused=self.paused)
    def action(self,action):
        if action not in ('pause','resume','scan','retry'):raise ValueError('Invalid action')
        if action in ('pause','resume'):self.paused=action=='pause'
        return self.snapshot()
    @contextlib.contextmanager
    def demand(self):
        self.active+=1
        try:yield
        finally:self.active-=1


class VisionMainServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='vision-server-');setup_fixture(self.temp.name)
        self.previous_data=server.DATA_DIR;server.DATA_DIR=self.temp.name
        from test_vision_albums import seed
        seed(Path(self.temp.name)/'vision.db',server.META['libs']['t']['items'])
        from test_memory_stories import seed_stories
        seed_stories(Path(self.temp.name)/'memories.db',server.META['libs']['t'])
        self.previous=server.VISION;self.vision=FakeVision();server.VISION=self.vision
        self.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);self.port=self.httpd.server_address[1]
        self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.httpd.shutdown();self.httpd.server_close();self.thread.join();server.VISION=self.previous;server.DATA_DIR=self.previous_data
        for store in (server.INDEX,server.CACHE_INDEX,server.SHARES):store.close()
        self.temp.cleanup()
    def request(self,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5);data=json.dumps(body) if body is not None else None
        try:
            connection.request('POST' if body is not None else 'GET',path,body=data,headers={'Content-Type':'application/json',**(headers or {})});response=connection.getresponse();raw=response.read()
            try:payload=json.loads(raw)
            except ValueError:payload=raw.decode('utf-8',errors='replace')
            return response.status,dict(response.getheaders()),payload
        finally:connection.close()
    def admin(self):
        code,headers,_=self.request('/api/admin/login',{'token':'test-pw-123'},{'X-SL-Admin':'1'})
        self.assertEqual(code,200);return {'Cookie':headers['Set-Cookie'].split(';')[0]}
    def test_admin_status_and_control_require_existing_auth_and_csrf(self):
        self.assertEqual(self.request('/api/vision/status')[0],401)
        admin=self.admin();self.assertEqual(self.request('/api/vision/status',headers=admin)[0],200)
        self.assertEqual(self.request('/api/vision/control',{'action':'pause'},admin)[0],403)
        code,_,body=self.request('/api/vision/control',{'action':'pause'},{**admin,'X-SL-Admin':'1'})
        self.assertEqual(code,200);self.assertTrue(body['paused'])
        self.assertEqual(self.request('/api/vision/control',[],{**admin,'X-SL-Admin':'1'})[0],400)
    def test_public_tunnel_cannot_access_private_background_controls(self):
        headers={**self.admin(),'Host':'photos.example.com','X-Forwarded-Proto':'https','CF-Connecting-IP':'203.0.113.7','X-SL-Admin':'1'}
        self.assertEqual(self.request('/api/vision/status',headers=headers)[0],403)
        self.assertEqual(self.request('/api/vision/control',{'action':'pause'},headers)[0],403)
        self.assertFalse(self.vision.paused)
    def test_media_demand_always_releases_on_failure(self):
        @server.vision_media
        def media(handler):self.assertEqual(self.vision.active,1);raise RuntimeError('fixture')
        with self.assertRaises(RuntimeError):media(None)
        self.assertEqual(self.vision.active,0)
    def test_confirmed_album_pagination_and_private_name_search(self):
        admin=self.admin()
        code,_,data=self.request('/api/vision/albums?lib=t',headers=admin)
        self.assertEqual(code,200);self.assertEqual(len(data['albums']),3)
        self.assertEqual(next(a['count'] for a in data['albums'] if a['id']=='P'),2)
        code,_,data=self.request('/api/vision/album?lib=t&ids=P,Q&lim=1',headers=admin)
        self.assertEqual(code,200);self.assertEqual(data['total'],1);self.assertEqual(len(data['items']),1)
        self.assertEqual(self.request('/api/vision/album?lib=t&ids=unknown',headers=admin)[0],400)
        self.assertEqual(self.request('/api/vision/album?lib=t&memory=month',headers=admin)[0],400)
        self.assertEqual(self.request('/api/vision/albums?lib=u',headers=admin)[2]['albums'],[])
        self.assertEqual(self.request('/api/search?lib=t&q=Alice',headers=admin)[2]['total'],2)
    def test_album_names_are_not_available_to_public_or_unauthorised_visitors(self):
        self.assertEqual(self.request('/api/vision/albums?lib=t')[0],401)
        headers={**self.admin(),'Host':'photos.example.com','X-Forwarded-Proto':'https','CF-Connecting-IP':'203.0.113.7'}
        self.assertEqual(self.request('/api/vision/albums?lib=t',headers=headers)[0],403)
        self.assertEqual(self.request('/api/vision/album?lib=t&ids=P',headers=headers)[0],403)
    def test_share_search_does_not_reveal_private_identity_names(self):
        admin=self.admin();rel=next(i['p'] for i in server.META['libs']['t']['items'] if i['k']=='img')
        code,_,data=self.request('/api/share/create',dict(lib='t',rels=[rel]),{**admin,'X-SL-Admin':'1'})
        self.assertEqual(code,200)
        code,headers,_=self.request('/s/'+data['token']);self.assertEqual(code,302)
        shared={'Cookie':headers['Set-Cookie'].split(';')[0]}
        self.assertEqual(self.request('/api/vision/albums?lib=t',headers=shared)[0],403)
        self.assertEqual(self.request('/api/vision/album?lib=t&ids=P',headers=shared)[0],403)
        self.assertEqual(self.request('/api/search?lib=t&q=Alice',headers=shared)[2]['total'],0)
        self.assertEqual(self.request('/api/vision/stories?lib=t',headers=shared)[0],403)
    def test_memory_samples_are_private_and_return_normal_media_items(self):
        self.assertEqual(self.request('/api/vision/stories?lib=t')[0],401)
        admin=self.admin();code,_,data=self.request('/api/vision/stories?lib=t',headers=admin)
        self.assertEqual(code,200);self.assertEqual(len(data['stories']),3)
        self.assertNotIn('photos',data['stories'][0]);self.assertNotIn('gps',json.dumps(data))
        code,_,detail=self.request('/api/vision/story?lib=t&id=fixture-story-ink',headers=admin)
        self.assertEqual(code,200);self.assertGreaterEqual(len(detail['photos']),3)
        self.assertNotIn('version',detail['photos'][0]);self.assertIn('p',detail['photos'][0]['item'])
        self.assertEqual(self.request('/api/vision/story?lib=u&id=fixture-story-ink',headers=admin)[0],404)
        self.assertEqual(self.request('/api/vision/story?lib=t&id=../other',headers=admin)[0],400)
        public={**admin,'Host':'photos.example.com','X-Forwarded-Proto':'https','CF-Connecting-IP':'203.0.113.7'}
        self.assertEqual(self.request('/api/vision/stories?lib=t',headers=public)[0],403)
    def test_memory_full_pages_and_opening_edits_require_auth_and_revision(self):
        admin=self.admin();payload=dict(lib='t',id='fixture-story-ink',revision=0,changes=dict(title='A changed title',theme='sage'))
        self.assertEqual(self.request('/api/vision/story/edit',payload,admin)[0],403)
        self.assertEqual(self.request('/api/vision/story/edit',payload,{**admin,'X-SL-Admin':'1'})[0],200)
        self.assertEqual(self.request('/api/vision/story/edit',payload,{**admin,'X-SL-Admin':'1'})[0],409)
        code,_,data=self.request('/api/vision/story/items?lib=t&id=fixture-story-ink&mode=full&off=2&lim=2',headers=admin)
        self.assertEqual(code,200);self.assertEqual(data['title'],'A changed title');self.assertEqual(len(data['items']),2)
        self.assertNotIn('version',data['items'][0]);self.assertEqual(data['revision'],1)
        self.assertEqual(self.request('/api/vision/story?lib=t&id=fixture-story-ink&layout=invalid',headers=admin)[0],400)
        self.assertEqual(self.request('/api/vision/story/items?lib=u&id=fixture-story-ink',headers=admin)[0],404)
    def test_event_regrouping_is_private_and_undoable(self):
        admin={**self.admin(),'X-SL-Admin':'1'}
        data=dict(lib='t',action='merge',ids=['fixture-story-ink','fixture-story-amber'],revisions={'fixture-story-ink':0,'fixture-story-amber':0})
        code,_,result=self.request('/api/vision/story/organize',data,admin);self.assertEqual(code,200);self.assertEqual(len(result['created']),1)
        self.assertEqual(self.request('/api/vision/stories?lib=t',headers=admin)[2]['stories'].__len__(),2)
        self.assertEqual(self.request('/api/vision/story/organize',dict(lib='t',action='undo'),admin)[0],200)
        self.assertEqual(len(self.request('/api/vision/stories?lib=t',headers=admin)[2]['stories']),3)


if __name__=='__main__':unittest.main()
