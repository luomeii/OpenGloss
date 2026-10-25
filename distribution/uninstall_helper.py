"""Detached per-install cleanup; runs from a temporary copy of the embedded runtime."""
from pathlib import Path
from contextlib import contextmanager
import ctypes, hashlib, json, msvcrt, os, re, subprocess, sys, time, winreg
from . import package
from .locks import data_owner


@contextmanager
def wait_for_manager(root, timeout=60):
    path = Path(root) / 'manager.lock'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with path.open('a+b') as stream:
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                time.sleep(.1)
                continue
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
    raise RuntimeError('Manager did not exit; uninstall was not completed')


def _desktop_shortcut(instance_id):
    name = 'OpenGloss-' + instance_id
    buffer = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) != 0:
        raise OSError('Desktop directory unavailable')
    return Path(buffer.value) / (name + '.lnk')


def _read_run(name):
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def _read_shortcut(path):
    script = '''$s = New-Object -ComObject WScript.Shell
$l = $s.CreateShortcut($env:OG_SHORTCUT_PATH)
@{ target=$l.TargetPath; arguments=$l.Arguments; working_directory=$l.WorkingDirectory } | ConvertTo-Json -Compress
'''
    env = dict(os.environ, OG_SHORTCUT_PATH=str(path))
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
        env=env, check=True, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(completed.stdout)


def inspect_owned_entries(root, instance_id):
    root = Path(root).resolve()
    version = root / 'versions' / package.active_version(root)
    target = str(version / 'runtime/pythonw.exe')
    root_arg = '--root "' + str(root) + '"'
    arguments = '-X utf8 -B -m distribution.bootstrap run ' + root_arg
    name = 'OpenGloss-' + instance_id
    command = '"' + target + '" ' + arguments + ' --no-browser'
    actual_run = _read_run(name)
    if actual_run is not None and actual_run != command:
        raise ValueError('Autostart entry does not belong to this installation')
    shortcut = _desktop_shortcut(instance_id)
    if shortcut.is_symlink() or shortcut.is_junction():
        raise ValueError('Linked shortcut refused')
    digest = None
    shortcut_details = {'target': target, 'arguments': arguments,
                        'working_directory': str(root)}
    if shortcut.exists():
        details = _read_shortcut(shortcut)
        shortcut_target = Path(str(details.get('target', '')))
        shortcut_version = shortcut_target.parent.parent
        versions = root / 'versions'
        if (shortcut_version.parent != versions or
            str(shortcut_target) != str(shortcut_version / 'runtime/pythonw.exe') or
            versions.is_symlink() or versions.is_junction() or
            shortcut_version.is_symlink() or shortcut_version.is_junction() or
            not shortcut_target.is_file() or shortcut_target.is_symlink() or
            shortcut_target.is_junction() or
            details.get('arguments') not in (arguments, arguments+' --no-browser') or
            details.get('working_directory') != str(root)):
            raise ValueError('Shortcut does not belong to this installation')
        package.verify_directory(shortcut_version)
        shortcut_details = {'target': str(shortcut_target), 'arguments': details['arguments'],
                            'working_directory': str(root)}
        if details != shortcut_details:
            raise ValueError('Shortcut does not belong to this installation')
        digest = hashlib.sha256(shortcut.read_bytes()).hexdigest()
    return {'name': name, 'command': command, 'shortcut': shortcut,
            'shortcut_details': shortcut_details,
            'run_present': actual_run is not None, 'shortcut_sha256': digest}


