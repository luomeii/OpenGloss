"""Bounded detached uninstall recovery and one-shot timeout reporting."""
from pathlib import Path
from contextlib import contextmanager
import json, msvcrt, re, subprocess, time, uuid
from . import package


def request_at(path):
    path=Path(path).resolve();request=package.read_json(path)
    if (path.name!='uninstall-request.json' or
        request.get('format')!=1 or type(request.get('remove_data')) is not bool or
        not re.fullmatch('[a-f0-9]{32}',str(request.get('instance_id',''))) or
        not re.fullmatch('[a-f0-9]{32}',str(request.get('operation_id','')))):
        raise ValueError('Invalid uninstall request')
    root=Path(request['root'])
    if not root.is_absolute() or root.is_symlink() or root.is_junction():raise ValueError('Invalid uninstall root')
    expected=path.parent/'uninstall-status.json' if request['remove_data'] else root/'data/uninstall-status.json'
    if Path(request['result'])!=expected:raise ValueError('Invalid uninstall result path')
    return path,request


@contextmanager
def status_lock(control):
    with (control/'status.lock').open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def status(path,attempt,state,*,replace_operation=False,**extra):
    path,request=request_at(path)
    with status_lock(path.parent):
        target=Path(request['result'])
        if target.is_symlink() or target.is_junction():raise ValueError('Linked uninstall status refused')
        if target.exists():
            old=package.read_json(target)
            if old.get('operation_id')!=request['operation_id'] and not replace_operation:raise ValueError('Uninstall result belongs to another operation')
            if state!='pending' and old.get('attempt_id')!=attempt:return False
            if state=='unconfirmed' and old.get('state')!='pending':return False
        package.atomic_json(target,{'operation_id':request['operation_id'],'attempt_id':attempt,
            'state':state,'complete':False,'program_removed':None,
            'recovery_entry':str(path.parent/'Continue-uninstall.cmd'),**extra})
    return True


def start_watch(path,attempt):
    path,_=request_at(path)
    return subprocess.Popen([str(path.parent/'runtime/python.exe'),'-X','utf8','-B',
        '-m','distribution.uninstall_helper','--watch',str(path),attempt],
        cwd=path.parent,creationflags=subprocess.CREATE_NO_WINDOW)


def watch(path,attempt,timeout=300,poll=.5):
    path,request=request_at(path);deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        with status_lock(path.parent):
            value=package.read_json(request['result'])
        if value.get('attempt_id')!=attempt or value.get('state')!='pending':return
        time.sleep(poll)
    status(path,attempt,'unconfirmed',message='尚未确认卸载完成；请保留继续卸载入口，确认原管理器退出后重试。')


def execute(path,*,resume=False):
    from . import uninstall_helper as helper
    path,request=request_at(path);root=Path(request['root'])
    attempt=uuid.uuid4().hex if resume else package.read_json(request['result'])['attempt_id']
    phase='preflight'
    try:
        # Keep both ownership locks through status publication. Concurrent retries
        # cannot replace a running attempt's state or delete the other instance.
        with helper.wait_for_manager(root),helper.data_owner(root):
            package.preflight_uninstall(root,remove_data=request['remove_data'])
            journal_path=root/package.UNINSTALL_JOURNAL
            journal=package.read_json(journal_path) if journal_path.exists() else None
            marker=journal['installation'] if journal else package.installation(root)
            if marker['instance_id']!=request['instance_id']:raise ValueError('Installation identity changed')
            if journal and journal['operation_id']!=request['operation_id']:raise ValueError('Uninstall operation changed')
            if resume:
                status(path,attempt,'pending',message='正在继续清理此实例；未确认完成。')
                start_watch(path,attempt)
            saved=path.parent/'owned-entries.json'
            if journal:
                record=package.read_json(saved)
                if record.get('operation_id')!=request['operation_id']:raise ValueError('Entry snapshot belongs to another operation')
                snapshot=record['snapshot'];snapshot['shortcut']=Path(snapshot['shortcut'])
            else:
                snapshot=helper.inspect_owned_entries(root,request['instance_id'])
                package.atomic_json(saved,{'operation_id':request['operation_id'],
                    'snapshot':{**snapshot,'shortcut':str(snapshot['shortcut'])}})
            phase='deleting'
            package.uninstall_files(root,remove_data=request['remove_data'],operation_id=request['operation_id'])
            phase='deleted'
            skipped=helper.remove_owned_entries(root,request['instance_id'],snapshot)
            status(path,attempt,'partial' if skipped else 'complete',complete=not skipped,
                program_removed=True,entry_cleanup='partial' if skipped else 'complete',
                message='程序已删除；部分入口已被外部改动，请人工检查。' if skipped else '此实例程序已卸载。')
    except Exception:
        # A second recovery click may fail to acquire ownership; it must not
        # overwrite another running attempt or claim it has finished.
        status(path,attempt,'interrupted' if phase=='deleting' else 'partial' if phase=='deleted' else 'preflight_failed',
            program_removed=True if phase=='deleted' else None,
            message='卸载尚未确认完成；请确认此实例已关闭，排除文件占用后使用继续卸载入口。')
    return package.read_json(request['result'])


def main(argv):
    if argv[0]=='--watch':watch(argv[1],argv[2])
    else:
        result=execute(argv[1],resume=argv[0]=='--resume')
        if argv[0]=='--resume':
            print(result['message'],flush=True)
            print('状态文件：'+request_at(argv[1])[1]['result'],flush=True)
        return result
