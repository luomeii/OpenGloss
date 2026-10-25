"""Recovery entry contracts using synthetic installs and mocked system adapters."""
from pathlib import Path
import json, subprocess, sys, unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
from distribution import bootstrap, package, uninstall_helper, uninstall_recovery
import test_uninstall_resume as fixtures


class DetachedRecoveryContracts(unittest.TestCase):
    def setUp(self): fixtures.UninstallResumeContracts.setUp(self)

    def schedule(self):
        control = self.base / 'detached'; control.mkdir()
        with patch.object(bootstrap.tempfile, 'mkdtemp', return_value=str(control)), \
             patch.object(bootstrap, '_desktop_shortcut', return_value=self.base / 'owned.lnk'), \
             patch.object(bootstrap.subprocess, 'Popen') as spawn:
            result = bootstrap.schedule_uninstall(self.root, False)
        return control, result, spawn

    def test_schedule_persists_pending_result_before_any_helper_runs(self):
        control, result, spawn = self.schedule()
        self.assertTrue(result.is_file(), 'a killed helper must not leave a nonexistent result')
        self.assertEqual(package.read_json(result)['state'], 'pending')
        self.assertTrue((control / 'Continue-uninstall.cmd').is_file())
        self.assertTrue((control / 'uninstall-request.json').is_file())
        self.assertTrue((control / 'distribution/uninstall_recovery.py').is_file())
        self.assertGreaterEqual(spawn.call_count, 2, 'independent timeout reporter plus worker')

    def test_scheduled_entry_resumes_partial_program_deletion(self):
        control, result, _ = self.schedule()
        request = control / 'uninstall-request.json'
        snapshot = {'name': 'OpenGloss-' + package.installation(self.root)['instance_id'],
                    'shortcut': str(self.base / 'owned.lnk'), 'run_present': False,
                    'command': 'synthetic', 'shortcut_details': {}, 'shortcut_sha256': None}
        original = package.shutil.rmtree
        def fail_once(path, *args, **kwargs):
            if Path(path) == self.root / 'versions':
                next(p for p in Path(path).rglob('*') if p.is_file()).unlink()
                raise PermissionError('synthetic interruption')
            return original(path, *args, **kwargs)
        with patch.object(uninstall_helper, 'inspect_owned_entries', return_value=snapshot), \
             patch.object(uninstall_helper, 'remove_owned_entries', return_value=[]), \
             patch.object(uninstall_helper.subprocess, 'Popen'), \
             patch.object(package.shutil, 'rmtree', side_effect=fail_once):
            uninstall_helper.main(['--resume', str(request)])
        self.assertEqual(package.read_json(result)['state'], 'interrupted')
        with patch.object(uninstall_helper, 'inspect_owned_entries', side_effect=AssertionError('must use saved snapshot')), \
             patch.object(uninstall_helper, 'remove_owned_entries', return_value=[]), \
             patch.object(uninstall_helper.subprocess, 'Popen'):
            uninstall_helper.main(['--resume', str(request)])
        self.assertEqual(package.read_json(result)['state'], 'complete')
        self.assertTrue(self.sentinel.is_file())

    def test_failed_update_has_explicit_recovery_without_starting_engine(self):
        with patch.object(package, 'recover_pending', side_effect=OSError('synthetic restore failure')), \
             patch.object(bootstrap.Supervisor, 'start') as start:
            with self.assertRaises(RuntimeError) as caught:
                bootstrap.main(['recover', '--root', str(self.root)])
        self.assertIn('暂停', str(caught.exception))
        start.assert_not_called()

    def test_killed_worker_gets_unconfirmed_status_from_independent_watch(self):
        control, result, _ = self.schedule()
        request = control / 'uninstall-request.json'
        attempt = package.read_json(result)['attempt_id']
        uninstall_recovery.watch(request, attempt, timeout=0)
        state = package.read_json(result)
        self.assertEqual(state['state'], 'unconfirmed')
        self.assertFalse(state['complete'])
        self.assertIsNone(state['program_removed'])

    def test_late_watch_cannot_overwrite_completed_result(self):
        control, result, _ = self.schedule()
        request = control / 'uninstall-request.json'
        attempt = package.read_json(result)['attempt_id']
        uninstall_recovery.status(request, attempt, 'complete', complete=True)
        uninstall_recovery.watch(request, attempt, timeout=0)
        self.assertTrue(package.read_json(result)['complete'])

    def test_old_watch_cannot_overwrite_new_attempt(self):
        control, result, _ = self.schedule()
        request = control / 'uninstall-request.json'
        old = package.read_json(result)['attempt_id']
        uninstall_recovery.status(request, 'b' * 32, 'pending')
        uninstall_recovery.watch(request, old, timeout=0)
        self.assertEqual(package.read_json(result)['state'], 'pending')

    def test_repeated_owned_entry_cleanup_accepts_entries_already_absent(self):
        instance = package.installation(self.root)['instance_id']
        shortcut = self.base / 'owned.lnk'
        arguments = '-X utf8 -B -m distribution.bootstrap run --root "' + str(self.root) + '"'
        target = str(self.root / 'versions/0.4.0-rc.3/runtime/pythonw.exe')
        snapshot = {'name': 'OpenGloss-' + instance, 'shortcut': shortcut,
                    'run_present': True, 'command': '"' + target + '" ' + arguments + ' --no-browser',
                    'shortcut_details': {'target': target, 'arguments': arguments,
                                         'working_directory': str(self.root)},
                    'shortcut_sha256': 'a' * 64}
        with patch.object(uninstall_helper, '_read_run', return_value=None), \
             patch.object(uninstall_helper, '_desktop_shortcut', return_value=shortcut):
            self.assertEqual(uninstall_helper.remove_owned_entries(self.root, instance, snapshot), [])

    def test_recover_entry_does_not_launch_engine_after_success(self):
        with patch.object(package, 'recover_pending', return_value=True) as recover, \
             patch.object(bootstrap.Supervisor, 'start') as start:
            bootstrap.main(['recover', '--root', str(self.root)])
        recover.assert_called_once_with(self.root)
        start.assert_not_called()

    def test_normal_restart_cannot_bypass_uncertain_update_recovery(self):
        package.atomic_json(self.root/'pending-update.json',{'synthetic':'unconfirmed'})
        supervisor=bootstrap.Supervisor(self.root)
        self.addCleanup(supervisor.stop)
        with patch.object(bootstrap.subprocess,'Popen',side_effect=AssertionError('engine must stay stopped')):
            with self.assertRaises(RuntimeError):supervisor.start()

    def test_uninstall_recovery_message_survives_manager_shutdown(self):
        script=(ROOT/'distribution/manage.js').read_text(encoding='utf-8')
        harness=r'''
const vm=require('vm'),fs=require('fs'),nodes=new Map(),events={};let tick,closing=false;
function node(id){if(!nodes.has(id))nodes.set(id,{classList:{toggle(){}},textContent:'',value:'',checked:false,dataset:{},addEventListener:(t,f)=>events[id+':'+t]=f});return nodes.get(id)}
const state={version:'synthetic',privacy:{},engine_url:'http://127.0.0.1:54935'};
vm.runInNewContext(fs.readFileSync(0,'utf8'),{URLSearchParams,location:{hash:'#token=synthetic',pathname:'/manage'},history:{replaceState(){}},document:{getElementById:node},confirm:()=>true,setInterval:f=>tick=f,fetch:async(url,init)=>{
if(url.endsWith('/uninstall')){closing=true;return {ok:true,json:async()=>({result_file:'synthetic-result',recovery_entry:'Continue-uninstall.cmd'})}};
if(closing)throw Error('manager is closed');return {ok:true,json:async()=>state};}});
(async()=>{await new Promise(setImmediate);await events['uninstall:click']();const message=node('operation-status').textContent;if(!message.includes('Continue-uninstall.cmd'))process.exit(2);await tick();if(node('operation-status').textContent!==message)process.exit(3)})().catch(()=>process.exit(4));
'''
        result=subprocess.run(['node','-e',harness],input=script,text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr or 'recovery message was overwritten')


if __name__ == '__main__': unittest.main()
