"""Portable, offline security regression contracts. Run this file explicitly.
All data is synthetic; no socket, real dictionary, browser or model is used.
"""
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_SANDBOX = tempfile.TemporaryDirectory(prefix='pae-security-')
_ROOT = Path(_SANDBOX.name)
os.environ.update(PAE_CONFIG_PATH=str(_ROOT/'config.json'), PAE_DB_PATH=str(_ROOT/'unused.db'),
                  PAE_KEYS_PATH=str(_ROOT/'keys.json'), PAE_HTTP_TOKEN='synthetic-'+'x'*40,
                  PAE_ENGINE_PORT='4815')
(_ROOT/'config.json').write_text('{}', encoding='utf-8')
from pae_core import capsurface as cs, engine as em, sensejudge as sj, observe, memory
from pae_core import api

async def request(path, method='GET', token=True, host='127.0.0.1:4815', origin=None, body=None, extra=()):
    headers=[(b'host',host.encode()),(b'content-type',b'application/json')]
    if token: headers.append((b'authorization',('Bearer '+os.environ['PAE_HTTP_TOKEN']).encode()))
    if origin is not None: headers.append((b'origin',origin.encode()))
    headers.extend(extra)
    raw=json.dumps(body or {}).encode()
    scope=dict(type='http', asgi={'version':'3.0','spec_version':'2.4'}, http_version='1.1',
               method=method, scheme='http', path=path, raw_path=path.encode(), query_string=b'',
               headers=headers, server=('127.0.0.1',4815), client=('127.0.0.1',12345))
    messages=[]
    first=True
    async def receive():
        nonlocal first
        if first:
            first=False
            return {'type':'http.request','body':raw,'more_body':False}
        await asyncio.Event().wait()
    async def send(msg): messages.append(msg)
    await api.app(scope,receive,send)
    status=next(m['status'] for m in messages if m['type']=='http.response.start')
    data=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
    try: data=json.loads(data)
    except (ValueError,TypeError): data=data.decode()
    return status,data

