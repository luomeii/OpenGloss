"""Explicit offline review regressions; run only in a temporary source copy with -B."""
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import unittest
import urllib.error
from unittest.mock import patch
sys.dont_write_bytecode=True
import test_security_contracts as base

class ReviewContracts(unittest.TestCase):
    setUp=base.Contracts.setUp
    tearDown=base.Contracts.tearDown
    config=base.Contracts.config
    http=base.Contracts.http
    call=base.Contracts.call
    def scoped(self,path,credential='write',method='POST',body=None,xkey=None):
        headers=[(b'authorization',('Bearer '+credential).encode())]
        if xkey is not None: headers.append((b'x-pae-key',xkey.encode()))
        return self.http(path,method=method,token=False,body=body,extra=headers)
    def test_read_bearer_is_read_and_cannot_mutate(self):
        self.assertEqual(self.scoped('/v1/cap/pae.status','read','GET')[0],200)
        for name,body in [('pae.key.issue',{'name':'ext:new'}),('pae.annotate',{'text':'vessel'}),('pae.event',{'type':'hover','lemma':'vessel'})]:
            self.assertEqual(self.scoped('/v1/cap/'+name,'read',body=body)[0],403)
    def test_scoped_bearer_xkey_removal_and_replacement_never_elevate(self):
        for xkey in [None,'read','write','unknown','']:
            status,_=self.scoped('/v1/cap/pae.key.issue','read',xkey=xkey)
            self.assertIn(status,(401,403))
        self.assertEqual(self.scoped('/v1/cap/pae.status','read','GET',xkey='read')[0],200)
    def test_scoped_write_cannot_forge_user(self):
        ok,f=base.memory.remember(self.eng.conn,'user','preference','synthetic',[1]);self.assertTrue(ok)
        self.assertNotEqual(self.scoped('/v1/cap/pae.forget',body={'actor':'user','fact_id':f['fact_id']})[0],200)
        self.assertEqual(self.scoped('/v1/cap/pae.status',method='GET')[1]['actor'],'ext:write')
        self.assertIsNone(self.eng.conn.execute('SELECT superseded_by FROM facts').fetchone()[0])
    def test_direct_routes_deny_scoped_bearer(self):
        for path,body in [('/v1/event',{'type':'known_click','lemma':'vessel'}),('/v1/annotate',{'text':'vessel'}),('/v1/persona/say',{'stimulus':'synthetic'})]:
            self.assertEqual(self.scoped(path,body=body)[0],403)
    def test_root_pairing_and_legacy_narrowing(self):
        s,b=self.http('/v1/cap/pae.key.issue',method='POST',body={'name':'ext:synthetic'})
        self.assertEqual(s,200);self.assertEqual(b['actor'],'local')
        self.assertEqual(self.http('/v1/cap/pae.key.issue',method='POST',extra=[(b'x-pae-key',b'read')])[0],403)
    def test_invalid_duplicate_headers_fail_closed(self):
        for extra in [[(b'authorization',b'Bearer read')],[(b'x-pae-key',b'read'),(b'x-pae-key',b'write')],[(b'x-pae-key',b'')]]:
            self.assertIn(self.http('/v1/cap/pae.status',extra=extra)[0],(401,403))
        for value in ['unknown','', 'Bearer read']:
            self.assertEqual(self.scoped('/v1/cap/manifest',value,'GET')[0],401)
    def test_health_scoped_and_public_do_not_expose_identity(self):
        self.assertEqual(self.scoped('/v1/health','read','GET')[1],{'ok':True})
        self.assertEqual(self.http('/v1/health',token=False)[1],{'ok':True})
        self.assertIn('db',self.http('/v1/health')[1])
    def test_scoped_sse_and_manifest_require_read(self):
        self.assertEqual(self.scoped('/v1/cap/manifest','read','GET')[0],200)
        self.assertEqual(self.scoped('/v1/ext/stream','unknown','GET')[0],401)
        key=base.cs.keys_mod.issue('ext:writeonly',['write'])
        self.assertEqual(self.scoped('/v1/ext/stream',key,'GET')[0],403)
        self.assertEqual(self.scoped('/v1/cap/manifest',key,'GET')[0],403)
    def test_generic_mastery_events_reject_external_and_companion(self):
        for key,hint in [('write',None),('', 'companion')]:
            for typ in ['user_pin','user_mastered','known_click']:
                s,b=self.call('pae.event',{'type':typ,'lemma':'vessel','actor':'user'},key,hint)
                self.assertEqual(s,403,(typ,b))
        self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
    def test_generic_passive_event_persists_authenticated_source(self):
        for i,(key,hint,actor) in enumerate([('write',None,'ext:write'),('','companion','companion')]):
            for typ in ['annotation_shown','hover']:
                s,b=self.call('pae.event',{'type':typ,'lemma':typ+str(i),'page_id':str(i),'actor':'user'},key,hint)
                self.assertEqual(s,200)
                self.assertEqual(self.eng.conn.execute('SELECT source FROM events ORDER BY rowid DESC LIMIT 1').fetchone()[0],actor)
    def test_true_local_mastery_and_hover_keep_idempotence(self):
        for typ in ['known_click','hover','user_pin','user_mastered']:
            params={'type':typ,'lemma':typ,'page_id':typ}
            s,b=self.http('/v1/cap/pae.event',method='POST',body=params)
            self.assertEqual(s,200);self.assertTrue(b['result']['counted'])
            self.assertFalse(self.http('/v1/cap/pae.event',method='POST',body=params)[1]['result']['counted'])
        self.assertEqual({r[0] for r in self.eng.conn.execute('SELECT source FROM events')},{'local'})
    def test_pin_quota_cannot_be_bypassed_through_generic_event(self):
        for key,hint,actor,prefix in [('write',None,'ext:write','external'),('','companion','companion','companion')]:
            for i in range(20):
                self.assertEqual(self.call('pae.pin',{'lemma':prefix+str(i)},key,hint)[0],200)
            self.assertNotEqual(self.call('pae.pin',{'lemma':prefix+'20'},key,hint)[0],200)
            for typ in ['user_pin','user_mastered','known_click']:
                self.assertEqual(self.call('pae.event',{'type':typ,'lemma':prefix+'20','actor':'user'},key,hint)[0],403)
            self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM events WHERE source=?',(actor,)).fetchone()[0],20)
    def test_direct_http_true_extension_keeps_known_click(self):
        s,b=self.http('/v1/event',method='POST',body={'type':'known_click','lemma':'vessel'})
        self.assertEqual(s,200);self.assertTrue(b['counted'])
        self.assertEqual(self.eng.conn.execute('SELECT source FROM events').fetchone()[0],'local')
    def test_config_urls_cannot_echo_embedded_credentials(self):
        self.config({'llm':{'base':'https://URL_USER:URL_PASSWORD@synthetic.invalid/v1?api_key=URL_SECRET#URL_FRAGMENT','safe_base':'https://synthetic.invalid/v1','nested':['https://U:URL_PASSWORD@synthetic.invalid/p?x=URL_SECRET']}})
        for result in [self.http('/v1/config')[1],self.call('pae.config',key='read')[1]]:
            text=json.dumps(result)
            for secret in ['URL_USER','URL_PASSWORD','URL_SECRET','URL_FRAGMENT']:self.assertNotIn(secret,text)
            self.assertIn('https://synthetic.invalid/v1',text)

    def test_reserved_actor_names_cannot_be_issued(self):
        for actor in ['local','user','companion']:
            with self.assertRaises(ValueError):base.cs.keys_mod.issue(actor,['read','write','admin'])
    def test_old_reserved_actor_keys_fail_closed_http_and_internal(self):
        path=base.cs.keys_mod.KEYFILE
        data=json.loads(path.read_text(encoding='utf-8'))
        for actor in ['local','user','companion']:
            data['actors'][actor]={'key_sha256':base.cs.keys_mod.sha('reserved-'+actor),'scopes':['read','write','admin']}
        path.write_text(json.dumps(data),encoding='utf-8')
        for actor in ['local','user','companion']:
            key='reserved-'+actor
            self.assertEqual(base.cs.keys_mod.resolve(key),(None,None))
            self.assertEqual(self.call('pae.key.issue',{'name':'ext:bad'},key)[0],401)
            self.assertEqual(self.scoped('/v1/cap/pae.status',key,'GET')[0],401)
        self.assertEqual(self.call('pae.status',{},'','local')[0],200)
        self.assertEqual(self.call('pae.status',{},'','companion')[0],200)
    def test_private_health_resolves_db_on_server(self):
        with patch.object(base.em,'DB_PATH','server-relative/synthetic.db'):
            status,result=self.http('/v1/health')
        self.assertEqual(status,200)
        self.assertEqual(result['db'],str(Path('server-relative/synthetic.db').resolve()))
    def test_storage_permission_error_is_not_generic_event_denial(self):
        with patch.object(base.cs,'_dispatch',side_effect=PermissionError('synthetic storage denied')):
            status,result=self.call('pae.status',key='read')
        self.assertEqual(status,500)
        self.assertNotEqual(result.get('error'),'generic_event_requires_trusted_user')
        self.assertNotIn('use pae.pin',json.dumps(result))
