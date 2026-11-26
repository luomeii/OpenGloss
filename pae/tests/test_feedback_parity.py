"""Compare normal learning states/events with the untouched public baseline."""
import json
from pathlib import Path
import subprocess
import sys

RESEARCH=Path(__file__).resolve().parents[3]
RUNNER=r'''
import os,sys,json,sqlite3,threading,tempfile
from pathlib import Path
from unittest.mock import patch
for key in list(os.environ):
    if key.startswith('PAE_') or any(t in key.upper() for t in ('API_KEY','TOKEN','SECRET')):
        os.environ.pop(key,None)
sys.path.insert(0,sys.argv[1])
from pae_core import db
from pae_core.engine import Engine
from pae_core.state import DEFAULTS
art=Path(sys.argv[2]);art.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='parity-',dir=art) as tmp:
    e=Engine.__new__(Engine);e.db_path=str(Path(tmp)/'synthetic.sqlite')
    e.conn=db.get_db(e.db_path);db.init_schema(e.conn);e.lock=threading.Lock()
    e.states={};e.seen_idem=set();e.params=dict(DEFAULTS)
    e.translations={'synthetic':('合成释义',0),'exampleword':('例词',0)}
    e.lemma_map={};e.exchange_map={};e._fam_mode='off'
    e._params_ts=e._fam_ts=e._proactive_dispatch_ts=1900000000
    annotations=[]
    with patch('time.time',return_value=1700000000) as clock:
        annotations.append(e.annotate('synthetic exampleword','page-1','s0'))
        e.record_event('hover','synthetic',metas={'session_id':'s0'})
        e.record_event('known_click','synthetic')
        e.record_event('known_click','synthetic')
        annotations.append(e.annotate('synthetic exampleword','page-1','s0'))
        clock.return_value+=60
        annotations.append(e.annotate('synthetic exampleword','page-2','s1'))
        clock.return_value+=86400
        annotations.append(e.annotate('synthetic exampleword','page-3','s0'))
        e.record_event('hover','exampleword',metas={'session_id':'s0'})
        e.record_event('known_click','exampleword')
        result={'annotations':annotations,
            'states':{k[0]:dict(vars(v),S=v.s_value(e.params)) for k,v in sorted(e.states.items())},
            'events':[{'ts':r['ts'],'type':r['type'],'payload':json.loads(r['payload']),'idem_key':r['idem_key']}
                for r in e.conn.execute('SELECT * FROM events ORDER BY rowid')],
            'seen_idem':sorted(e.seen_idem)}
    e.conn.close()
print(json.dumps(result,ensure_ascii=False))
'''

def run():
    outputs=[]
    for source in (RESEARCH/'repository'/'pae',Path(__file__).resolve().parents[1]):
        p=subprocess.run([sys.executable,'-X','utf8','-I','-B','-c',RUNNER,str(source),
            str(RESEARCH/'test-artifacts'/'feedback')],capture_output=True,text=True,encoding='utf-8',timeout=20)
        if p.returncode:raise AssertionError(p.stderr)
        outputs.append(json.loads(p.stdout))
    assert outputs[0]==outputs[1], 'normal learning behavior diverged from baseline'
    print(json.dumps({'normal_learning_parity':True,'annotations':len(outputs[0]['annotations']),
                     'events':len(outputs[0]['events']),'states':len(outputs[0]['states'])}))

if __name__=='__main__':run()
