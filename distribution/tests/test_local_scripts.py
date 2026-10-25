from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from distribution import bootstrap,package

class LocalScriptContracts(unittest.TestCase):
    def test_existing_warming_manager_is_waited_for_without_new_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(package,'installation',return_value={'instance_id':'a'*32}),patch.object(bootstrap,'local_status',side_effect=[TimeoutError(),TimeoutError(),{'engine_running':True,'busy':False}]),patch.object(bootstrap.subprocess,'Popen') as launch,patch.object(bootstrap.time,'sleep'):
                self.assertTrue(bootstrap.start_instance(root))
            launch.assert_not_called()

    def test_foreign_manager_is_never_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bootstrap,'local_status',side_effect=RuntimeError('foreign')),patch.object(bootstrap.urllib.request,'build_opener') as opener:
                with self.assertRaises(RuntimeError):bootstrap.local_action(Path(temp),'stop')
            opener.assert_not_called()

    def test_stopped_instance_stop_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bootstrap,'local_status',return_value=None),patch.object(bootstrap.urllib.request,'build_opener') as opener:
                self.assertFalse(bootstrap.local_action(Path(temp),'stop'))
            opener.assert_not_called()

if __name__=='__main__':unittest.main()
