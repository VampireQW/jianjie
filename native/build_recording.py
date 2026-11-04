"""Build the small, system DLL-only capture/encoding bridge."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent.parent
def build():
    tools=ROOT/'build/native-toolchain'
    zig=tools/'zig-x86_64-windows-0.15.2/zig.exe'
    output=ROOT/'native/jj_recording.dll'
    environment=os.environ.copy()
    environment.update(ZIG_GLOBAL_CACHE_DIR=str(tools/'cache'),ZIG_LOCAL_CACHE_DIR=str(ROOT/'build/native-zig-cache'),TEMP=str(ROOT/'build/native-temp'),TMP=str(ROOT/'build/native-temp'))
    Path(environment['TEMP']).mkdir(parents=True,exist_ok=True)
    subprocess.run([str(zig),'c++','-target','x86_64-windows-gnu','-std=c++17','-O2','-g0','-DNDEBUG','-shared','-s',str(ROOT/'native/recording.cpp'),'-o',str(output),'-ld3d11','-ldxgi','-ld3dcompiler_47','-lmfplat','-lmfreadwrite','-lmfuuid','-lole32'],check=True,env=environment,cwd=ROOT)
    metadata=dict(bytes=output.stat().st_size,sha256=hashlib.sha256(output.read_bytes()).hexdigest(),sourceSha256=hashlib.sha256((ROOT/'native/recording.cpp').read_bytes()).hexdigest(),compiler='Zig 0.15.2 / Clang')
    (ROOT/'build/native-recording-build.json').write_text(json.dumps(metadata,indent=2))
    print('Native recording DLL:',metadata['bytes'],'bytes',flush=True)
    return str(output)
if __name__=='__main__':build()
