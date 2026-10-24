"""Verified Windows release installation. Never reads an existing OpenGloss checkout."""
from pathlib import Path,PurePosixPath
import contextlib,hashlib,json,os,re,shutil,sqlite3,stat,tempfile,urllib.parse,uuid,zipfile
from .locks import data_owner
PRODUCT='OpenGloss'
ASSET='OpenGloss-windows-x64.zip'
API='https://api.github.com/repos/luomeii/OpenGloss/releases/latest'
MAX_BYTES=512*1024*1024
MANIFEST='RELEASE-MANIFEST.json'
PUBLIC_CA='runtime/site-packages/certifi/cacert.pem'
PUBLIC_CA_SHA256='d87887200c9a705dccc937b9b83b5e67e47024c4a4b7f9dd15700c72e0f55453'

def version_key(value):
    match=re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.(0|[1-9]\d*))?',str(value))
    if not match:raise ValueError('Invalid release version')
    major,minor,patch,rc=match.groups()
    return int(major),int(minor),int(patch),1 if rc is None else 0,int(rc or 0)

def safe_path(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:raise ValueError('Invalid package path')
    path=PurePosixPath(name)
    if path.is_absolute() or any(p in ('','.','..') for p in name.split('/')):raise ValueError('Unsafe package path')
    for part in path.parts:
        if part.endswith((' ','.')) or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*('COM%d'%n for n in range(1,10)),*('LPT%d'%n for n in range(1,10))}:raise ValueError('Invalid Windows path')
    return path

def payload_path(name):
    path=safe_path(name)
    if path.parts[0] not in ('runtime','app','distribution') and name not in ('Install.cmd','Recover.cmd','README-INSTALL.md','THIRD-PARTY.json'):raise ValueError('Unrecognized payload root')
    low=name.lower();basename=path.name.lower()
    if any(p.lower() in ('.git','__pycache__','data','logs','backups') for p in path.parts):raise ValueError('Runtime data in package')
    if basename in ('.env','keys.json','instance.json','active.json') or basename.startswith('.env.'):raise ValueError('Private configuration in package')
    if path.suffix.lower() in ('.db','.sqlite','.sqlite3','.log','.pyc','.pem','.pfx','.p12','.key') and low not in ('app/resources/ecdict/dict.sqlite',PUBLIC_CA):raise ValueError('Runtime or private file in package')
    return path

def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()

def check_manifest(document):
    if not isinstance(document,dict) or document.get('format')!=1 or document.get('product')!=PRODUCT or document.get('platform')!='windows-x64':raise ValueError('Unsupported release manifest')
    version_key(document.get('version'))
    files=document.get('files')
    if not isinstance(files,dict) or not 1<=len(files)<=10000:raise ValueError('Invalid package file list')
    seen=set();size=0
    for name,item in files.items():
        payload_path(name)
        if name.casefold() in seen:raise ValueError('Case-colliding package paths')
        seen.add(name.casefold())
        if not isinstance(item,dict) or not re.fullmatch('[a-f0-9]{64}',str(item.get('sha256',''))) or type(item.get('size')) is not int or not 0<=item['size']<=MAX_BYTES:raise ValueError('Invalid package hash or size')
        size+=item['size']
    if size>MAX_BYTES:raise ValueError('Package too large')
    required={'runtime/python.exe','runtime/pythonw.exe','distribution/bootstrap.py','app/pae/config.json','app/pae/persona/default.json'}
    if not required.issubset(files):raise ValueError('Incomplete runtime package')
    if version_key(document['version'])[:3]>=(0,5,0) and (PUBLIC_CA not in files or files[PUBLIC_CA]['sha256']!=PUBLIC_CA_SHA256):raise ValueError('Public CA bundle missing or altered')
    return document

