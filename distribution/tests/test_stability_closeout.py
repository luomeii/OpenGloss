"""Only the three confirmed rc.3 management/launcher regressions."""
from pathlib import Path
import io,subprocess,sys,tempfile,unittest,urllib.error
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(Path(__file__).parent)]
from distribution import bootstrap,package
import test_package as fixtures

class StabilityCloseout(unittest.TestCase):
    def setUp(self):
        fixtures.DistributionContracts.setUp(self)
        self.source=fixtures.DistributionContracts.fixture(self)
        self.root=self.base/'installed';package.install(self.source,self.root,engine_port=54935)

    def available_history(self):
        newer=fixtures.DistributionContracts.fixture(self,'0.4.0')
        package.upgrade(self.root,newer,lambda version,data:True)

    def test_stop_waits_for_busy_owner_then_confirms_ports_free(self):
        errors=[urllib.error.HTTPError('',409,'busy',{},io.BytesIO(b'{}')) for _ in range(2)]
        with patch.object(bootstrap,'local_action',side_effect=[*errors,True]) as action,patch.object(bootstrap.time,'sleep'),patch.object(bootstrap.socket,'socket') as socket:
            socket.return_value.__enter__.return_value.connect_ex.return_value=10061
            self.assertTrue(bootstrap.stop_instance(self.root))
        self.assertEqual(action.call_count,3)
        self.assertTrue(all(error.closed for error in errors))

    def test_stop_does_not_retry_a_non_busy_denial(self):
        error=urllib.error.HTTPError('',403,'denied',{},io.BytesIO(b'{}'))
        with patch.object(bootstrap,'local_action',side_effect=error) as action,patch.object(bootstrap.time,'sleep') as sleep:
            with self.assertRaises(urllib.error.HTTPError):bootstrap.stop_instance(self.root)
        self.assertEqual(action.call_count,1);sleep.assert_not_called();self.assertTrue(error.closed)

    def test_busy_stop_has_a_deadline_instead_of_killing_any_process(self):
        error=urllib.error.HTTPError('',409,'busy',{},io.BytesIO(b'{}'))
        with patch.object(bootstrap,'local_action',side_effect=error),patch.object(bootstrap.time,'monotonic',side_effect=[0,0,41]),patch.object(bootstrap.time,'sleep'),patch.object(bootstrap.subprocess,'Popen') as launch:
            with self.assertRaises(RuntimeError):bootstrap.stop_instance(self.root)
        launch.assert_not_called()

    def test_start_waits_for_recovery_even_while_old_failed_stage_is_visible(self):
        states=[{'stage':'failed','engine_running':False,'busy':False},
                {'stage':'failed','engine_running':False,'busy':True},
                {'stage':'ready','engine_running':True,'busy':False}]
        with patch.object(bootstrap,'local_status',side_effect=states),patch.object(bootstrap,'local_action') as action,patch.object(bootstrap.time,'sleep'):
            self.assertTrue(bootstrap.start_instance(self.root))
        action.assert_called_once_with(self.root,'restart')

    def test_start_still_reports_a_finished_failed_recovery(self):
        state={'stage':'failed','engine_running':False,'busy':False}
        with patch.object(bootstrap,'local_status',return_value=state),patch.object(bootstrap,'local_action'),patch.object(bootstrap.time,'sleep'):
            with self.assertRaises(RuntimeError):bootstrap.start_instance(self.root)

    def assert_rollback_refused_without_engine_effects(self):
        supervisor=bootstrap.Supervisor(self.root)
        with patch.object(supervisor,'stop') as stop,patch.object(supervisor,'start') as start:
            with self.assertRaises((ValueError,OSError,TypeError)):supervisor.action('rollback')
        stop.assert_not_called();start.assert_not_called()

    def test_no_history_rollback_does_not_stop_current_engine(self):
        self.assert_rollback_refused_without_engine_effects()

    def test_missing_snapshot_rollback_does_not_stop_current_engine(self):
        self.available_history();pointer=package.read_json(self.root/'active.json')
        (self.root/pointer['rollback_backup']/'snapshot.json').unlink()
        self.assert_rollback_refused_without_engine_effects()

    def test_invalid_previous_program_does_not_stop_current_engine(self):
        self.available_history()
        (self.root/'versions/0.4.0-rc.1/runtime/python.exe').write_bytes(b'synthetic tamper')
        self.assert_rollback_refused_without_engine_effects()

    def test_status_disables_rollback_when_there_is_no_history(self):
        with patch.object(bootstrap,'autostart_enabled',return_value=False):
            self.assertIs(bootstrap.Supervisor(self.root).status().get('can_rollback'),False)

    def test_status_checks_availability_without_hashing_runtime_on_every_poll(self):
        self.available_history()
        with patch.object(bootstrap,'autostart_enabled',return_value=False),patch.object(package,'verify_directory',side_effect=AssertionError('status must remain a light read')):
            self.assertIs(bootstrap.Supervisor(self.root).status().get('can_rollback'),True)

    def test_frontend_enables_rollback_only_with_available_history(self):
        harness=r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert'),nodes=new Map();let poll;
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',checked:false,disabled:false,textContent:'',dataset:{},classList:{toggle(){}},addEventListener(){}});return nodes.get(id)}
const state={version:'synthetic',message:'ready',engine_url:'http://127.0.0.1:54935',engine_running:true,busy:false,privacy:{},can_rollback:false};
vm.runInNewContext(fs.readFileSync(0,'utf8'),{document:{getElementById:node},fetch:async()=>({ok:true,json:async()=>state}),setInterval:f=>poll=f});
(async()=>{await new Promise(setImmediate);assert.equal(node('rollback').disabled,true);state.can_rollback=true;await poll();assert.equal(node('rollback').disabled,false);state.busy=true;await poll();assert.equal(node('rollback').disabled,true)})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',harness],input=(ROOT/'distribution/manage.js').read_text(encoding='utf-8'),text=True,capture_output=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
