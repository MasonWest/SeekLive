"""Loopback-only review bridge. SQLite writes require origin, token and revision."""
import json
import secrets
import sys
from urllib.parse import urlparse,parse_qs
from http.cookies import SimpleCookie
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from vision_db import VisionDB,Conflict
from evaluate import dataset_id
from vision_pipeline import status as vision_status,control as vision_control
from vision_gallery import summary as gallery_summary,photos as gallery_photos,decide as gallery_decide,image as gallery_image


def make_handler(base,database,port):
    result=json.loads((base/'results.private.json').read_text(encoding='utf-8'));dataset=dataset_id(result);token=secrets.token_urlsafe(32);origin=f'http://127.0.0.1:{port}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass # Never log names, photo paths or request bodies.
        def reply(self,status,data,content_type='application/json; charset=utf-8',cookie=False):
            body=json.dumps(data,ensure_ascii=False).encode() if content_type.startswith('application/json') else data
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"frame-ancestors 'none'")
            if cookie:self.send_header('Set-Cookie','sl_vision_images='+token+'; Path=/; HttpOnly; SameSite=Strict')
            self.end_headers();self.wfile.write(body)
        def host_valid(self):return self.headers.get('Host')==f'127.0.0.1:{port}'
        def do_GET(self):
            if not self.host_valid():return self.reply(403,{'error':'Invalid host'})
            parsed=urlparse(self.path);qs=parse_qs(parsed.query)
            if parsed.path in ('/api/vision/summary','/api/vision/photos','/api/vision/image'):
                authorised=secrets.compare_digest(self.headers.get('X-Vision-Token',''),token)
                if parsed.path=='/api/vision/image':
                    cookies=SimpleCookie(self.headers.get('Cookie',''));entry=cookies.get('sl_vision_images');authorised=authorised or bool(entry and secrets.compare_digest(entry.value,token))
                if not authorised:return self.reply(403,{'error':'Invalid local request'})
                store=VisionDB(database)
                try:
                    if parsed.path=='/api/vision/summary':return self.reply(200,gallery_summary(store,dataset))
                    if parsed.path=='/api/vision/photos':return self.reply(200,gallery_photos(store,dataset,qs.get('entity',[''])[0],qs.get('mode',['pending'])[0],qs.get('offset',[0])[0],qs.get('limit',[24])[0]))
                    return self.reply(200,gallery_image(store,dataset,qs.get('id',[''])[0],qs.get('view',['crop'])[0]=='photo',base),'image/jpeg')
                except Conflict as error:return self.reply(409,{'error':str(error)})
                except (ValueError,OSError,KeyError) as error:return self.reply(400,{'error':str(error)})
                finally:store.close()
            if self.path=='/health':return self.reply(200,{'ok':True})
            if self.path=='/api/vision/status':
                if not secrets.compare_digest(self.headers.get('X-Vision-Token',''),token):return self.reply(403,{'error':'Invalid local request'})
                store=VisionDB(database)
                try:return self.reply(200,vision_status(store))
                finally:store.close()
            if self.path not in ('/','/review.html'):return self.reply(404,{'error':'Not found'})
            store=VisionDB(database)
            try:loaded=store.load(dataset)
            finally:store.close()
            if loaded is None:return self.reply(503,{'error':'Import required'})
            boot=json.dumps(dict(**loaded,token=token,vision=True,formal=True),ensure_ascii=False).replace('<','\\u003c')
            page=(base/'review.html').read_text(encoding='utf-8').replace("connect-src 'none'","connect-src 'self'").replace('img-src data:','img-src \'self\' data:')
            page=page.replace('<script>','<script type="application/json" id="database-bootstrap">'+boot+'</script><script>',1)
            page=page.replace('人物与宠物 · 样本核对','人物与动物 · 正式档案').replace('独立本机原型。这里的分组和匹配均为候选，需要你确认。','直接读取正式数据库。已归档照片与新增待确认候选分开显示。')
            page=page.replace('>人物候选</button>','>人物档案</button>').replace('>关注人物的新照片</button>','>全库匹配结果</button>')
            self.reply(200,page.encode('utf-8'),'text/html; charset=utf-8',cookie=True)
        def do_PUT(self):
            if not self.host_valid() or self.headers.get('Origin')!=origin or not secrets.compare_digest(self.headers.get('X-Vision-Token',''),token):return self.reply(403,{'error':'Invalid local request'})
            if self.path not in ('/api/state','/api/vision/control','/api/vision/decision'):return self.reply(404,{'error':'Not found'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=5_000_000:raise ValueError('Invalid request size')
                payload=json.loads(self.rfile.read(size));store=VisionDB(database)
                try:
                    if self.path=='/api/vision/control':
                        vision_control(store,payload.get('action'));return self.reply(200,vision_status(store))
                    if self.path=='/api/vision/decision':return self.reply(200,gallery_decide(store,dataset,payload['entity'],payload.get('ids',[]),payload['action'],payload['revision']))
                    revision=store.save(dataset,payload['state'],payload['revision'])
                finally:store.close()
                self.reply(200,{'revision':revision})
            except Conflict as error:self.reply(409,{'error':str(error)})
            except (ValueError,KeyError,TypeError,AttributeError) as error:self.reply(400,{'error':str(error)})
    return Handler


if __name__=='__main__':
    base=Path('tmp/vision-lab/combined');database=Path('data/vision.db');port=8767
    server=ThreadingHTTPServer(('127.0.0.1',port),make_handler(base,database,port))
    print('Local SQLite review ready on 127.0.0.1:'+str(port),flush=True);server.serve_forever()
