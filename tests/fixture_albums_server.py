"""Loopback synthetic albums fixture; never opens production vision storage."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from test_access import setup_fixture
from test_vision_albums import seed


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='albums-fixture-',dir='tmp') as root:
        setup_fixture(root);server.DATA_DIR=root
        from test_access import build_lib
        from PIL import Image,ImageDraw
        lib=server.META['libs']['t'];specs=[(it['p'],it['t'],it['k'],it['lv'],it['mv'] if it['lv'] else None) for it in lib['items']]
        for i in range(150):
            rel='story/SCENE_'+str(i).zfill(3)+'.jpg';path=Path(lib['root'])/rel;path.parent.mkdir(exist_ok=True)
            size=(360,540) if i%2 else (540,360);image=Image.new('RGB',size,(110+i%50,150+i%30,150+i%60));draw=ImageDraw.Draw(image)
            w,h=size;draw.ellipse((w*.65,h*.12,w*.8,h*.12+w*.15),fill=(245,222,173));draw.polygon([(0,h*.8),(w*.3,h*.3),(w*.7,h*.9),(w,h*.55),(w,h),(0,h)],fill=(55,95,85));image.save(path,'JPEG')
            specs.append((rel,1727913600+i*30,'img',0,None))
        server.META['libs']['t']=build_lib('t','测试库',lib['root'],specs)
        seed(Path(root)/'vision.db',server.META['libs']['t']['items'])
        from test_memory_stories import seed_stories
        seed_stories(Path(root)/'memories.db',server.META['libs']['t'])
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        print('READY http://127.0.0.1:'+str(httpd.server_address[1]),flush=True)
        try:httpd.serve_forever()
        finally:
            httpd.server_close()
            for store in (server.INDEX,server.CACHE_INDEX,server.SHARES):store.close()
