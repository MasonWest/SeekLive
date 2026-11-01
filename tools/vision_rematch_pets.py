"""Explicit cached-only v1 pet-review migration. Stop the old supervisor first."""
import json
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vision_db import VisionDB
from vision_pipeline import refresh_pet_review_policy

if __name__=='__main__':
    store=VisionDB('data/vision.db')
    try:
        control=store.db.execute('SELECT heartbeat FROM vision_control WHERE id=1').fetchone()
        if time.time()-control['heartbeat']<8:raise RuntimeError('Stop old supervisor before upgrading the matching policy')
        backup=Path('data/vision/backups')/('before-pet-review-policy-'+time.strftime('%Y%m%d-%H%M%S')+'.db');backup.parent.mkdir(parents=True,exist_ok=True)
        target=sqlite3.connect(backup);store.db.backup(target);target.close()
        print(json.dumps(refresh_pet_review_policy(store)),flush=True)
    finally:store.close()
