"""Main-process supervisor; inference runs separately at low OS priority."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from vision_db import VisionDB
from vision_pipeline import synchronize,status,control
from vision_inference import model_versions


class VisionService:
    def __init__(self,database,index,models,assets,python=None,busy=lambda:False,request_index=None):
        self.database=Path(database);self.index=Path(index);self.models=Path(models);self.assets=Path(assets)
        self.python=python or sys.executable;self.busy=busy;self.request_index=request_index;self.runtime=uuid.uuid4().hex
        self.stop_event=threading.Event();self.thread=None;self.process=None;self.active=0;self.last_activity=0;self.lock=threading.Lock();self.log=None

    @contextlib.contextmanager
    def demand(self):
        with self.lock:self.active+=1;self.last_activity=time.time()
        try:yield
        finally:
            with self.lock:self.active-=1;self.last_activity=time.time()

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self._loop,name='vision-supervisor',daemon=True);self.thread.start()

    def _loop(self):
        store=VisionDB(self.database);owned=False;last_sync=0;restart_after=0
        try:
            try:versions=model_versions(self.models)
            except (OSError,ValueError):
                with store.db:store.db.execute("UPDATE vision_control SET last_error='ModelsUnavailable' WHERE id=1")
                return
            while not self.stop_event.is_set():
                now=time.time();state=store.db.execute('SELECT * FROM vision_control WHERE id=1').fetchone()
                if not owned:
                    with store.db:
                        store.db.execute('BEGIN IMMEDIATE')
                        current=store.db.execute('SELECT * FROM vision_control WHERE id=1').fetchone()
                        if current['runtime'] and now-current['heartbeat']<8:
                            pass
                        else:
                            store.db.execute('UPDATE vision_control SET runtime=?,heartbeat=?,last_error=NULL,model=?,worker_heartbeat=0 WHERE id=1',(self.runtime,now,versions['full']))
                            store.db.execute("UPDATE jobs SET status='pending',owner=NULL,lease_until=NULL WHERE kind='vision' AND status='running'")
                            owned=True
                    if not owned:self.stop_event.wait(.5);continue
                if state['runtime'] not in (self.runtime,None) and now-state['heartbeat']<8:return
                with self.lock:foreground=self.active>0 or now-self.last_activity<1.5
                foreground=foreground or self.busy()
                with store.db:store.db.execute('UPDATE vision_control SET heartbeat=?,foreground_until=? WHERE id=1 AND runtime=?',(now,now+1 if foreground else 0,self.runtime))
                if not foreground and self.request_index:
                    request=store.claim(self.runtime+'-index',kind='vision-index')
                    if request:
                        try:self.request_index();store.finish(request['id'],self.runtime+'-index')
                        except Exception:store.finish(request['id'],self.runtime+'-index',error='IndexSyncFailed')
                        foreground=bool(self.busy())
                if (now-last_sync>=60 or state['last_sync']==0) and not foreground:
                    try:synchronize(store,self.index,versions['full'],review_delay=60);last_sync=now
                    except Exception:
                        with store.db:store.db.execute("UPDATE vision_control SET last_error='IndexSyncFailed' WHERE id=1")
                        last_sync=now
                if self.process is None or self.process.poll() is not None:
                    if now>=restart_after:
                        if self.log is None:
                            self.assets.parent.mkdir(parents=True,exist_ok=True);self.log=(self.assets.parent/'worker.log').open('ab')
                        flags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)) if os.name=='nt' else 0
                        args=[str(self.python),str(Path(__file__).with_name('vision_worker.py')),'--database',str(self.database.resolve()),'--models',str(self.models.resolve()),'--assets',str(self.assets.resolve()),'--runtime',self.runtime]
                        try:self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,creationflags=flags,cwd=str(Path(__file__).parent))
                        except OSError:
                            with store.db:store.db.execute("UPDATE vision_control SET last_error='WorkerUnavailable' WHERE id=1")
                        restart_after=now+15
                self.stop_event.wait(.25)
        except Exception:
            with store.db:store.db.execute("UPDATE vision_control SET last_error='SupervisorFailed' WHERE id=1")
        finally:
            if owned:
                with store.db:store.db.execute('UPDATE vision_control SET heartbeat=0 WHERE id=1 AND runtime=?',(self.runtime,))
            store.close()

    def snapshot(self):
        store=VisionDB(self.database)
        try:return status(store)
        finally:store.close()

    def action(self,action):
        store=VisionDB(self.database)
        try:
            control(store,action)
            if action in ('resume','retry','scan') and (self.thread is None or not self.thread.is_alive()):self.start()
            return status(store)
        finally:store.close()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=5)
        if self.process and self.process.poll() is None:
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
        if self.log:self.log.close()
