"""Self-contained Windows launcher, engine owner and opt-in release updater."""
from pathlib import Path
import argparse,contextlib,hmac,json,os,secrets,shutil,socket,subprocess,sys,threading,time,urllib.request,uuid,webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from . import package,network
from .locks import data_owner

def engine_environment(version,data):
    version=Path(version).resolve();data=Path(data).resolve()
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC') if k in os.environ}
    env.update(PATH=str(version/'runtime'),TEMP=str(data),TMP=str(data),PAE_DB_PATH=str(data/'pae.db'),PAE_DICT_PATH=str(version/'app/resources/ECDICT/dict.sqlite'),PAE_LEMMA_PATH=str(version/'app/resources/ECDICT/lemma.en.txt'),PAE_CONFIG_PATH=str(data/'config.json'),PAE_PERSONA_PATH=str(data/'persona.json'),PAE_KEYS_PATH=str(data/'keys.json'),PAE_SCHEDULE_CONFIG=str(data/'config.json'))
    return env

def authorized(headers,token,port):
    actual=headers.get('X-OpenGloss-Token','')
    origin=headers.get('Origin','')
    return bool(token and hmac.compare_digest(str(actual),token) and origin in ('','http://127.0.0.1:'+str(port)))

@contextlib.contextmanager
def instance_lock(root):
    import msvcrt
    path=Path(root)/'manager.lock'
    with path.open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('This installation is already running. Open its management page or close it first.')
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

def engine_main(root,version):
    # Keep ownership until the OS has terminated every thread/SQLite connection.
    # This entry runs only in our dedicated child, never in the manager process.
    owner=data_owner(root);owner.__enter__()
    exit_code=0
    try:_engine_main(root,version)
    except BaseException:
        exit_code=1
        import traceback
        with contextlib.suppress(Exception):traceback.print_exc()
    finally:
        for stream in (sys.stdout,sys.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):stream.flush()
        os._exit(exit_code)

def _engine_main(root,version):
    root=Path(root).resolve();package.installation(root);version=Path(version).resolve()
    if version.parent!=(root/'versions').resolve():raise ValueError('Engine version is outside this installation')
    sys.path.insert(0,str(version/'app/pae'))
    import uvicorn
    from pae_core import engine
    from .admission import Admission
    from pae_core.api import app
    from pae_core import scheduler
    pending=root/'pending-update.json';was_pending=pending.exists();original_config=scheduler.config
    def config_after_commit():
        config=original_config()
        return {**config,'enabled':False} if pending.exists() else config
    scheduler.config=config_after_commit
    config=uvicorn.Config(Admission(app,root),host='127.0.0.1',port=package.installation(root)['engine_port'],log_level='warning',access_log=False)
    server=uvicorn.Server(config)
    if was_pending:
        def schedule_after_commit():
            while pending.exists():
                if server.should_exit:return
                time.sleep(.1)
            if not server.should_exit and original_config().get('enabled'):
                scheduler.start(str(root/'data/pae.db'))
        threading.Thread(target=schedule_after_commit,daemon=True).start()
    def reader():
        sys.stdin.readline();server.should_exit=True
    threading.Thread(target=reader,daemon=True).start()
    try:server.run()
    finally:
        if engine._ENGINE is not None:engine._ENGINE.conn.close()