class MCPContracts(unittest.TestCase):
    def setUp(self):
        self.net=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'));self.net.start()
        self.addCleanup(self.net.stop)
        spec=importlib.util.spec_from_file_location('pae_review_mcp',Path(__file__).resolve().parents[1]/'mcp_server.py')
        self.m=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'PAE_BASE':'http://127.0.0.1:4815','PAE_KEY':'scoped-synthetic','PAE_HTTP_TOKEN':'full-trust-synthetic'}),patch.object(sys,'stdout',io.StringIO()),patch.object(sys,'stdin',io.StringIO()):spec.loader.exec_module(self.m)
    def test_scoped_key_is_bearer_even_when_root_env_exists(self):
        with patch.object(self.m.urllib.request,'urlopen',side_effect=AssertionError('use private opener')),patch.object(self.m.urllib.request.OpenerDirector,'open',return_value=io.BytesIO(b'{"ok":true}')) as opened:
            self.m._http('/v1/cap/manifest')
        req=opened.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'),'Bearer scoped-synthetic')
        self.assertEqual(req.get_header('X-pae-key'),'scoped-synthetic')
    def test_explicit_full_trust_token_without_key_is_supported(self):
        with patch.object(self.m,'KEY',''),patch.object(self.m,'TOKEN','full-trust-synthetic',create=True),patch.object(self.m.urllib.request,'urlopen',side_effect=AssertionError('use private opener')),patch.object(self.m.urllib.request.OpenerDirector,'open',return_value=io.BytesIO(b'{}')) as opened:
            self.m._http('/v1/cap/manifest')
        self.assertEqual(opened.call_args.args[0].get_header('Authorization'),'Bearer full-trust-synthetic')
        self.assertIsNone(opened.call_args.args[0].get_header('X-pae-key'))
    def test_missing_credentials_has_fixed_readable_error(self):
        with patch.object(self.m,'KEY',''),patch.object(self.m,'TOKEN','',create=True),patch.object(self.m.urllib.request.OpenerDirector,'open',side_effect=AssertionError('no network')):
            reply=self.m.handle({'id':1,'method':'tools/list'})
        self.assertIn('PAE_KEY',reply['error']['message'])
    def test_invalid_base_rejected_without_forwarding(self):
        for baseurl in ['https://127.0.0.1:4815','http://localhost:4815','http://user@127.0.0.1:4815','http://127.0.0.1:4815/','http://127.0.0.1:4815/x','http://127.0.0.1:4815?x','http://127.0.0.1:4815#x','http://127.0.0.1:0','http://127.0.0.1:99999']:
            with patch.object(self.m,'BASE',baseurl),patch.object(self.m.urllib.request.OpenerDirector,'open',side_effect=AssertionError('must not forward')):
                reply=self.m.handle({'id':1,'method':'tools/list'})
                self.assertEqual(reply['error']['message'],'invalid PAE_BASE')
    def test_tools_list_401_and_down_keep_stdio_alive(self):
        for failure in [urllib.error.HTTPError('SECRET_URL',401,'SECRET',{},io.BytesIO(b'PRIVATE_BODY')),urllib.error.URLError('SECRET_PATH')]:
            with patch.object(self.m,'_http',side_effect=failure),patch.object(sys,'stdin',io.StringIO('{"id":1,"method":"tools/list"}\n{"id":2,"method":"ping"}\n')),patch.object(sys,'stdout',io.StringIO()) as output:
                self.m.main()
                lines=[json.loads(x) for x in output.getvalue().splitlines()]
            self.assertIn('error',lines[0]);self.assertEqual(lines[1]['result'],{})
            self.assertNotIn('SECRET',json.dumps(lines));self.assertNotIn('PRIVATE_BODY',json.dumps(lines))
    def test_tool_call_errors_are_fixed_and_redacted(self):
        for failure in [urllib.error.URLError('SECRET_URL_TOKEN_PATH'),urllib.error.HTTPError('SECRET_URL',401,'SECRET',{},io.BytesIO(b'PRIVATE_BODY'))]:
            with patch.object(self.m,'_http',side_effect=failure):reply=self.m.handle({'id':1,'method':'tools/call','params':{'name':'pae_status'}})
            self.assertTrue(reply['result']['isError']);self.assertNotIn('SECRET',json.dumps(reply));self.assertNotIn('PRIVATE_BODY',json.dumps(reply))
    def test_transport_disables_proxy_and_redirect(self):
        handlers=[]
        def opener(*items):
            handlers.extend(items)
            out=type('Fake',(),{})();out.open=lambda *a,**k:io.BytesIO(b'{}');return out
        with patch.object(self.m.urllib.request,'urlopen',side_effect=AssertionError('use private opener')),patch.object(self.m.urllib.request,'build_opener',side_effect=opener):self.m._http('/v1/cap/manifest')
        self.assertTrue(any(isinstance(h,self.m.urllib.request.ProxyHandler) and h.proxies=={} for h in handlers))
        redirects=[h for h in handlers if isinstance(h,self.m.urllib.request.HTTPRedirectHandler)]
        self.assertTrue(redirects)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            redirects[0].redirect_request(None,None,302,'moved',{},'http://attacker.invalid')
        raised.exception.close()
    def test_annotate_is_not_labelled_read_only(self):
        with patch.object(self.m,'manifest',return_value={'capabilities':[{'name':'pae.annotate','kind':'observe','scope':'write','status':'implemented'}]}):
            self.assertNotIn('[只读]',self.m.tools()[0]['description'])

    def test_http_error_response_closed_after_tools_list(self):
        body=io.BytesIO(b'SYNTHETIC_PRIVATE_BODY')
        failure=urllib.error.HTTPError('https://synthetic.invalid/private',401,'synthetic',{},body)
        self.addCleanup(failure.close)
        with patch.object(self.m,'_http',side_effect=failure):
            result=self.m.handle({'id':1,'method':'tools/list'})
        self.assertEqual(result['error']['message'],'engine HTTP 401')
        self.assertTrue(failure.closed)
        self.assertTrue(body.closed)
    def test_http_error_response_closed_after_tools_call(self):
        body=io.BytesIO(b'SYNTHETIC_PRIVATE_BODY')
        failure=urllib.error.HTTPError('https://synthetic.invalid/private',401,'synthetic',{},body)
        self.addCleanup(failure.close)
        with patch.object(self.m,'_TOOL2CAP',{'pae_status':'pae.status'}),patch.object(self.m,'_http',side_effect=failure):
            result=self.m.handle({'id':1,'method':'tools/call','params':{'name':'pae_status'}})
        self.assertEqual(result['result']['content'][0]['text'],'engine HTTP 401')
        self.assertTrue(failure.closed)
        self.assertTrue(body.closed)
    def test_http_error_response_closed_after_manifest_fallback(self):
        body=io.BytesIO(b'SYNTHETIC_PRIVATE_BODY')
        failure=urllib.error.HTTPError('https://synthetic.invalid/private',401,'synthetic',{},body)
        self.addCleanup(failure.close)
        with patch.object(self.m,'_TOOL2CAP',None),patch.object(self.m,'manifest',side_effect=failure):
            self.assertEqual(self.m.tool_to_cap('pae_status'),'pae.status')
        self.assertTrue(failure.closed)
        self.assertTrue(body.closed)
