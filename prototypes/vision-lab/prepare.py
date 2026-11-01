"""Download only public model artifacts and verify the prototype's pinned hashes.

This is the only network-enabled script. It never reads libraries.json or photos.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def verify(folder):
    manifest=json.loads(Path(__file__).with_name('models.json').read_text(encoding='utf-8'))
    for item in manifest:
        path=folder/item['file']
        if not path.is_file() or digest(path)!=item['sha256']:
            raise RuntimeError('Missing or mismatched model: '+item['file'])
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--models',type=Path,default=Path('tmp/vision-lab/models'))
    args=parser.parse_args();args.models.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(Path(__file__).with_name('models.json').read_text(encoding='utf-8'))
    for item in manifest:
        out=args.models/item['file']
        if out.exists() and digest(out)==item['sha256']:
            print('VERIFIED',item['file'],flush=True);continue
        request=urllib.request.Request(item['url'],headers={'User-Agent':'SeekLive-local-prototype'})
        tmp=out.with_suffix('.part')
        with urllib.request.urlopen(request,timeout=90) as source,tmp.open('wb') as dest:
            while chunk:=source.read(1024*1024):dest.write(chunk)
        if digest(tmp)!=item['sha256']:raise RuntimeError('Model checksum mismatch: '+item['file'])
        tmp.replace(out);print('DOWNLOADED AND VERIFIED',item['file'],flush=True)
    verify(args.models)


if __name__=='__main__':main()
