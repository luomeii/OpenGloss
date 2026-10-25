"""Visible, version-independent entry points owned by one installation."""
from pathlib import Path
import hashlib, json, re, uuid, os

ROOT_FILES=frozenset({'README.md','Install.cmd','quick_start.bat','quick_stop.bat','autostart_on.bat','autostart_off.bat'})

def launcher(action):
    if action not in ('start','stop','autostart-on','autostart-off'):raise ValueError('Unknown root action')
    script=("$ErrorActionPreference='Stop';try {$r=$env:OG_ROOT;"
        "$v=[string](Get-Content -LiteralPath (Join-Path $r 'active.json') -Raw | ConvertFrom-Json).version;"
        "if($v -notmatch '^[0-9]+\\.[0-9]+\\.[0-9]+(?:-rc\\.[0-9]+)?$'){throw 'Invalid active version'};"
        "$p=Join-Path $r ('versions\\'+$v+'\\runtime\\python.exe');"
        "& $p -X utf8 -B -m distribution.bootstrap "+action+" --root $r;"
        "if($LASTEXITCODE -ne 0){throw 'Action did not finish'}"
        "}catch {Write-Host ('OpenGloss: '+$_.Exception.Message);exit 1}")
    return ('@echo off\r\nsetlocal\r\nset "OG_ROOT=%~dp0."\r\n'
        '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo -NoProfile -Command "'+script+'"\r\n'
        'if errorlevel 1 (\r\n  echo OpenGloss action failed. See the message above.\r\n  pause\r\n  exit /b 1\r\n)\r\nexit /b 0\r\n').encode('ascii')

def payload(version,port):
    version=Path(version)
    result={name:launcher(action) for name,action in (
        ('quick_start.bat','start'),('quick_stop.bat','stop'),
        ('autostart_on.bat','autostart-on'),('autostart_off.bat','autostart-off'),('Install.cmd','start'))}
    guide=next((p for p in (version/'README.md',version/'README-INSTALL.md') if p.is_file()),None)
    result['README.md']=guide.read_bytes() if guide else b'# OpenGloss\n\nStart with quick_start.bat, then load the extension folder in your browser.\nUse quick_stop.bat to stop this instance. Autostart is optional and off by default.\n'
    extension=version/'app/pae/extension'
    if extension.exists():
        for path in extension.rglob('*'):
            if path.is_symlink() or path.is_junction():raise ValueError('Linked extension file refused')
            if not path.is_file():continue
            name='extension/'+path.relative_to(extension).as_posix();data=path.read_bytes()
            if path.suffix in ('.js','.html','.json'):
                data=re.sub(r'http://127\.0\.0\.1:(4815|4817)(?=[/\'";\s]|$)',lambda _: 'http://127.0.0.1:'+str(port),data.decode('utf-8-sig')).encode('utf-8')
            result[name]=data
    return result

def _valid_name(name):
    if name in ROOT_FILES:return True
    parts=Path(name).parts
    return (isinstance(name,str) and name.startswith('extension/') and '\\' not in name and ':' not in name and
        all(p not in ('','..','.') for p in name.split('/')) and len(parts)>1)

def _directories(names):
    return {parent.as_posix() for name in names for parent in Path(name).parents if parent.as_posix() not in ('.','')}

def verify(root,marker):
    root=Path(root);expected=marker.get('entry_files',{})
    if not isinstance(expected,dict):raise ValueError('Invalid root entry manifest')
    for name,digest in expected.items():
        if not _valid_name(name) or not re.fullmatch('[a-f0-9]{64}',str(digest)):raise ValueError('Invalid root entry boundary')
        path=root/name
        if path.is_symlink() or path.is_junction() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Root entry changed; user file preserved')
    if (root/'extension').exists():
        directories=_directories(expected)
        for path in (root/'extension').rglob('*'):
            if path.is_symlink() or path.is_junction():raise ValueError('Linked extension path refused')
            if path.is_dir() and path.relative_to(root).as_posix() not in directories:raise ValueError('Unknown extension directory preserved')
            if path.is_file() and path.relative_to(root).as_posix() not in expected:raise ValueError('Unknown extension file preserved')
    return expected

def install(root,version,marker,atomic_json,*,other_version=None):
    root=Path(root);new=payload(version,marker['engine_port']);old=marker.get('entry_files',{})
    alternatives=payload(other_version,marker['engine_port']) if other_version else {}
    for name in set(old)|set(new)|set(alternatives):
        if not _valid_name(name):raise ValueError('Invalid entry boundary')
        path=root/name
        if path.is_symlink() or path.is_junction():raise ValueError('Linked root entry refused')
        if path.exists():
            allowed={old.get(name)}
            allowed.update(hashlib.sha256(items[name]).hexdigest() for items in (new,alternatives) if name in items)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() not in allowed:raise ValueError('Changed root file preserved')
    extension=root/'extension'
    if extension.is_symlink() or extension.is_junction():raise ValueError('Linked extension directory refused')
    if extension.exists():
        directories=_directories(set(old)|set(new)|set(alternatives))
        for path in extension.rglob('*'):
            name=path.relative_to(root).as_posix()
            if path.is_symlink() or path.is_junction() or (path.is_file() and name not in set(old)|set(new)|set(alternatives)):raise ValueError('Foreign extension contents preserved')
            if path.is_dir() and name not in directories:raise ValueError('Foreign extension directory preserved')
    for name,data in new.items():
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,target)
        finally:
            if temporary.exists():temporary.unlink()
    for name in set(old)-set(new):
        if (root/name).exists():(root/name).unlink()
    marker={**marker,'entry_files':{name:hashlib.sha256(data).hexdigest() for name,data in new.items()}}
    atomic_json(root/'installation.json',marker)
    return marker