class Supervisor:
    def __init__(self,root):
        self.root=Path(root).resolve();self.settings=package.installation(self.root)
        self.child=None;self.log=None;self.mutex=threading.RLock();self.offer=None;self.busy=False;self.closing=False
        self.state={'stage':'starting','message':'正在启动引擎','update':None}
    def start(self,version=None):
        if self.child is not None and self.child.poll() is None:raise RuntimeError('Owned engine is already running')
        version=version or self.root/'versions'/package.active_version(self.root)
        package.verify_directory(version)
        self.log=(self.root/'data/engine.log').open('a',encoding='utf-8')
        self.child=subprocess.Popen([str(version/'runtime/python.exe'),'-X','utf8','-B','-m','distribution.bootstrap','engine','--root',str(self.root),'--version-dir',str(version)],cwd=self.root/'data',env=engine_environment(version,self.root/'data'),stdin=subprocess.PIPE,stdout=self.log,stderr=self.log,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+180
        try:
            while time.monotonic()<deadline:
                if self.child.poll() is not None:raise RuntimeError('Engine exited; see this installation data/engine.log')
                try:
                    with opener.open('http://127.0.0.1:%d/v1/health'%self.settings['engine_port'],timeout=2) as response:body=json.load(response)
                    if body.get('ok') and Path(body.get('db','')).resolve()==(self.root/'data/pae.db').resolve():return True
                except (OSError,ValueError):pass
                time.sleep(.2)
            raise RuntimeError('Engine readiness timed out')
        except BaseException:self.stop();raise
    def stop(self):
        p=self.child
        if p is not None and p.poll() is None:
            try:p.stdin.write('stop\n');p.stdin.flush();p.wait(timeout=20)
            except (OSError,subprocess.TimeoutExpired):p.terminate();p.wait(timeout=10)
        if self.log is not None:self.log.close();self.log=None
        self.child=None
    def status(self):
        return {**self.state,'version':package.active_version(self.root),'engine_url':'http://127.0.0.1:%d'%self.settings['engine_port'],'engine_running':self.child is not None and self.child.poll() is None,'busy':self.busy,'instance_id':self.settings['instance_id'],'data_directory':str(self.root/'data')}
    def check(self):
        self.state.update(stage='checking',message='正在检查 GitHub Release')
        try:
            self.offer=network.latest(package.active_version(self.root))
            self.state.update(stage='ready',message='发现可安装的新版本' if self.offer else '当前没有更新的正式安装版',update=self.offer)
        except Exception as error:self.state.update(stage='check_failed',message=str(error),update=None);self.offer=None
    def perform_update(self):
        cache=self.root/'downloads';cache.mkdir(exist_ok=True);archive=cache/(uuid.uuid4().hex+'.zip');folder=cache/uuid.uuid4().hex
        try:
            offer=dict(self.offer or {})
            if not offer:raise ValueError('Check for a verified release before updating')
            self.state.update(stage='downloading',message='正在下载并校验更新')
            network.download(offer,archive);package.unpack(archive,folder)
            if package.verify_directory(folder)['version']!=offer['version']:raise ValueError('Package version does not match GitHub Release')
            self.state.update(stage='switching',message='正在备份数据并切换版本')
            self.stop()
            try:package.upgrade(self.root,folder,lambda version,data:self.start(version))
            except BaseException:
                if self.child is not None:self.stop()
                package.recover_pending(self.root);self.start();raise
            self.offer=None;self.state.update(stage='ready',message='更新完成，用户数据已保留',update=None)
        except Exception as error:self.state.update(stage='update_failed',message=str(error))
        finally:
            if archive.exists():archive.unlink()
            if folder.exists():shutil.rmtree(folder)
    def action(self,name):
        with self.mutex:
            if self.closing:return
            if name=='check':self.check()
            elif name=='update':self.perform_update()
            elif name=='rollback':
                self.stop()
                try:package.rollback(self.root,lambda version,data:self.start(version))
                except BaseException:
                    self.stop();package.recover_pending(self.root);self.start();raise
                self.offer=None;self.state.update(stage='ready',message='已回退程序和更新前的数据；回退前的数据已另行备份',update=None)
            elif name=='restart':
                self.stop();self.start();self.state.update(stage='ready',message='引擎已重新启动')
            else:raise ValueError('Unknown management action')
    def admit_shutdown(self):
        # A slow download/start owns mutex. Reject concurrent requests promptly.
        if not self.mutex.acquire(blocking=False):return False
        try:
            if self.busy or self.closing:return False
            self.closing=True
            return True
        finally:self.mutex.release()
    def submit(self,name):
        if not self.mutex.acquire(blocking=False):
            raise RuntimeError('Another management operation is still running')
        try:
            if self.closing:raise RuntimeError('This manager is closing')
            if self.busy:raise RuntimeError('Another management operation is still running')
            self.busy=True
        finally:self.mutex.release()
        def work():
            try:self.action(name)
            except Exception as error:self.state.update(stage='failed',message=str(error))
            finally:self.busy=False
        threading.Thread(target=work,daemon=True).start()

class ManagementServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

def reopen_existing(root,*,no_browser=False):
    root=Path(root).resolve()
    try:
        settings=package.installation(root);session=package.read_json(root/'manager-session.json')
        expected='http://127.0.0.1:'+str(settings['manager_port'])
        if session.get('url')!=expected or session.get('instance_id')!=settings['instance_id'] or not isinstance(session.get('token'),str):return False
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request=urllib.request.Request(expected+'/api/status',headers={'X-OpenGloss-Token':session['token']})
        with opener.open(request,timeout=3) as response:status=json.load(response)
        if status.get('instance_id')!=settings['instance_id']:return False
        if not no_browser:webbrowser.open(expected+'/manage#token='+session['token'])
        return True
    except (OSError,ValueError):return False

def run(root,*,no_browser=False):
    root=Path(root).resolve();lock=instance_lock(root)
    try:lock.__enter__()
    except RuntimeError:
        if reopen_existing(root,no_browser=no_browser):return
        raise
    try:return _run_owned(root,no_browser=no_browser)
    finally:lock.__exit__(None,None,None)

def _run_owned(root,*,no_browser=False):
    root=Path(root).resolve();settings=package.installation(root)
    # Never restore a database while a surviving process still holds its port.
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as guard:
        guard.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        guard.bind(('127.0.0.1',settings['engine_port']))
    package.recover_pending(root)
    supervisor=Supervisor(root);token=secrets.token_urlsafe(32);port=settings['manager_port']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,body,kind='application/json; charset=utf-8'):
            raw=(json.dumps(body,ensure_ascii=False) if isinstance(body,dict) else body).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(raw)
        def valid_host(self):return self.headers.get('Host')=='127.0.0.1:'+str(port)
        def do_GET(self):
            if not self.valid_host():return self.send(403,{'error':'invalid_host'})
            if self.path in ('/','/manage'):
                return self.send(200,(Path(__file__).parent/'manage.html').read_text(encoding='utf-8'),'text/html; charset=utf-8')
            if self.path in ('/manage.js','/manage.css'):
                return self.send(200,(Path(__file__).parent/self.path[1:]).read_text(encoding='utf-8'),'text/javascript; charset=utf-8' if self.path.endswith('.js') else 'text/css; charset=utf-8')
            if self.path=='/api/status' and authorized(self.headers,token,port):return self.send(200,supervisor.status())
            return self.send(403,{'error':'management_token_required'})
        def do_POST(self):
            if not self.valid_host() or not authorized(self.headers,token,port):return self.send(403,{'error':'management_token_required'})
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length','0') not in ('0','2'):return self.send(400,{'error':'unexpected_body'})
            if self.headers.get('Content-Length')=='2':self.rfile.read(2)
            if self.path=='/api/stop':
                if not supervisor.admit_shutdown():return self.send(409,{'error':'operation_in_progress'})
                self.send(200,{'ok':True});threading.Thread(target=server.shutdown,daemon=True).start();return
            name=self.path.removeprefix('/api/')
            if name not in ('check','update','restart','rollback'):return self.send(404,{'error':'unknown_action'})
            try:supervisor.submit(name);self.send(202,{'ok':True})
            except RuntimeError as error:self.send(409,{'error':str(error)})
    server=ManagementServer(('127.0.0.1',port),Handler)
    session=root/'manager-session.json'
    try:
        package.atomic_json(session,{'url':'http://127.0.0.1:%d'%port,'token':token,'instance_id':settings['instance_id']})
        supervisor.start();supervisor.state.update(stage='ready',message='引擎已就绪')
        supervisor.submit('check')
        if not no_browser:webbrowser.open('http://127.0.0.1:%d/manage#token=%s'%(port,token))
        server.serve_forever(poll_interval=.2)
    finally:
        with supervisor.mutex:
            supervisor.closing=True
            supervisor.stop()
        server.server_close()
        if session.exists():session.unlink()