def remove_owned_entries(root, instance_id, snapshot):
    # A saved retry snapshot may outlive its program. Validate its fixed instance
    # and path boundaries again without depending on now-deleted executables.
    root=Path(root).resolve()
    if snapshot['name']!='OpenGloss-'+instance_id or Path(snapshot['shortcut'])!=_desktop_shortcut(instance_id):
        raise ValueError('Entry snapshot identity changed')
    arguments='-X utf8 -B -m distribution.bootstrap run --root "'+str(root)+'"'
    match=re.fullmatch(r'"([^"]+)" (.+)',snapshot['command'])
    if not match or match[2]!=arguments+' --no-browser':raise ValueError('Entry command changed')
    for target in (Path(match[1]),Path(snapshot['shortcut_details']['target'])):
        version=target.parent.parent
        if version.parent!=root/'versions' or target!=version/'runtime/pythonw.exe':raise ValueError('Entry target is foreign')
        package.version_key(version.name)
    if snapshot['shortcut_details'].get('arguments') not in (arguments,arguments+' --no-browser') or snapshot['shortcut_details'].get('working_directory')!=str(root):raise ValueError('Shortcut snapshot changed')
    skipped = []
    name = snapshot['name']
    if snapshot['run_present']:
        current=_read_run(name)
        if current is None:
            pass  # An earlier attempt already removed this exact entry.
        elif current != snapshot['command']:
            skipped.append('autostart')
        else:
            key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
            rights = winreg.KEY_SET_VALUE | getattr(winreg, 'KEY_QUERY_VALUE', 0)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, rights) as key:
                if winreg.QueryValueEx(key, name)[0] == snapshot['command']:
                    winreg.DeleteValue(key, name)
                else:
                    skipped.append('autostart')
    elif _read_run(name) is not None:
        skipped.append('autostart')
    shortcut = snapshot['shortcut']
    if snapshot['shortcut_sha256'] is not None:
        if not shortcut.exists() and not shortcut.is_symlink() and not shortcut.is_junction():
            pass
        elif shortcut.is_symlink() or shortcut.is_junction() or not shortcut.is_file():
            skipped.append('shortcut')
        elif (_read_shortcut(shortcut) != snapshot['shortcut_details'] or
              hashlib.sha256(shortcut.read_bytes()).hexdigest() != snapshot['shortcut_sha256']):
            skipped.append('shortcut')
        else:
            shortcut.unlink()
    elif shortcut.exists() or shortcut.is_symlink():
        skipped.append('shortcut')
    return skipped


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ('--watch','--resume','--execute'):
        from .uninstall_recovery import main as recovery_main
        return recovery_main(argv)
    root = Path(sys.argv[1]); instance_id = sys.argv[2]
    remove_data = sys.argv[3] == '1'
    result = Path(sys.argv[4])
    phase = 'preflight'
    try:
        if package.installation(root)['instance_id'] != instance_id:
            raise ValueError('Installation identity changed')
        with wait_for_manager(root), data_owner(root):
            if package.installation(root)['instance_id'] != instance_id:
                raise ValueError('Installation identity changed')
            snapshot = inspect_owned_entries(root, instance_id)
            phase = 'deleting'
            package.uninstall_files(root, remove_data=remove_data)
        phase = 'deleted'
        skipped = remove_owned_entries(root, instance_id, snapshot)
        if skipped:
            message = {'complete': False, 'program_removed': True, 'entry_cleanup': 'partial',
                'message': '程序已删除，但部分入口因外部改动未撤销；请人工检查本实例自启和桌面快捷方式。'}
        else:
            message = {'complete': True, 'program_removed': True, 'entry_cleanup': 'complete',
                'message': '此实例程序已卸载。'}
    except Exception:
        if phase == 'preflight':
            message = {'complete': False, 'program_removed': False,
                'message': '卸载预检未通过，程序未删除；请检查此实例入口及安装目录后重试。'}
        elif phase == 'deleted':
            message = {'complete': False, 'program_removed': True, 'entry_cleanup': 'partial',
                'message': '程序已删除，但入口撤销失败；请人工检查本实例自启和桌面快捷方式。'}
        else:
            message = {'complete': False, 'program_removed': None,
                'message': '程序清理中断，删除状态未确认；请人工检查安装目录，勿从旧入口反复启动。'}
    package.atomic_json(result, message)


if __name__ == '__main__':
    outcome=main()
    if isinstance(outcome,dict):raise SystemExit(0 if outcome.get('complete') else 1)
