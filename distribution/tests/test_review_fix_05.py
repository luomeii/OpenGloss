from pathlib import Path
import contextlib, hashlib, json, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, locks, package, uninstall_helper


class ReviewFixContracts(unittest.TestCase):
    def installed(self, base):
        source = base / 'source'; source.mkdir()
        files = {
            'runtime/python.exe': b'synthetic runtime',
            'runtime/pythonw.exe': b'synthetic background runtime',
            'distribution/bootstrap.py': b'# synthetic\n',
            'app/pae/config.json': b'{"privacy":{"url_storage":"full","context_storage":true,"shadow_model":false}}',
            'app/pae/persona/default.json': b'{}',
        }
        for name, content in files.items():
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        package.atomic_json(source / package.MANIFEST, {
            'format': 1, 'product': 'OpenGloss', 'version': '0.4.0',
            'platform': 'windows-x64',
            'files': {name: {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}
                      for name, content in files.items()}})
        root = base / 'installed'
        package.install(source, root, engine_port=54985)
        return root

    def test_normal_data_owner_sentinel_allows_uninstall(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.installed(Path(temp))
            with locks.data_owner(root):
                pass
            (root / 'manager.lock').write_bytes(b'0')
            self.assertTrue(package.uninstall_files(root))
            self.assertFalse((root / 'versions').exists())
            self.assertTrue((root / 'data').is_dir())
            self.assertTrue((root / 'data-owner.lock').is_file())

    def test_helper_owns_both_locks_through_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.installed(Path(temp))
            entered = threading.Event(); release = threading.Event()
            actual = package.uninstall_files
            def slow_cleanup(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(5), 'cleanup barrier timed out')
                return actual(*args, **kwargs)
            result = root / 'data/uninstall-status.json'
            instance_id = package.installation(root)['instance_id']
            with patch.object(package, 'uninstall_files', side_effect=slow_cleanup), \
                 patch.object(uninstall_helper, 'inspect_owned_entries', return_value={'synthetic': True}) as inspect_entry, \
                 patch.object(uninstall_helper, 'remove_owned_entries', return_value=[]) as remove_entry, \
                 patch.object(sys, 'argv', ['helper', str(root), instance_id, '0', str(result)]):
                worker = threading.Thread(target=uninstall_helper.main)
                worker.start()
                try:
                    self.assertTrue(entered.wait(5), 'helper never reached cleanup')
                    with self.assertRaises(RuntimeError):
                        with bootstrap.instance_lock(root):
                            pass
                    with self.assertRaises(RuntimeError):
                        with locks.data_owner(root):
                            pass
                finally:
                    release.set(); worker.join(5)
                self.assertFalse(worker.is_alive())
            self.assertTrue(package.read_json(result)['complete'])
            inspect_entry.assert_called_once_with(root, instance_id)
            remove_entry.assert_called_once()
            self.assertEqual(remove_entry.call_args.args[:2], (root, instance_id))

    def test_uninstall_preflight_and_failed_schedule_keep_recovery_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.installed(Path(temp))
            shortcut = Path(temp) / 'owned-shortcut.lnk'
            shortcut.write_bytes(b'synthetic shortcut')
            (root / 'unrelated.txt').write_text('keep', encoding='utf-8')
            calls = []
            with patch.object(bootstrap, '_desktop_shortcut', return_value=shortcut), \
                 patch.object(bootstrap, 'set_autostart', side_effect=lambda *args: calls.append(args)), \
                 patch.object(bootstrap.subprocess, 'Popen') as launch:
                with self.assertRaises(ValueError):
                    bootstrap.schedule_uninstall(root, False)
            self.assertEqual(calls, [])
            launch.assert_not_called()
            self.assertTrue(shortcut.exists())
            (root / 'unrelated.txt').unlink()
            state = {'enabled': True}
            def set_state(_root, enabled): state['enabled'] = enabled
            with patch.object(bootstrap, '_desktop_shortcut', return_value=shortcut), \
                 patch.object(bootstrap, 'set_autostart', side_effect=set_state), \
                 patch.object(bootstrap.subprocess, 'Popen', side_effect=OSError('synthetic launch failure')) as launch:
                with self.assertRaises(OSError):
                    bootstrap.schedule_uninstall(root, False)
            launch.assert_called_once()
            self.assertTrue(state['enabled'])
            self.assertTrue(shortcut.exists())

    @contextlib.contextmanager
    def manager(self, root):
        with patch.object(bootstrap.Supervisor, 'start', lambda self, version=None: True), \
             patch.object(bootstrap.Supervisor, 'stop', lambda self: None), \
             patch.object(bootstrap.Supervisor, 'check', lambda self: None):
            worker = threading.Thread(target=bootstrap._run_owned, args=(root,), kwargs={'no_browser': True}, daemon=True)
            worker.start()
            session = root / 'manager-session.json'
            for _ in range(200):
                if session.exists(): break
                time.sleep(.01)
            self.assertTrue(session.exists(), 'manager session missing')
            token = package.read_json(session)['token']
            try:
                yield lambda path, body=None: self.request(path, token, body)
            finally:
                try: self.request('/api/stop', token, {})
                except Exception: pass
                worker.join(5)
                self.assertFalse(worker.is_alive(), 'synthetic manager did not stop')

    def request(self, path, token, body=None):
        base = 'http://127.0.0.1:54986'
        raw = None if body is None else json.dumps(body).encode()
        headers = {'X-OpenGloss-Token': token}
        if raw is not None: headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base + path, data=raw, headers=headers,
                                         method='GET' if body is None else 'POST')
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            try: return error.code, json.load(error)
            finally: error.close()

    def test_fast_settings_claim_admission_before_any_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.installed(Path(temp))
            payload = {'url_storage': 'hash', 'context_storage': False, 'shadow_model': True}
            entered = threading.Event(); release = threading.Event(); outcome = []
            original = bootstrap.save_privacy
            def slow_save(*args):
                entered.set()
                self.assertTrue(release.wait(5), 'privacy barrier timed out')
                return original(*args)
            with self.manager(root) as request, patch.object(bootstrap, 'save_privacy', side_effect=slow_save):
                worker = threading.Thread(target=lambda: outcome.append(request('/api/privacy', payload)))
                worker.start()
                try:
                    self.assertTrue(entered.wait(5))
                    status, _ = request('/api/check', {})
                    self.assertEqual(status, 409, 'a second action entered before privacy completed')
                finally:
                    release.set(); worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(outcome[0][0], 202)
                self.assertEqual(package.read_json(root / 'data/config.json')['privacy']['url_storage'], 'hash')

    def test_uninstall_result_hides_absolute_host_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.installed(Path(temp))
            # The scheduler now durably creates a pending result BEFORE launch.
            # Preserve that contract in this HTTP fixture; no helper is executed.
            package.atomic_json(root / 'data/uninstall-status.json', {
                'state':'pending','recovery_entry':str(Path(temp)/'OpenGloss-uninstall-synthetic/Continue-uninstall.cmd')})
            with self.manager(root) as request, \
                 patch.object(bootstrap, 'schedule_uninstall', return_value=root / 'data/uninstall-status.json'):
                status, body = request('/api/uninstall', {'confirm': 'uninstall-this-instance', 'remove_data': False})
                self.assertEqual(status, 202)
                self.assertFalse(any(str(root) in value for value in body.values()
                                     if isinstance(value, str)))
                self.assertIn('uninstall-status.json', body.get('result_file', ''))

    def test_private_key_extension_rejected(self):
        with self.assertRaises(ValueError):
            package.payload_path('runtime/private.key')

    def test_privacy_form_keeps_unsaved_user_selection_on_refresh(self):
        script = (ROOT / 'distribution/manage.js').read_text(encoding='utf-8')
        harness = r"""
const vm=require('vm'), fs=require('fs');
const nodes=new Map(), callbacks={};
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',checked:false,textContent:'',hidden:false,disabled:false,dataset:{},href:'',addEventListener:(type,fn)=>{callbacks[id+':'+type]=fn}});return nodes.get(id)}
let tick;
const state={version:'0.5.0',message:'ready',engine_running:true,engine_url:'http://127.0.0.1:54985',engine_token:'x',busy:false,privacy:{url_storage:'full',context_storage:true,shadow_model:false},autostart:false};
const context={URLSearchParams,location:{hash:'#token=t',pathname:'/manage'},history:{replaceState(){}},document:{getElementById:node},navigator:{clipboard:{writeText:async()=>{}}},fetch:async()=>({ok:true,json:async()=>state}),setInterval:fn=>{tick=fn},setTimeout,URL:{createObjectURL(){return ''},revokeObjectURL(){}}};
vm.runInNewContext(fs.readFileSync(0,'utf8'),context);
(async()=>{await new Promise(setImmediate);node('url-storage').value='hash';for(const type of ['input','change']){const handler=callbacks['url-storage:'+type];if(handler)handler({target:node('url-storage')})}await tick();if(node('url-storage').value!=='hash')process.exit(2)})().catch(()=>process.exit(3));
"""
        result = subprocess.run(['node', '-e', harness], input=script, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr or 'unsaved privacy selection was reset')


if __name__ == '__main__':
    unittest.main()
