from pathlib import Path
import os,sys,tempfile,json,unittest,subprocess
ROOT=Path(__file__).resolve().parents[2]
class ConfigContract(unittest.TestCase):
 def test_one_explicit_config_is_used_by_all_consumers(self):
  with tempfile.TemporaryDirectory(prefix='config-contract-',dir=None) as temp:
   path=Path(temp)/'config.json';path.write_text(json.dumps({'budget':3,'annotate_max_s':.8,'facts_max_active':7,'proactive_llm':False,'context_max_tokens':12345,'llm':{'model':'synthetic-model'},'agent_schedule':{'enabled':False}}))
   code="import sys;sys.path.insert(0,sys.argv[1]);from pae_core import paths,api,capsurface,llm,engine,memory,params,scheduler,push,observe;from pathlib import Path;p=Path(sys.argv[2]);assert paths.config_path()==p;assert api.CONFIG_PATH==p;assert capsurface.CONFIG_PATH==p;assert llm.CONFIG==p;assert scheduler.CONFIG==p;assert engine.BUDGET==3;assert params.ENGINE_DEFAULTS['annotate_max_s']==.8;assert memory._cfg('facts_max_active',0)==7;assert llm.load_provider()['model']=='synthetic-model';assert push._llm_enabled() is False;assert observe._config()['context_max_tokens']==12345"
   env={k:v for k,v in os.environ.items() if not k.startswith('PAE_')};env['PAE_CONFIG_PATH']=str(path)
   result=subprocess.run([sys.executable,'-I','-B','-c',code,str(ROOT/'pae'),str(path)],env=env,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main(verbosity=2)
