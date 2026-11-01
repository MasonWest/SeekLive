import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'prototypes'/'vision-lab'))
from review_server import make_handler
from evaluate import dataset_id
from vision_db import VisionDB


class LocalReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='tmp/vision-lab');self.base=Path(self.temp.name);self.dbpath=self.base/'vision.db'
        result=dict(samples=[],faces=[],pets=[],face_groups=[],models=[]);self.dataset=dataset_id(result)
        (self.base/'results.private.json').write_text(json.dumps(result),encoding='utf-8')
        (self.base/'review.html').write_text("<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; connect-src 'none'\"><script>const example=1;</script>",encoding='utf-8')
        self.state=dict(dataset_id=self.dataset,animal_entities={},face_checks={},group_names={})
        store=VisionDB(self.dbpath)
        try:store.save(self.dataset,self.state,0,catalog=dict(groups=[],observations=[dict(id='B',sample='s',kind='bird')],confirmed=[]))
        finally:store.close()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),lambda *a:None);self.port=self.server.server_address[1]
        self.server.RequestHandlerClass=make_handler(self.base,self.dbpath,self.port);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.port);self.client=build_opener(ProxyHandler({}))
        with self.client.open(self.origin+'/') as response:html=response.read().decode()
        self.boot=json.loads(html.split('id="database-bootstrap">')[1].split('</script>')[0])
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def put(self,state,revision=1,origin=None,token=None):
        req=Request(self.origin+'/api/state',data=json.dumps(dict(state=state,revision=revision)).encode(),method='PUT',headers={'Content-Type':'application/json','Origin':origin or self.origin,'X-Vision-Token':token or self.boot['token']})
        try:
            with self.client.open(req) as response:return response.status,json.load(response)
        except HTTPError as response:return response.code,json.load(response)
    def test_bird_archive_persists_and_stale_writer_conflicts(self):
        state=dict(self.state,animal_entities={'PET':dict(id='PET',kind='bird',name='Bird',status='followed',members=['B'])})
        self.assertEqual(self.put(state)[0],200)
        self.assertEqual(self.put(self.state)[0],409)
        db=VisionDB(self.dbpath)
        try:self.assertEqual(db.load(self.dataset)['state']['animal_entities']['PET']['name'],'Bird')
        finally:db.close()
    def test_csrf_and_file_origins_are_rejected(self):
        self.assertEqual(self.put(self.state,origin='null')[0],403)
        self.assertEqual(self.put(self.state,token='wrong')[0],403)
        self.assertEqual(self.put(self.state,origin='http://other.example')[0],403)
    def test_host_rebinding_is_rejected(self):
        request=Request(self.origin+'/health',headers={'Host':'other.example'})
        with self.assertRaises(HTTPError) as error:self.client.open(request)
        self.assertEqual(error.exception.code,403)
    def test_formal_results_require_local_token_and_images_cookie_is_private(self):
        self.assertTrue(self.boot['formal'])
        for path in ('/api/vision/summary','/api/vision/photos?entity=unknown','/api/vision/image?id=B'):
            with self.assertRaises(HTTPError) as error:self.client.open(self.origin+path)
            self.assertEqual(error.exception.code,403)
        request=Request(self.origin+'/api/vision/summary',headers={'X-Vision-Token':self.boot['token']})
        with self.client.open(request) as response:self.assertEqual(json.load(response)['detections']['bird'],1)
        with self.client.open(self.origin+'/') as response:
            cookie=response.headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        request=Request(self.origin+'/api/vision/image?id=missing',headers={'Cookie':cookie.split(';')[0]})
        with self.assertRaises(HTTPError) as error:self.client.open(request)
        self.assertEqual(error.exception.code,400) # Cookie accepted; unknown observation rejected.
    def test_served_page_allows_local_images_and_offline_page_stays_data_only(self):
        with self.client.open(self.origin+'/') as response:html=response.read().decode()
        self.assertIn("img-src 'self' data:;",html)
        self.assertIn("connect-src 'self'",html)
        self.assertNotIn('img-src *',html)
        self.assertIn('img-src data:;',(self.base/'review.html').read_text(encoding='utf-8'))
    def test_invalid_membership_is_atomic(self):
        state=dict(self.state,animal_entities={'PET':dict(id='PET',kind='cat',name='Bad',status='followed',members=['B'])})
        self.assertEqual(self.put(state)[0],400)
        db=VisionDB(self.dbpath)
        try:self.assertEqual(db.load(self.dataset)['revision'],1)
        finally:db.close()


if __name__=='__main__':unittest.main()