class SourceScriptContracts(unittest.TestCase):
    def helper(self):
        path=Path(__file__).resolve().parents[1]/'source_health.py'
        if not path.is_file():self.fail('authenticated source health helper is missing')
        spec=importlib.util.spec_from_file_location('source_health',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
    def test_source_token_env_and_custom_config_path(self):
        mod=self.helper()
        with base.tempfile.TemporaryDirectory() as name:
            root=Path(name);cfg=root/'custom config.json';cfg.write_text('{}');token='synthetic-file-'+'f'*40
            (root/'http-auth.json').write_text(json.dumps({'token':token}))
            with patch.dict(os.environ,{'PAE_CONFIG_PATH':str(cfg),'PAE_HTTP_TOKEN':''}):self.assertEqual(mod.health_token(),token)
            with patch.dict(os.environ,{'PAE_CONFIG_PATH':str(cfg),'PAE_HTTP_TOKEN':'synthetic-env-'+'e'*40}):self.assertEqual(mod.health_token(),'synthetic-env-'+'e'*40)
    def test_missing_or_invalid_source_token_does_not_generate(self):
        mod=self.helper()
        with base.tempfile.TemporaryDirectory() as name,patch.dict(os.environ,{'PAE_CONFIG_PATH':str(Path(name)/'config.json'),'PAE_HTTP_TOKEN':''}):
            with self.assertRaises(ValueError):mod.health_token()
            self.assertFalse((Path(name)/'http-auth.json').exists())
            (Path(name)/'http-auth.json').write_text('{"token":"bad"}')
            with self.assertRaises(ValueError):mod.health_token()
    def test_default_source_token_path_is_beside_default_config(self):
        mod=self.helper()
        with patch.dict(os.environ,{'PAE_CONFIG_PATH':'','PAE_HTTP_TOKEN':''}),patch.object(mod.Path,'read_text',return_value=json.dumps({'token':'synthetic-'+'x'*40})) as read:
            self.assertEqual(mod.health_token(),'synthetic-'+'x'*40)
        self.assertEqual(read.call_args.kwargs.get('encoding'),'utf-8')
    def test_helper_sends_header_and_uses_configured_port(self):
        mod=self.helper()
        with patch.dict(os.environ,{'PAE_HTTP_TOKEN':'synthetic-'+'x'*40,'PAE_ENGINE_PORT':'4915'}),patch.object(mod.urllib.request.OpenerDirector,'open',return_value=io.BytesIO(json.dumps({'ok':True,'db':str(Path('synthetic.db').resolve())}).encode())) as opened:
            self.assertTrue(mod.probe()['ok'])
        req=opened.call_args.args[0]
        self.assertEqual(req.full_url,'http://127.0.0.1:4915/v1/health')
        self.assertEqual(req.get_header('Authorization'),'Bearer synthetic-'+'x'*40)
    def test_source_scripts_route_health_via_authenticated_helper(self):
        root=Path(__file__).resolve().parents[1]
        for name in ['start_engine.bat','status_engine.bat']:
            text=(root/name).read_text()
            self.assertIn('source_health.py',text)
            self.assertNotIn('Invoke-WebRequest',text)
            self.assertNotIn('taskkill',text.lower())

    def test_relative_server_db_identity_is_rejected(self):
        mod=self.helper()
        with patch.dict(os.environ,{'PAE_HTTP_TOKEN':'synthetic-'+'x'*40,'PAE_DB_PATH':'relative/synthetic.db'}),patch.object(mod.urllib.request.OpenerDirector,'open',return_value=io.BytesIO(b'{"ok":true,"db":"relative/synthetic.db"}')):
            result=mod.probe()
        self.assertFalse(result['ok'])
        self.assertNotIn('db',result)
    def test_http_error_response_closed_after_source_probe(self):
        mod=self.helper()
        body=io.BytesIO(b'SYNTHETIC_PRIVATE_BODY')
        failure=urllib.error.HTTPError('https://synthetic.invalid/private',401,'synthetic',{},body)
        self.addCleanup(failure.close)
        with patch.dict(os.environ,{'PAE_HTTP_TOKEN':'synthetic-'+'x'*40}),patch.object(mod.urllib.request.OpenerDirector,'open',side_effect=failure):
            result=mod.probe()
        self.assertEqual(result['error'],'engine_authentication_failed')
        self.assertTrue(failure.closed)
        self.assertTrue(body.closed)

class HTTPResourceContracts(unittest.TestCase):
    def test_http_error_response_closed_after_model_failure(self):
        from pae_core import llm
        body=io.BytesIO(b'SYNTHETIC_PRIVATE_BODY')
        failure=urllib.error.HTTPError('https://synthetic.invalid/private',401,'synthetic',{},body)
        self.addCleanup(failure.close)
        with patch.object(llm,'load_provider',return_value={'base':'https://synthetic.invalid','key':'SYNTHETIC_KEY','model':'synthetic'}),patch.object(llm.urllib.request,'urlopen',side_effect=failure):
            with self.assertRaisesRegex(RuntimeError,'^model_http_error$'):llm.chat([])
        self.assertTrue(failure.closed)
        self.assertTrue(body.closed)
if __name__=='__main__':unittest.main()