def verify_directory(folder):
    folder=Path(folder).resolve()
    if not folder.is_dir() or folder.is_symlink():raise ValueError('Package directory missing')
    document=check_manifest(read_json(folder/MANIFEST))
    actual=set()
    for path in folder.rglob('*'):
        if path.is_symlink() or path.is_junction():raise ValueError('Package links forbidden')
        if path.is_file():actual.add(path.relative_to(folder).as_posix())
    if actual!=set(document['files'])|{MANIFEST}:raise ValueError('Unexpected or missing package files')
    for name,item in document['files'].items():
        path=folder.joinpath(*safe_path(name).parts)
        if path.stat().st_size!=item['size'] or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Package checksum mismatch: '+name)
    config=read_json(folder/'app/pae/config.json')
    if (config.get('llm') or {}).get('key'):raise ValueError('LLM credentials in release config')
    return document

def unpack(archive,destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Extraction destination already exists')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();names=[x.filename for x in infos]
        for entry in infos:
            safe_path(entry.filename)
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr>>16):raise ValueError('Only regular package files allowed')
        if len(set(n.casefold() for n in names))!=len(names) or MANIFEST not in names:raise ValueError('Duplicate paths or missing manifest')
        if z.getinfo(MANIFEST).file_size>4*1024*1024:raise ValueError('Manifest too large')
        document=check_manifest(json.loads(z.read(MANIFEST)))
        if set(names)!=set(document['files'])|{MANIFEST}:raise ValueError('Unexpected archive contents')
        for entry in infos:
            if entry.filename!=MANIFEST and entry.file_size!=document['files'][entry.filename]['size']:raise ValueError('Archive size mismatch')
        destination.mkdir(parents=True)
        try:
            for entry in infos:
                target=destination.joinpath(*safe_path(entry.filename).parts);target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(entry) as source,target.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
            verify_directory(destination)
        except BaseException:
            shutil.rmtree(destination);raise
    return destination

def installation(root):
    root=Path(root).resolve()
    if not (root/'installation.json').is_file():raise ValueError('Unrecognized installation directory')
    marker=read_json(root/'installation.json')
    if marker.get('product')!=PRODUCT or not re.fullmatch('[a-f0-9]{32}',marker.get('instance_id','')):raise ValueError('Unrecognized installation')
    return marker

def active_version(root):
    if (Path(root)/'uninstall-in-progress.json').exists():raise ValueError('Uninstall pending; use its saved Continue-uninstall.cmd entry')
    installation(root);version=read_json(Path(root)/'active.json')['version'];version_key(version);return version

def stage(source,root):
    document=verify_directory(source);root=Path(root).resolve();version=document['version']
    versions=root/'versions';versions.mkdir(exist_ok=True)
    target=versions/version
    if target.exists():
        existing=verify_directory(target)
        if existing!=document:raise ValueError('A different package uses this version; publish a new version')
        return target
    temporary=versions/('.staging-'+uuid.uuid4().hex)
    try:
        temporary.mkdir()
        for name in list(document['files'])+[MANIFEST]:
            dest=temporary.joinpath(*safe_path(name).parts);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(source)/name,dest)
        verify_directory(temporary);os.replace(temporary,target)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return target

def install(source,root,*,engine_port=4817):
    document=verify_directory(source);root=Path(root).resolve()
    if type(engine_port) is not int or not 1024<=engine_port<=65533 or engine_port in (4814,4815,4816):raise ValueError('Invalid or protected port')
    fresh=not root.exists() or not any(root.iterdir())
    if not fresh:
        installation(root)
        if (root/'active.json').exists() and active_version(root)!=document['version']:
            raise ValueError('A different version is already installed; use its management page to update safely')
    root.mkdir(parents=True,exist_ok=True)
    if fresh:atomic_json(root/'installation.json',{'product':PRODUCT,'instance_id':uuid.uuid4().hex,'engine_port':engine_port,'manager_port':engine_port+1})
    target=stage(source,root)
    data=root/'data';data.mkdir(exist_ok=True)
    for src,dest in [('app/pae/config.json','config.json'),('app/pae/persona/default.json','persona.json')]:
        if not (data/dest).exists():shutil.copyfile(target/src,data/dest)
    if not (root/'active.json').exists():atomic_json(root/'active.json',{'version':document['version'],'previous':None})
    return target