class Contracts(unittest.TestCase):
    def setUp(self):
        self.runner=asyncio.Runner(); self.runner.get_loop()
        self.tmp=tempfile.TemporaryDirectory(dir=_ROOT)
        self.root=Path(self.tmp.name)
        self.cfg=_ROOT/'config.json'
        self.config({})
        self.patches=[patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')),
                      patch.object(em,'load_exchange_map',return_value={}),
                      patch.object(em,'load_full_dict',return_value={'vessel':('boat',0),'planet':('world',0)}),
                      patch.object(em,'load_lemma_map',return_value={}),
                      patch.object(em,'import_lemma_as_dict',return_value=0),
                      patch('pae_core.push.maybe_proactive',return_value=None),
                      patch('pae_core.llm.chat',side_effect=RuntimeError('fake failure'))]
        for p in self.patches: p.start()
        self.eng=em.Engine(self.root/'db.sqlite', self.root/'lemma.txt')
        self.getter=patch.object(em,'get_engine',return_value=self.eng); self.getter.start()
        self.api_getter=patch.object(api,'get_engine',return_value=self.eng); self.api_getter.start()
        (_ROOT/'keys.json').write_text(json.dumps({'actors':{
            'ext:read':{'key_sha256':cs.keys_mod.sha('read'),'scopes':['read']},
            'ext:write':{'key_sha256':cs.keys_mod.sha('write'),'scopes':['read','write']}}}),encoding='utf-8')
    def tearDown(self):
        self.api_getter.stop(); self.getter.stop()
        self.eng.conn.close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup(); self.runner.close()
    def config(self, value): self.cfg.write_text(json.dumps(value),encoding='utf-8')
    def http(self,*args,**kwargs): return self.runner.run(request(*args,**kwargs))
    def call(self,name,params=None,key='write',actor_hint=None):
        return cs.handle(name,params or {},key,actor_hint=actor_hint)
    def test_external_cannot_forge_user_to_forget(self):
        ok,f=memory.remember(self.eng.conn,'user','preference','synthetic protected',[1])
        self.assertTrue(ok)
        for key,hint in [('write',None),('', 'companion')]:
            s,b=self.call('pae.forget',{'actor':'user','fact_id':f['fact_id']},key,hint)
            self.assertNotEqual(s,200)
        self.assertIsNone(self.eng.conn.execute('SELECT superseded_by FROM facts').fetchone()[0])
    def test_external_cannot_permanently_relax(self):
        for key,hint in [('write',None),('', 'companion')]:
            s,b=self.call('pae.propose',{'actor':'user','patch':[{'key':'daily_say_cap','to':50}],'rationale':'synthetic','evidence':[1]},key,hint)
            self.assertNotEqual(s,200)
    def test_external_read_cannot_annotate(self):
        self.assertEqual(self.call('pae.annotate',{'text':'vessel','page_id':'synthetic'},'read')[0],403)
    def test_real_local_can_forget(self):
        ok,f=memory.remember(self.eng.conn,'user','preference','synthetic',[1])
        self.assertEqual(self.call('pae.forget',{'fact_id':f['fact_id']},'', 'local')[0],200)
    def test_query_actor_filter_survives(self):
        cs.audit('synthetic:one','pae.status',True)
        cs.audit('synthetic:two','pae.status',True)
        s,b=self.call('pae.audit',{'actor':'synthetic:one'},'read')
        self.assertEqual(s,200); self.assertTrue(b['result'])
        self.assertTrue(all(x['actor']=='synthetic:one' for x in b['result']))
    def test_config_both_surfaces_filter_nested_secrets(self):
        self.config({'llm':{'api_key':'MODEL_SECRET','items':[{'Authorization':'AUTH_SECRET','safe':7}]},'budget':8,'password':'PW'})
        for value in [self.http('/v1/config')[1], self.call('pae.config',key='read')[1]]:
            self.assertNotIn('MODEL_SECRET',json.dumps(value)); self.assertNotIn('AUTH_SECRET',json.dumps(value)); self.assertNotIn('PW',json.dumps(value))
            self.assertIn('budget',str(value))
    def test_http_requires_bearer(self):
        for path in ['/v1/config','/v1/status','/v1/cap/manifest','/v1/ext/stream']:
            self.assertEqual(self.http(path,token=False)[0],401)
    def test_host_origin_exact_validation(self):
        for host in ['localhost:4815','127.0.0.1:9','127.0.0.1:4815.attacker.invalid']:
            self.assertEqual(self.http('/v1/config',host=host)[0],403)
        for origin in ['https://attacker.invalid','null','chrome-extension://bad','chrome-extension://'+'a'*32+'.evil']:
            self.assertEqual(self.http('/v1/config',origin=origin)[0],403)
        for origin in [None,'http://127.0.0.1:4815','chrome-extension://'+'a'*32]:
            self.assertEqual(self.http('/v1/config',origin=origin)[0],200)
    def test_health_redacts_without_bearer(self):
        s,b=self.http('/v1/health',token=False)
        self.assertEqual(s,200); self.assertEqual(b,{'ok':True})
        self.assertIn('db',self.http('/v1/health')[1])
    def test_docs_disabled(self):
        for path in ['/docs','/redoc','/openapi.json']: self.assertEqual(self.http(path)[0],404)
    def test_get_rejects_all_effectful_caps_before_dispatch(self):
        for name in ['pae.persona.say','pae.agent.tick','pae.annotate','pae.familiarity','pae.outcomes','pae.params','pae.context']:
            with patch.object(cs,'_dispatch',side_effect=AssertionError('must not dispatch')):
                self.assertEqual(self.http('/v1/cap/'+name)[0],405)
    def test_http_external_scope_not_promoted_by_bearer(self):
        self.assertEqual(self.http('/v1/cap/pae.forget',method='POST',extra=[(b'x-pae-key',b'read')])[0],403)
    def test_token_created_once_and_existing_invalid_not_replaced(self):
        from pae_core import http_security as hs
        with patch.dict(os.environ,{},clear=False):
            old=os.environ.pop('PAE_HTTP_TOKEN')
            try:
                with patch.object(hs,'config_path',return_value=self.root/'config.json'):
                    t=hs.engine_token(); self.assertGreaterEqual(len(t),32)
                    self.assertEqual(t,hs.engine_token())
                    (self.root/'http-auth.json').write_text('{"token":"bad"}')
                    with self.assertRaises(ValueError): hs.engine_token()
                    self.assertEqual(json.loads((self.root/'http-auth.json').read_text())['token'],'bad')
            finally: os.environ['PAE_HTTP_TOKEN']=old
    def test_privacy_storage_modes_keep_pages_distinct_and_idempotent(self):
        for mode in ['full','host_only','hash','off']:
            self.config({'privacy':{'url_storage':mode,'context_storage':False}})
            for suffix in ['one','two']:
                url='https://synthetic.invalid/'+mode+'/'+suffix+'?secret=VALUE'
                self.eng.annotate('vessel planet',url)
                self.eng.annotate('vessel planet',url)
            rows=self.eng.conn.execute("SELECT payload FROM events WHERE type='encounter'").fetchall()[-4:]
            self.assertEqual(len(rows),4)
            payloads=[json.loads(r[0]) for r in rows]
            self.assertEqual(len(set(p['page_key'] for p in payloads)),2)
            for p in payloads:
                self.assertNotIn('sentence_context',p)
                if mode!='full': self.assertNotIn('VALUE',p['page_id'])
                if mode=='off': self.assertEqual(p['page_id'],'')
    def test_event_body_privacy_and_default_shadow_off(self):
        self.config({'privacy':{'url_storage':'host_only','context_storage':False}})
        with patch.object(self.eng,'_shadow_judge') as fake:
            self.eng.record_event('hover','vessel',metas={'page_id':'https://synthetic.invalid/private','sentence':'private body'})
            time.sleep(.03); self.assertFalse(fake.called)
        payload=json.loads(self.eng.conn.execute('SELECT payload FROM events').fetchone()[0])
        self.assertEqual(payload['page_id'],'https://synthetic.invalid'); self.assertNotIn('sentence',payload)
    def test_old_and_nested_url_outputs_redacted_by_default(self):
        self.eng.record_event('annotation_shown','vessel',metas={'page_id':'https://synthetic.invalid/private?q=VALUE'})
        for value in [observe.page(self.eng,{}),observe.events(self.eng,{}),observe.assemble(self.eng,{'include':['page']})]:
            self.assertNotIn('VALUE',json.dumps(value)); self.assertNotIn('/private',json.dumps(value))
    def test_legacy_new_page_grouping_consistent(self):
        from pae_core import privacy
        url='https://synthetic.invalid/same'
        self.assertEqual(privacy.page_identity({'page_id':url}),privacy.page_identity({'page_key':privacy.page_key(url),'page_id':''}))
    def test_persona_failed_attempts_are_capped_persistently(self):
        for _ in range(120): self.call('pae.persona.say',{'stimulus':'synthetic','context_block':'synthetic'})
        with patch('pae_core.llm.chat') as fake:
            s,b=self.call('pae.persona.say',{'stimulus':'synthetic','context_block':'synthetic'})
            self.assertEqual(s,429); self.assertFalse(fake.called)
    def test_atomic_quota_concurrent_and_restart(self):
        from pae_core import quota
        def reserve(_): return quota.reserve(self.eng.conn,'synthetic',7)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            accepted=list(pool.map(reserve,range(30)))
        self.assertEqual(sum(x is not None for x in accepted),7)
        from pae_core.db import get_db
        c=get_db(self.eng.db_path)
        try: self.assertIsNone(quota.reserve(c,'synthetic',7))
        finally: c.close()
    def test_sensejudge_failed_attempt_limit_and_free_prefilter(self):
        with patch.object(sj,'full_candidates',return_value=['one']), patch.object(sj,'DAILY_CALL_CAP',3):
            for _ in range(5): sj.judge(self.eng.conn,'unused','vessel','vessel synthetic',llm_chat=lambda *_: self.fail('free prefilter called model'))
        hits=[]
        def fail(*_): hits.append(1); raise RuntimeError('synthetic')
        with patch.object(sj,'full_candidates',return_value=['one','two']), patch.object(sj,'DAILY_CALL_CAP',3):
            for i in range(8): out=sj.judge(self.eng.conn,'unused','vessel','vessel synthetic '+str(i),llm_chat=fail)
        self.assertEqual(len(hits),3); self.assertEqual(out['reason'],'daily_cap')
        self.assertEqual(sj.stats(self.eng.conn)['calls_today'],0)
    def test_sensejudge_model_outside_connection_lock(self):
        def model(*_):
            done=[]
            def read():
                with self.eng.conn.access_lock: done.append(1)
            t=threading.Thread(target=read);t.start();t.join(1)
            self.assertTrue(done);return '{"sense_index":0,"evidence_span":"vessel"}'
        with patch.object(sj,'full_candidates',return_value=['one','two']):
            sj.judge(self.eng.conn,'unused','vessel','vessel synthetic',llm_chat=model)

    def test_success_audits_do_not_double_charge_reservations(self):
        from pae_core import quota
        for _ in range(5):
            self.assertIsNotNone(quota.reserve(self.eng.conn,'pae.persona.say',5))
            cs.audit('local','pae.persona.say',True)
        self.assertIsNone(quota.reserve(self.eng.conn,'pae.persona.say',5))
    def test_legacy_successes_count_before_first_reservation(self):
        from pae_core import quota
        for _ in range(3): cs.audit('local','pae.persona.say',True)
        self.assertIsNotNone(quota.reserve(self.eng.conn,'pae.persona.say',4))
        self.assertIsNone(quota.reserve(self.eng.conn,'pae.persona.say',4))
    def test_quota_across_independent_connections(self):
        from pae_core import quota
        from pae_core.db import get_db
        def reserve(_):
            c=get_db(self.eng.db_path)
            try:return quota.reserve(c,'synthetic-shared',4)
            finally:c.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(x is not None for x in pool.map(reserve,range(20))),4)
    def test_persona_model_outside_connection_lock(self):
        def model(*args,**kwargs):
            done=[]
            def read():
                with self.eng.conn.access_lock: done.append(1)
            t=threading.Thread(target=read);t.start();t.join(1)
            self.assertTrue(done)
            return {'content':'synthetic'}
        with patch('pae_core.llm.chat',side_effect=model):
            self.assertEqual(self.call('pae.persona.say',{'context_block':'synthetic'})[0],200)
    def test_real_local_can_make_permanent_proposal(self):
        self.assertEqual(self.call('pae.propose',{'patch':[{'key':'daily_say_cap','to':50}],'rationale':'synthetic'},'', 'local')[0],200)
    def test_chat_intent_does_not_create_protected_user_fact_for_external(self):
        with patch('pae_core.llm.chat',return_value={'content':'synthetic answer'}):
            s,b=self.call('pae.persona.say',{'stimulus':'记住我喜欢合成测试','context_block':'synthetic','actor':'user'})
        self.assertEqual(s,200)
        rows=self.eng.conn.execute('SELECT source_actor FROM facts').fetchall()
        self.assertTrue(rows);self.assertTrue(all(r[0]=='ext:write' for r in rows))
    def test_shadow_explicit_opt_in_reaches_model_judge(self):
        self.config({'privacy':{'shadow_model':True}})
        hit=threading.Event()
        with patch.object(self.eng,'_shadow_judge',side_effect=lambda *_:hit.set()):
            self.eng.record_event('hover','vessel',metas={'sentence':'vessel synthetic'})
            self.assertTrue(hit.wait(1))
    def test_health_failure_has_only_public_code(self):
        with patch.object(self.eng,'check_health',side_effect=RuntimeError(str(_ROOT/'private'/'MODEL_SECRET'))):
            s,b=self.http('/v1/health',token=False)
            self.assertEqual(s,503);self.assertEqual(b,{'ok':False,'error':'engine_unavailable'})
    def test_direct_persona_http_uses_same_reservation_cap(self):
        from pae_core import quota
        for _ in range(120): self.assertIsNotNone(quota.reserve(self.eng.conn,'pae.persona.say',120))
        with patch('pae_core.llm.chat') as fake:
            self.assertEqual(self.http('/v1/persona/say',method='POST',body={'stimulus':'synthetic'})[0],429)
            self.assertFalse(fake.called)
    def test_sse_query_key_cannot_authenticate(self):
        self.assertEqual(self.http('/v1/ext/stream',token=False)[0],401)
        import inspect
        self.assertNotIn('key',inspect.signature(api.ext_stream).parameters)
    def test_manifest_reports_effects_and_http_methods(self):
        for cap in cs.load_manifest()['capabilities']:
            expected=['GET','POST'] if cap['name'] in cs.HTTP_READ_CAPS else ['POST']
            self.assertEqual(cap.get('capability_http_methods'),expected)
            if cap['name']=='pae.annotate':self.assertEqual(cap['scope'],'write')
    def test_quota_statistics_disclose_attempts_without_claiming_spend(self):
        with patch.object(sj,'full_candidates',return_value=['one','two']):
            sj.judge(self.eng.conn,'unused','vessel','vessel synthetic',llm_chat=lambda *_: (_ for _ in ()).throw(RuntimeError('synthetic')))
        stats=sj.stats(self.eng.conn)
        self.assertEqual(stats['calls_today'],0)
        self.assertEqual(stats['attempts_today'],1)
        self.assertEqual(stats['attempts_remaining'],149)

    def test_read_context_params_outcomes_familiarity_are_pure(self):
        self.eng.conn.execute("INSERT INTO params VALUES('daily_say_cap','2',0,'synthetic',NULL,1)")
        self.eng.conn.commit()
        trace=[];self.eng.conn.set_trace_callback(trace.append)
        for cap in ['pae.context','pae.params','pae.outcomes','pae.familiarity']:
            s,b=self.call(cap,key='read')
            self.assertEqual(s,200,(cap,b))
        self.eng.conn.set_trace_callback(None)
        writes=[q for q in trace if q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','REPLACE')) and 'cap_calls' not in q]
        self.assertEqual(writes,[])
        self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM params').fetchone()[0],1)
    def test_read_key_cannot_explicitly_refresh_derived_views(self):
        self.assertEqual(self.call('pae.familiarity',{'recompute':True},'read')[0],403)
        self.assertEqual(self.call('pae.outcomes',{'evaluate_decisions':True},'read')[0],403)

    def test_familiarity_read_projection_with_real_synthetic_rows(self):
        self.eng.record_event('annotation_shown','vessel',metas={'page_id':'https://synthetic.invalid/one','page_active_ms':2000})
        s,b=self.call('pae.familiarity',key='read')
        self.assertEqual(s,200,b)
        self.assertEqual(b['result']['near_miss'][0]['q_exp'],1)
        self.assertEqual(b['result']['near_miss'][0]['days'],1)
    def test_admin_authority_is_preserved(self):
        path=_ROOT/'keys.json';data=json.loads(path.read_text())
        data['actors']['ext:admin']={'key_sha256':cs.keys_mod.sha('admin'),'scopes':['read','write','admin']}
        path.write_text(json.dumps(data))
        self.assertEqual(self.call('pae.key.issue',{'name':'ext:synthetic'},key='write')[0],500)
        self.assertEqual(self.call('pae.key.issue',{'name':'ext:synthetic'},key='admin')[0],200)

    def test_read_usage_includes_unmigrated_legacy_successes(self):
        from pae_core import quota
        cs.audit('local','pae.persona.say',True)
        self.assertEqual(quota.usage(self.eng.conn,'pae.persona.say')['attempts_today'],1)
    def test_handle_none_parameters_keeps_internal_compatibility(self):
        self.assertEqual(cs.handle('pae.outcomes',None,actor_hint='companion')[0],200)
    def test_mixed_old_new_page_identity_drives_real_grouping(self):
        from pae_core.db import append_event
        from pae_core import privacy,familiarity,metrics
        url='https://synthetic.invalid/mixed'
        append_event(self.eng.conn,'annotation_shown',{'lemma':'vessel','page_id':url,'page_active_ms':2000})
        append_event(self.eng.conn,'annotation_shown',{'lemma':'vessel','page_id':'','page_key':privacy.page_key(url),'page_active_ms':2000})
        self.assertEqual(familiarity._exposure_stats(self.eng.conn)['vessel']['q_exp'],1)
        self.assertEqual(metrics.compute(self.eng.conn)['avg_annotations_per_page'],2)
        self.assertEqual(metrics._outcome_inputs(self.eng.conn)[-1],1)

class TLSContracts(unittest.TestCase):
    def test_model_uses_verified_certifi_context(self):
        import ssl
        from pae_core import llm
        response=unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value=b'{"choices":[{"message":{"content":"synthetic"}}]}'
        with patch.object(llm,'load_provider',return_value={'base':'https://synthetic.invalid','key':'FAKE','model':'synthetic'}), patch.object(llm.urllib.request,'urlopen',return_value=response) as opening:
            self.assertEqual(llm.chat([])['content'],'synthetic')
            ctx=opening.call_args.kwargs.get('context')
            self.assertIsInstance(ctx,ssl.SSLContext)
            self.assertTrue(ctx.check_hostname);self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
    def test_model_error_hides_provider_secrets(self):
        from pae_core import llm
        with patch.object(llm,'load_provider',return_value={'base':'https://synthetic.invalid/private','key':'FAKE_SECRET','model':'synthetic'}), patch.object(llm.urllib.request,'urlopen',side_effect=OSError('FAKE_SECRET https://synthetic.invalid/private '+str(_ROOT/'private'))):
            with self.assertRaises(RuntimeError) as caught: llm.chat([])
            self.assertNotIn('FAKE_SECRET',str(caught.exception));self.assertNotIn('synthetic.invalid',str(caught.exception))

    def test_malformed_provider_error_hides_full_value(self):
        from pae_core import llm
        with patch.object(llm,'load_provider',return_value={'base':'FAKE_SECRET_PRIVATE_BASE','key':'FAKE','model':'synthetic'}):
            with self.assertRaises(RuntimeError) as caught:llm.chat([])
            self.assertNotIn('FAKE_SECRET',str(caught.exception))

if __name__=='__main__': unittest.main(verbosity=2)

