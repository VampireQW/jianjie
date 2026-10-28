"""Build the GPL UxPlay receiver; media DLLs are provisioned separately."""
from pathlib import Path
import hashlib,json,os,runpy,shutil,subprocess,zipfile
ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'build/airplay-research'
UPSTREAM=WORK/'UxPlay'
SDK=WORK/'sdk/ucrt64'
ZIG=ROOT/'build/native-toolchain/zig-x86_64-windows-0.15.2/zig.exe'
COMMIT='ec203121355220dd90278a369eeabb0b8ba4f14a'

def patched_source():
    text=(UPSTREAM/'uxplay.cpp').read_text()
    text=text.replace('#include <unordered_map>','#include <unordered_map>\n#include <winsock2.h>\n#include <windows.h>\n#include <shellapi.h>')
    patch=r"""
static HANDLE jj_stop_event = NULL;
static HANDLE jj_parent_process = NULL;
static gboolean jj_stop_watch(gpointer) {
    if ((jj_stop_event && WaitForSingleObject(jj_stop_event, 0) == WAIT_OBJECT_0) || (jj_parent_process && WaitForSingleObject(jj_parent_process, 0) == WAIT_OBJECT_0)) {
        relaunch_video = false;
        g_main_loop_quit(gmainloop);
        return G_SOURCE_CONTINUE;
    }
    return G_SOURCE_CONTINUE;
}
"""
    text=text.replace('static gboolean handle_signal(gpointer data) {',patch+'\nstatic gboolean handle_signal(gpointer data) {')
    text=text.replace('    gmainloop = loop;','    gmainloop = loop;\n    guint jj_watch = g_timeout_add(100, jj_stop_watch, NULL);')
    text=text.replace('    gmainloop = NULL;','    g_source_remove(jj_watch);\n    gmainloop = NULL;')
    entry='int main (int argc, char *argv[]) {'
    pos=text.rfind(entry)
    assert pos>=0
    setup=r"""
#ifdef _WIN32
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    int jj_argc = 0;
    LPWSTR *jj_wide = CommandLineToArgvW(GetCommandLineW(), &jj_argc);
    std::vector<std::string> jj_strings;
    std::vector<char *> jj_args;
    for (int i = 0; jj_wide && i < jj_argc; i++) {
        int count = WideCharToMultiByte(CP_UTF8, 0, jj_wide[i], -1, NULL, 0, NULL, NULL);
        std::string value(count, '\0');
        WideCharToMultiByte(CP_UTF8, 0, jj_wide[i], -1, &value[0], count, NULL, NULL);
        value.pop_back();
        jj_strings.push_back(value);
    }
    if (jj_wide) {
        LocalFree(jj_wide);
        for (auto &value : jj_strings) jj_args.push_back(&value[0]);
        jj_args.push_back(NULL);
        argc = jj_argc;
        argv = jj_args.data();
    }
    wchar_t jj_event_name[128];
    if (GetEnvironmentVariableW(L"JIANJIE_AIRPLAY_STOP_EVENT", jj_event_name, 128))
        jj_stop_event = OpenEventW(SYNCHRONIZE, FALSE, jj_event_name);
    char jj_parent_pid[32];
    if (GetEnvironmentVariableA("JIANJIE_AIRPLAY_PARENT_PID", jj_parent_pid, 32)) {
        jj_parent_process = OpenProcess(SYNCHRONIZE, FALSE, strtoul(jj_parent_pid, NULL, 10));
        if (!jj_parent_process) return 1;
    }
#endif
"""
    text=text[:pos]+text[pos:].replace(entry,entry+'\n'+setup,1)
    text=text.replace('    reconnect:\n','    puts("JJ_AIRPLAY_READY");\n    reconnect:\n')
    text=text.replace('    open_connections++;','    puts("JJ_AIRPLAY_CONNECTED");\n    open_connections++;')
    text=text.replace('    if (open_connections == 0) {','    if (open_connections == 0) {\n        jj_video_seen = false;\n        puts("JJ_AIRPLAY_READY");')
    text=text.replace('static GMainLoop *gmainloop = NULL;', 'static bool jj_video_seen = false;\nstatic GMainLoop *gmainloop = NULL;')
    text=text.replace('extern "C" void video_process (void *cls, raop_ntp_t *ntp, video_decode_struct *data) {','extern "C" void video_process (void *cls, raop_ntp_t *ntp, video_decode_struct *data) {\n    if (!jj_video_seen) { puts("JJ_AIRPLAY_STREAMING"); jj_video_seen = true; }')
    video = runpy.run_path(str(Path(__file__).with_name('windows_video.py')))
    return video['patch_main'](text)