def snapshot_data(root):
    with data_owner(root):
        return _snapshot_data(root)

def _snapshot_data(root):
    root=Path(root);data=root/'data';backup=data/'backups'/uuid.uuid4().hex;backup.mkdir(parents=True)
    presence={name:(data/name).exists() for name in ('pae.db','config.json','persona.json','keys.json','http-auth.json','privacy-key.json')}
    if presence['pae.db']:
        with contextlib.closing(sqlite3.connect(str(data/'pae.db'))) as source,contextlib.closing(sqlite3.connect(str(backup/'pae.db'))) as target:source.backup(target)
    for name in ('config.json','persona.json','keys.json','http-auth.json','privacy-key.json'):
        if presence[name]:shutil.copyfile(data/name,backup/name)
    atomic_json(backup/'snapshot.json',{'files':presence})
    return backup

def restore_data(root,backup):
    with data_owner(root):
        return _restore_data(root,backup)

def _restore_data(root,backup):
    data=Path(root)/'data';state=read_json(Path(backup)/'snapshot.json')['files']
    # Caller has stopped its owned child. Never remove files in any other instance.
    for suffix in ('-wal','-shm'):
        path=data/('pae.db'+suffix)
        if path.exists():path.unlink()
    for name,present in state.items():
        if name not in ('pae.db','config.json','persona.json','keys.json','http-auth.json','privacy-key.json'):raise ValueError('Unknown snapshot path')
        if present:shutil.copyfile(Path(backup)/name,data/name)
        elif (data/name).exists():(data/name).unlink()

def upgrade(root,source,probe):
    root=Path(root).resolve();current=active_version(root);document=verify_directory(source)
    if version_key(document['version'])<=version_key(current):raise ValueError('Update must be newer')
    target=stage(source,root);backup=snapshot_data(root);old=read_json(root/'active.json')
    # Journal survives an interrupted switch; next manager restores it before starting.
    atomic_json(root/'pending-update.json',{'old':old,'new':document['version'],'backup':backup.relative_to(root).as_posix()})
    try:
        atomic_json(root/'active.json',{'version':document['version'],'previous':current,'rollback_backup':backup.relative_to(root).as_posix()})
        if not probe(target,root/'data'):raise RuntimeError('New version failed its health check')
    except BaseException:
        restore_data(root,backup);atomic_json(root/'active.json',old);(root/'pending-update.json').unlink();raise
    (root/'pending-update.json').unlink()
    return {'version':document['version'],'previous':current,'backup':str(backup)}

def recover_pending(root):
    root=Path(root);pending=root/'pending-update.json'
    if not pending.exists():return False
    installation(root);journal=read_json(pending);version_key(journal['old']['version'])
    backup=root.joinpath(*safe_path(journal['backup']).parts)
    if not backup.resolve().is_relative_to((root/'data/backups').resolve()):raise ValueError('Invalid recovery backup')
    restore_data(root,backup);atomic_json(root/'active.json',journal['old']);pending.unlink();return True

def select_release(release,current):
    version=str(release.get('tag_name','')).removeprefix('v')
    if release.get('draft') or release.get('prerelease'):return None
    if version_key(version)<=version_key(current):return None
    assets=[a for a in release.get('assets',[]) if a.get('name')==ASSET]
    if not assets:raise ValueError('This Release has no Windows installation package')
    if len(assets)!=1:raise ValueError('Ambiguous release asset')
    asset=assets[0];digest=str(asset.get('digest',''))
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):raise ValueError('Release asset has no SHA256 digest')
    expected='https://github.com/luomeii/OpenGloss/releases/download/'+release['tag_name']+'/'+ASSET
    if asset.get('browser_download_url')!=expected or type(asset.get('size')) is not int or not 0<asset['size']<=MAX_BYTES:raise ValueError('Invalid release download')
    return {'version':version,'notes':str(release.get('body') or '')[:100000],'url':expected,'sha256':digest[7:],'size':asset['size']}