def create_shortcut(root):
    # Current-user desktop shortcut; never creates autostart or changes browser/Python settings.
    root=Path(root).resolve();version=root/'versions'/package.active_version(root)
    script='''$s = New-Object -ComObject WScript.Shell
$l = $s.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'OpenGloss.lnk'))
$l.TargetPath = $env:OG_SHORTCUT_TARGET
$l.Arguments = $env:OG_SHORTCUT_ARGUMENTS
$l.WorkingDirectory = $env:OG_SHORTCUT_ROOT
$l.Save()
'''
    env=dict(os.environ,OG_SHORTCUT_TARGET=str(version/'runtime/pythonw.exe'),OG_SHORTCUT_ARGUMENTS='-X utf8 -B -m distribution.bootstrap run --root "'+str(root)+'"',OG_SHORTCUT_ROOT=str(root))
    subprocess.run(['powershell.exe','-NoProfile','-Command',script],env=env,check=True,creationflags=subprocess.CREATE_NO_WINDOW)

def main(argv=None):
    parser=argparse.ArgumentParser(description='OpenGloss isolated Windows installation')
    parser.add_argument('action',choices=('install','run','engine'))
    parser.add_argument('--root',type=Path)
    parser.add_argument('--version-dir',type=Path)
    parser.add_argument('--port',type=int,default=4817)
    parser.add_argument('--no-launch',action='store_true');parser.add_argument('--no-shortcuts',action='store_true');parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args(argv)
    if args.root is not None:root=args.root.resolve()
    else:
        local_appdata=os.environ.get('LOCALAPPDATA')
        root=((Path(local_appdata) if local_appdata else Path.home()/'AppData/Local')/'OpenGloss').resolve()
    if args.action=='install':
        source=Path(__file__).resolve().parent.parent
        target=package.install(source,root,engine_port=args.port)
        if not args.no_shortcuts:create_shortcut(root)
        print('OpenGloss installed: '+str(root),flush=True)
        if not args.no_launch:
            command=[str(target/'runtime/pythonw.exe'),'-X','utf8','-B','-m','distribution.bootstrap','run','--root',str(root)]
            if args.no_browser:command.append('--no-browser')
            process=subprocess.Popen(command,cwd=root,creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+190;opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic()<deadline:
                try:
                    session=package.read_json(root/'manager-session.json')
                    settings=package.installation(root);expected='http://127.0.0.1:'+str(settings['manager_port'])
                    if session.get('url')!=expected or session.get('instance_id')!=settings['instance_id']:raise ValueError('Manager identity mismatch')
                    request=urllib.request.Request(expected+'/api/status',headers={'X-OpenGloss-Token':session['token']})
                    with opener.open(request,timeout=2) as response:state=json.load(response)
                    if state.get('instance_id')!=settings['instance_id']:raise ValueError('Manager identity mismatch')
                    if state.get('engine_running') and state.get('stage')!='starting':
                        print('OpenGloss engine is ready.',flush=True);break
                except (OSError,ValueError):pass
                if process.poll() not in (None,0):raise RuntimeError('The installed manager could not start. See data/manager-error.log; no existing process was stopped.')
                time.sleep(.2)
            else:raise RuntimeError('Installed manager readiness timed out; inspect this installation data directory')
    elif args.action=='run':
        current=root/'versions'/package.active_version(root)
        if Path(__file__).resolve().parent.parent!=current.resolve():
            command=[str(current/'runtime/pythonw.exe'),'-X','utf8','-B','-m','distribution.bootstrap','run','--root',str(root)]
            if args.no_browser:command.append('--no-browser')
            subprocess.Popen(command,cwd=root,creationflags=subprocess.CREATE_NO_WINDOW)
        else:run(root,no_browser=args.no_browser)
    else:
        if args.version_dir is None:parser.error('--version-dir is required for the internal engine')
        engine_main(root,args.version_dir)
if __name__=='__main__':
    try:main()
    except Exception as error:
        if sys.stderr is not None:print('OpenGloss: '+str(error),file=sys.stderr)
        if 'run' in sys.argv and '--root' in sys.argv:
            with contextlib.suppress(Exception):
                error_root=Path(sys.argv[sys.argv.index('--root')+1]);package.installation(error_root)
                (error_root/'data/manager-error.log').write_text(str(error)+'\n',encoding='utf-8')
        raise SystemExit(1)
