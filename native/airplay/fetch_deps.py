"""Prepare pinned build dependencies, without installing system services."""
from pathlib import Path
from urllib.request import Request,urlopen
import hashlib,json,subprocess,tarfile,time
ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'build/airplay-research'
MANIFEST=json.loads(Path(__file__).with_name('dependencies.json').read_text())

def main():
    WORK.mkdir(parents=True,exist_ok=True)
    source=WORK/'UxPlay'
    if not source.exists():
        subprocess.run(['git','-c','http.sslBackend=openssl','clone','https://github.com/FDH2/UxPlay.git',str(source)],check=True)
    subprocess.run(['git','checkout',MANIFEST['commit']],cwd=source,check=True)
    for item in MANIFEST['packages']:
        archive=WORK/item['name']
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest()!=item['sha256']:
            for attempt in range(3):
                try:
                    pending=archive.with_suffix('.pending')
                    with urlopen(Request(item['url'],headers={'User-Agent':'JianJie-AirPlay-Build'}),timeout=20) as response,pending.open('wb') as target:
                        while True:
                            chunk=response.read(1024*1024)
                            if not chunk:break
                            target.write(chunk)
                    if hashlib.sha256(pending.read_bytes()).hexdigest()!=item['sha256']:
                        raise ValueError('Dependency checksum mismatch: '+item['name'])
                    pending.replace(archive)
                    break
                except Exception:
                    if attempt==2: raise
                    time.sleep(1)
        with tarfile.open(archive,'r:*') as tar:
            tar.extractall(WORK/'sdk',filter='data')
        print(item['name'],flush=True)

if __name__=='__main__':main()