def rollback(root,probe):
    root=Path(root).resolve();installation(root);old=read_json(root/'active.json')
    previous=old.get('previous');version_key(previous)
    previous_dir=root/'versions'/previous;verify_directory(previous_dir)
    restore=root.joinpath(*safe_path(old.get('rollback_backup','')).parts)
    if not restore.resolve().is_relative_to((root/'data/backups').resolve()):raise ValueError('No valid previous data backup')
    current_backup=snapshot_data(root)
    atomic_json(root/'pending-update.json',{'old':old,'new':previous,'backup':current_backup.relative_to(root).as_posix()})
    try:
        restore_data(root,restore)
        atomic_json(root/'active.json',{'version':previous,'previous':old['version'],'rollback_backup':current_backup.relative_to(root).as_posix()})
        if not probe(previous_dir,root/'data'):raise RuntimeError('Previous version failed its health check')
    except BaseException:
        restore_data(root,current_backup);atomic_json(root/'active.json',old);(root/'pending-update.json').unlink();raise
    (root/'pending-update.json').unlink()
    return {'version':previous}

def export_data(root,destination):
    root=Path(root)
    if root.is_symlink() or root.is_junction():raise ValueError('Linked installation root refused')
    installation(root)
    data=root/'data';destination=Path(destination)
    for path in (data,data/'backups',data/'exports',*(data/name for name in ('pae.db','config.json','persona.json','keys.json','http-auth.json','privacy-key.json'))):
        if path.is_symlink() or path.is_junction():raise ValueError('Linked data path refused')
    if destination.parent.resolve()!=(data/'exports').resolve() or destination.suffix!='.zip' or not re.fullmatch(r'[A-Za-z0-9_-]+\.zip',destination.name):raise ValueError('Invalid export destination')
    if destination.exists():raise ValueError('Export already exists')
    backup=snapshot_data(root)
    names=[name for name,present in read_json(backup/'snapshot.json')['files'].items() if present]
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:archive.write(backup/name,arcname=name)
    except BaseException:
        if destination.exists():destination.unlink()
        raise
    return destination

def _reject_links(tree):
    if tree.is_symlink() or tree.is_junction():raise ValueError('Linked install path refused')
    if tree.is_dir():
        for child in tree.iterdir():_reject_links(child)

UNINSTALL_JOURNAL='uninstall-in-progress.json'