def build():
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=UPSTREAM,text=True).strip()!=COMMIT:
        raise RuntimeError('Unexpected UxPlay source revision')
    output=ROOT/'native/airplay'
    generated=WORK/'generated'
    generated.mkdir(exist_ok=True)
    source=generated/'uxplay.cpp'
    source.write_text(patched_source(),encoding='utf-8')
    video = runpy.run_path(str(Path(__file__).with_name('windows_video.py')))
    discovery = runpy.run_path(str(Path(__file__).with_name('windows_discovery.py')))
    env=os.environ.copy()
    env['ZIG_GLOBAL_CACHE_DIR']=str(ROOT/'build/native-toolchain/cache')
    env['ZIG_LOCAL_CACHE_DIR']=str(WORK/'zig-cache')
    flags=['-target','x86_64-windows-gnu','-O2','-g0','-DNDEBUG','-DWIN32','-D_WIN32_WINNT=0x0A00','-DWINSOCK2','-DSTANDALONE','-DNOHOLD','-DPLIST_210','-DPLIST_230','-DOPENSSL_API_COMPAT=0x10101000L']
    for p in [UPSTREAM,UPSTREAM/'renderers',UPSTREAM/'lib',UPSTREAM/'lib/playfair',UPSTREAM/'lib/llhttp',UPSTREAM/'lib/mdnsd',SDK/'include',SDK/'include/glib-2.0',SDK/'lib/glib-2.0/include',SDK/'include/gstreamer-1.0']:
        flags+=['-I',str(p)]
    objects=[]
    sources=[]
    for directory in ['lib','lib/playfair','lib/llhttp','lib/mdnsd','renderers']:
        sources+=list((UPSTREAM/directory).glob('*.c'))
    ntp_patched = None
    mdns_patched = None
    renderer_patched = None
    for i,file in enumerate(sources):
        if file.name == 'raop_ntp.c':
            ntp_patched = file.read_text().replace('struct cmsghdr align;', 'WSACMSGHDR align;', 1)
            ntp_patched = ntp_patched.replace('#include "compat.h"', '#include "compat.h"\n#ifdef _WIN32\ntypedef WSACMSGHDR CMSGHDR;\ntypedef WSACMSGHDR *PCMSGHDR;\n#endif')
            replacement = generated/'raop_ntp.c'
            replacement.write_text(ntp_patched)
            file = replacement
        elif file.name == 'mdnsd.c':
            mdns_patched = discovery['patch_mdns'](file.read_text()).replace(
                '    unsigned char ttl = 255;\n    unsigned char loop = 1;',
                '    DWORD ttl = 255;\n    DWORD loop = 1;', 1)
            old = '    setsockopt(fd, IPPROTO_IP, IP_MULTICAST_TTL, (const char *) &ttl, sizeof(ttl));'
            new = '''    if (setsockopt(fd, IPPROTO_IP, IP_MULTICAST_TTL, (const char *) &ttl, sizeof(ttl)) == -1 ||
        setsockopt(fd, IPPROTO_IP, IP_TTL, (const char *) &ttl, sizeof(ttl)) == -1) {
        int error = SOCKET_GET_ERROR();
        CLOSESOCKET(fd);
        return -error;
    }'''
            assert old in mdns_patched
            mdns_patched = mdns_patched.replace(old, new, 1)
            marker = '    mdnsd->running = 1;\n    THREAD_CREATE'
            diagnostic = '''    if (mdnsd->sock_fd4 != -1) {
        DWORD ttl = 0;
        int length = sizeof(ttl);
        char address[INET_ADDRSTRLEN] = {0};
        struct in_addr local;
        local.s_addr = mdnsd->ipv4_addr;
        inet_ntop(AF_INET, &local, address, sizeof(address));
        getsockopt(mdnsd->sock_fd4, IPPROTO_IP, IP_MULTICAST_TTL, (char *) &ttl, &length);
        printf("JJ_AIRPLAY_DISCOVERY %s %lu\\n", address, (unsigned long) ttl);
    }
    mdnsd->running = 1;
    THREAD_CREATE'''
            assert marker in mdns_patched
            mdns_patched = mdns_patched.replace(marker, diagnostic, 1)
            replacement = generated/'mdnsd.c'
            replacement.write_text(mdns_patched)
            file = replacement
        elif file.name == 'video_renderer.c':
            renderer_patched = video['patch_renderer'](file.read_text())
            replacement = generated/'video_renderer.c'
            replacement.write_text(renderer_patched)
            file = replacement
        target=generated/(str(i)+'.o')
        print('Compile '+file.name,flush=True)
        subprocess.run([str(ZIG),'cc',*flags,'-c',str(file),'-o',str(target)],check=True,env=env)
        objects.append(str(target))
    libraries=['crypto','plist-2.0','gstapp-1.0','gstvideo-1.0','gstsdp-1.0','gstbase-1.0','gstreamer-1.0','gobject-2.0','glib-2.0','winpthread']
    shutil.copy2(ROOT/'_app.ico', generated/'_app.ico')
    resource = generated/'receiver.rc'
    resource.write_text('101 ICON "_app.ico"\n', encoding='ascii')
    resource_binary = generated/'receiver.res'
    subprocess.run([str(ZIG),'rc','/fo',str(resource_binary),str(resource)],cwd=generated,check=True,env=env)
    command=[str(ZIG),'c++',*flags,'-std=c++11','-s',str(source),*objects,str(resource_binary),'-L',str(SDK/'lib'),*[str(SDK/'lib'/('lib'+name+'.dll.a')) for name in libraries],'-lws2_32','-liphlpapi','-lshell32','-luser32','-ldxguid','-o',str(output/'receiver.exe')]
    subprocess.run(command,check=True,env=env)
    with zipfile.ZipFile(output/'source.zip','w',zipfile.ZIP_DEFLATED) as z:
        for file in UPSTREAM.rglob('*'):
            if file.is_file() and '.git' not in file.relative_to(UPSTREAM).parts:
                relative=file.relative_to(UPSTREAM)
                overrides = {'uxplay.cpp': source.read_bytes(),
                             'lib/raop_ntp.c': ntp_patched.encode(),
                             'lib/mdnsd/mdnsd.c': mdns_patched.encode(),
                             'renderers/video_renderer.c': renderer_patched.encode()}
                z.writestr(str(relative), overrides.get(str(relative).replace('\\','/'), file.read_bytes()))
        z.write(__file__,'native/airplay/build.py')
        z.write(ROOT/'_app.ico','_app.ico')
        z.write(resource,'native/airplay/receiver.rc')
        for name in ('fetch_deps.py','dependencies.json','BUILD.md','windows_video.py','windows_discovery.py','video_stats.c','display_latency.c'):
            z.write(Path(__file__).with_name(name),'native/airplay/'+name)
    (output/'LICENSE').write_bytes((UPSTREAM/'LICENSE').read_bytes())
    exe=output/'receiver.exe'
    meta={'upstream':'https://github.com/FDH2/UxPlay','commit':COMMIT,'license':'GPL-3.0','engineSha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'engineBytes':exe.stat().st_size,'runtimeUrl':'https://github.com/leapbtw/uxplay-windows/releases/download/2.0.0.1736/uxplay-windows.zip','runtimeSha256':'9d3a51c15fc9db857351195e7eb7bbb21700d9ae25d936a54bcf8536b62cca18','runtimeBytes':113529789,'version':'jj-airplay-1','internalMdns':True}
    (output/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps(meta),flush=True)
    return str(exe)

if __name__=='__main__': build()
