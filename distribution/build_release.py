"""Build only from explicit source files and cryptographically pinned public inputs."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,shutil,sys,zipfile
from . import package

DEFAULT_VERSION='0.5.0-rc.4'
SOURCE_BASE_COMMIT='fdd2acccb0b46969055a4867f0be7f5136998c76'

def extension_version(version):
    value=package.version_key(version)
    if any(part>65535 for part in value[:3]) or (value[3]==0 and value[4]>=65535):raise ValueError('Extension version exceeds Chrome limits')
    # Reserve the highest fourth component for stable, so rc -> stable increases.
    return '%d.%d.%d.%d'%(*value[:3],value[4] if value[3]==0 else 65535)

def runtime_pth_pair(archive):
    names=[item.filename for item in archive.infolist() if not item.is_dir()]
    pths=[name for name in names if __import__('re').fullmatch(r'python3[0-9]+\._pth',name)]
    if len(pths)!=1:raise ValueError('Expected exactly one Python runtime path file')
    companion=pths[0][:-5]+'.zip'
    if companion not in names:raise ValueError('Runtime path file has no matching stdlib ZIP')
    return pths[0],companion

def build(source,runtime,wheels,dictionary,output,version):
    source=Path(source).resolve();output=Path(output).resolve();package.version_key(version)
    if output.exists():raise ValueError('Build output already exists; use a fresh directory')
    lock=json.loads((source/'distribution/inputs.lock.json').read_text(encoding='utf-8'))
    def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest(runtime)!=lock['runtime']['sha256'] or digest(dictionary)!=lock['dictionary']['sha256']:raise ValueError('Public build input checksum mismatch')
    wheel_files={p.name:p for p in Path(wheels).glob('*.whl')}
    if set(wheel_files)!=set(item['filename'] for item in lock['wheels']):raise ValueError('Unpinned wheel in build input')
    for item in lock['wheels']:
        if digest(wheel_files[item['filename']])!=item['sha256']:raise ValueError('Wheel checksum mismatch')
    output.mkdir(parents=True);origins={}
    def put(name,data,origin):
        package.payload_path(name);target=output/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('Duplicate build output: '+name)
        target.write_bytes(data);origins[name]=origin
    with zipfile.ZipFile(runtime) as z:
        runtime_pth,runtime_zip=runtime_pth_pair(z)
        for item in z.infolist():
            if item.is_dir():continue
            name='runtime/'+item.filename
            data=z.read(item)
            if item.filename==runtime_pth:data=(runtime_zip+'\n.\nsite-packages\n..\n../app/pae\n').encode('ascii')
            put(name,data,'official Python embeddable archive; isolated path file adjusted' if item.filename==runtime_pth else lock['runtime']['url'])
    for item in lock['wheels']:
        with zipfile.ZipFile(wheel_files[item['filename']]) as z:
            for entry in z.infolist():
                if entry.is_dir():continue
                path=PurePosixPath(entry.filename)
                if any(part in ('tests','__pycache__') for part in path.parts) or path.suffix=='.pyc':continue
                if '.data' in path.parts[0]:
                    if len(path.parts)<3 or path.parts[1] not in ('purelib','platlib'):continue
                    path=PurePosixPath(*path.parts[2:])
                put('runtime/site-packages/'+str(path),z.read(entry),item['url'])
    files=[]
    files.extend(p for p in (source/'pae/pae_core').glob('*.py') if not p.name.startswith('test'))
    files.extend([source/'pae/config.json',source/'pae/capabilities.json',source/'pae/panel.html',source/'pae/persona/default.json',source/'pae/persona/template.md',source/'resources/ECDICT/lemma.en.txt',source/'resources/ECDICT/README.md',source/'resources/ECDICT/LICENSE',source/'LICENSE'])
    files.extend(p for p in (source/'pae/extension').iterdir() if p.is_file() and p.suffix in ('.js','.html','.json','.css','.png'))
    for path in sorted(files):
        if path.is_symlink():raise ValueError('Source symlink forbidden')
        rel=path.relative_to(source).as_posix();data=path.read_bytes();origin='audited candidate source'
        if rel.startswith('pae/extension/'):
            if path.suffix in ('.js','.html'):data=data.decode('utf-8-sig').replace('127.0.0.1:4815','127.0.0.1:4817').encode('utf-8')
            if path.name=='manifest.json':
                doc=json.loads(data);doc['version']=extension_version(version);doc['version_name']=version
                doc['host_permissions']=[x.replace('127.0.0.1:4815','127.0.0.1:4817') for x in doc['host_permissions']];data=json.dumps(doc,ensure_ascii=False,indent=2).encode('utf-8')
            origin+='; packaged extension default port/version set'
        put('app/'+rel,data,origin)
    put('app/resources/ECDICT/dict.sqlite',Path(dictionary).read_bytes(),lock['dictionary']['url'])
    for name in ('__init__.py','package.py','entries.py','locks.py','admission.py','network.py','bootstrap.py','manage.html','manage.css','manage.js','uninstall_helper.py','uninstall_recovery.py'):
        put('distribution/'+name,(source/'distribution'/name).read_bytes(),'audited release lifecycle source')
    install='@echo off\r\nsetlocal\r\n"%~dp0runtime\\python.exe" -X utf8 -B -m distribution.bootstrap install %*\r\nif errorlevel 1 (\r\n  echo OpenGloss installation failed. No existing engine was stopped.\r\n  pause\r\n  exit /b 1\r\n)\r\necho Load the extension folder from the installation location shown above.\r\npause\r\nexit /b 0\r\n'
    put('Install.cmd',install.encode('ascii'),'generated one-click entry')
    recover='@echo off\r\nsetlocal\r\n"%~dp0runtime\\python.exe" -X utf8 -B -m distribution.bootstrap recover %*\r\npause\r\n'
    put('Recover.cmd',recover.encode('ascii'),'generated explicit recovery entry; never starts the engine')
    put('README.md',(source/'distribution/README-INSTALL.md').read_bytes(),'audited installation instructions')
    put('THIRD-PARTY.json',json.dumps(lock,ensure_ascii=False,indent=2).encode('utf-8'),'public build inputs and official hashes')
    manifest={'format':1,'product':'OpenGloss','version':version,'platform':'windows-x64','source_base_commit':SOURCE_BASE_COMMIT,'status':'release candidate; verify Windows/browser acceptance before publishing','files':{name:{'sha256':digest(output/name),'size':(output/name).stat().st_size,'origin':origin} for name,origin in sorted(origins.items())}}
    package.atomic_json(output/package.MANIFEST,manifest);package.verify_directory(output)
    archive=output.with_suffix('.zip')
    if archive.exists():raise ValueError('Archive already exists')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                info=zipfile.ZipInfo(path.relative_to(output).as_posix(),date_time=(2026,10,19,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,path.read_bytes())
    archive.with_suffix('.sha256').write_text(digest(archive)+'  '+package.ASSET+'\n',encoding='ascii')
    return {'version':version,'files':len(manifest['files'])+1,'bytes':archive.stat().st_size,'sha256':digest(archive),'archive':str(archive)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',required=True,type=Path);parser.add_argument('--wheels',required=True,type=Path);parser.add_argument('--dictionary',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--version',default=DEFAULT_VERSION)
    args=parser.parse_args();print(json.dumps(build(Path(__file__).resolve().parent.parent,args.runtime,args.wheels,args.dictionary,args.output,args.version),ensure_ascii=False))
if __name__=='__main__':main()