def _uninstall_inventory(root):
    """Only the original program/cache tree may be removed during a retry."""
    files={};directories=[]
    for name in ('versions','downloads'):
        tree=root/name
        if not tree.exists():continue
        _reject_links(tree)
        if not tree.is_dir():raise ValueError('Invalid uninstall payload tree')
        directories.append(name)
        for path in tree.rglob('*'):
            relative=path.relative_to(root).as_posix()
            if path.is_dir():directories.append(relative)
            elif path.is_file():files[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
            else:raise ValueError('Invalid uninstall payload entry')
    return files,sorted(directories)

def _read_uninstall_journal(root,remove_data):
    journal=read_json(root/UNINSTALL_JOURNAL)
    if not isinstance(journal,dict) or set(journal)!={'format','product','operation_id','installation','remove_data','phase','files','directories'}:raise ValueError('Invalid uninstall journal')
    marker=journal['installation']
    if (journal['format']!=1 or journal['product']!=PRODUCT or
        not isinstance(marker,dict) or marker.get('product')!=PRODUCT or
        not re.fullmatch('[a-f0-9]{32}',str(marker.get('instance_id',''))) or
        not re.fullmatch('[a-f0-9]{32}',str(journal['operation_id'])) or
        type(journal['remove_data']) is not bool or journal['remove_data']!=remove_data or
        journal['phase'] not in ('deleting','files_removed','complete')):raise ValueError('Uninstall identity or data choice mismatch')
    if not isinstance(journal['files'],dict) or not isinstance(journal['directories'],list):raise ValueError('Invalid uninstall inventory')
    for name in list(journal['files'])+journal['directories']:
        if safe_path(name).parts[0] not in ('versions','downloads'):raise ValueError('Invalid uninstall boundary')
    if any(not re.fullmatch('[a-f0-9]{64}',str(digest)) for digest in journal['files'].values()):raise ValueError('Invalid uninstall checksum')
    if (root/'installation.json').exists():
        if installation(root)!=marker:raise ValueError('Uninstall installation identity changed')
    elif journal['phase'] not in ('files_removed','complete'):
        raise ValueError('Missing installation identity during deletion')
    return journal

def preflight_uninstall(root,*,remove_data=False):
    root=Path(root)
    if type(remove_data) is not bool:raise ValueError('Invalid data removal choice')
    if root.is_symlink() or root.is_junction():raise ValueError('Linked installation root refused')
    allowed={'installation.json','active.json','manager.lock','data-owner.lock','manager-session.json','pending-update.json','versions','downloads','data',UNINSTALL_JOURNAL}
    if any(path.name not in allowed for path in root.iterdir()):raise ValueError('Unknown installation contents')
    for name in ('installation.json','active.json','manager-session.json','pending-update.json',UNINSTALL_JOURNAL,'manager.lock','data-owner.lock'):
        path=root/name
        if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):raise ValueError('Invalid ownership sentinel')
    if (root/'data').is_symlink() or (root/'data').is_junction():raise ValueError('Linked data path refused')
    if (root/UNINSTALL_JOURNAL).exists():
        journal=_read_uninstall_journal(root,remove_data)
        files,directories=_uninstall_inventory(root)
        if (not set(directories).issubset(journal['directories']) or
            any(journal['files'].get(name)!=digest for name,digest in files.items())):raise ValueError('Uninstall payload changed; foreign files preserved')
        if journal['phase'] in ('files_removed','complete'):
            if directories or files or (remove_data and (root/'data').exists()):raise ValueError('Unexpected files after uninstall completion')
            if any((root/name).exists() for name in ('active.json','manager-session.json','pending-update.json')):raise ValueError('Unexpected installation metadata after cleanup')
        if remove_data and (root/'data').exists():_reject_links(root/'data')
        return root
    installation(root)
    active_version(root)
    versions=root/'versions'
    if not versions.is_dir():raise ValueError('Missing installed versions')
    for version in versions.iterdir():
        if not version.is_dir() or version.name.startswith('.'):raise ValueError('Unknown installed version')
        verify_directory(version)
    for name in ('versions','downloads'):
        if (root/name).exists():_reject_links(root/name)
    if remove_data and (root/'data').exists():_reject_links(root/'data')
    return root

def uninstall_files(root,*,remove_data=False,operation_id=None):
    root=preflight_uninstall(root,remove_data=remove_data)
    path=root/UNINSTALL_JOURNAL
    if path.exists():
        journal=_read_uninstall_journal(root,remove_data)
        if operation_id is not None and operation_id!=journal['operation_id']:raise ValueError('Uninstall operation changed')
    else:
        files,directories=_uninstall_inventory(root)
        journal={'format':1,'product':PRODUCT,'operation_id':operation_id or uuid.uuid4().hex,
                 'installation':installation(root),'remove_data':remove_data,'phase':'deleting',
                 'files':files,'directories':directories}
        if not re.fullmatch('[a-f0-9]{32}',str(journal['operation_id'])):raise ValueError('Invalid uninstall operation')
        atomic_json(path,journal)
    if journal['phase']=='complete':return True
    for name in ('versions','downloads'):
        if (root/name).exists():shutil.rmtree(root/name)
    for name in ('active.json','manager-session.json','pending-update.json'):
        if (root/name).exists():(root/name).unlink()
    if remove_data and (root/'data').exists():shutil.rmtree(root/'data')
    # The durable completion stage precedes marker removal. A missing marker can
    # only finalize an already empty payload, never authorize recursive deletion.
    journal['phase']='files_removed';atomic_json(path,journal)
    if (root/'installation.json').exists():(root/'installation.json').unlink()
    journal['phase']='complete';atomic_json(path,journal)
    # Windows cannot unlink a lock file while the helper holds its handle.
    # Keep the privacy-free lock sentinels and journal to cover final-write crashes.
    return True
