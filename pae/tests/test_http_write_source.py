"""HTTP source and key identity regressions; synthetic DB, no listening server."""
from contextlib import ExitStack, closing
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pae_core import api, capsurface, engine, keys, paths, user_controls

BASE = 'http://127.0.0.1:41815'
MANIFEST = Path(__file__).resolve().parents[1] / 'extension/manifest.json'


def manifest_origin(path):
    key = json.loads(path.read_text(encoding='utf-8')).get('key', '')
    if not key:  # Keep the old fixture usable for the pre-fix RED run.
        return 'chrome-extension://' + 'a' * 32
    digest = hashlib.sha256(base64.b64decode(key, validate=True)).hexdigest()[:32]
    return 'chrome-extension://' + ''.join(chr(ord('a') + int(c, 16)) for c in digest)


EXTENSION = manifest_origin(MANIFEST)
FOREIGN = 'chrome-extension://' + ('b' if EXTENSION != 'chrome-extension://' + 'b' * 32 else 'c') * 32
WORD = 'fixturecanopy'


class HttpWriteSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='opengloss-source-'))
        self.work = Path(directory)
        env = {name: value for name, value in os.environ.items()
               if not name.startswith('PAE_') and not any(
                   part in name.upper() for part in ('API_KEY', 'TOKEN', 'SECRET'))}
        env.update(PAE_ROOT=directory, PAE_DB_PATH=str(self.work / 'events.sqlite'),
                   PAE_DICT_PATH=str(self.work / 'dict.sqlite'),
                   PAE_LEMMA_PATH=str(self.work / 'lemmas.txt'),
                   PAE_KEYS_PATH=str(self.work / 'keys.json'),
                   PAE_CONTROLS_PATH=str(self.work / 'controls.json'),
                   PAE_PERSONA_PATH=str(self.work / 'persona.json'),
                   PAE_SCHEDULE_CONFIG=str(self.work / 'config.json'))
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))
        self.stack.enter_context(patch.object(socket.socket, 'bind', side_effect=AssertionError('no bind')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('no connect')))
        self.stack.enter_context(patch('urllib.request.urlopen', side_effect=AssertionError('no provider')))
        dictionary = self.work / 'dict.sqlite'
        with closing(sqlite3.connect(dictionary)) as conn:
            conn.execute('CREATE TABLE dict_words(word TEXT PRIMARY KEY,translation TEXT,bnc INTEGER)')
            conn.execute('INSERT INTO dict_words VALUES (?, ?, 0)', (WORD, 'n. 合成词'))
            conn.execute('CREATE TABLE exchange_map(inflected TEXT,base TEXT)')
            conn.commit()
        lemma = self.work / 'lemmas.txt'
        lemma.write_text(WORD + '/1 -> fixturecanopys\n', encoding='utf-8')
        self.stack.enter_context(patch.object(engine, 'DICT_SQLITE_PATH', str(dictionary)))
        self.stack.enter_context(patch.object(engine, 'DB_PATH', str(self.work / 'events.sqlite')))
        self.stack.enter_context(patch.object(keys, 'KEYFILE', self.work / 'keys.json'))
        self.stack.enter_context(patch.object(capsurface, 'CONFIG_PATH', self.work / 'config.json'))
        self.stack.enter_context(patch.object(paths, 'db_path', return_value=str(self.work / 'events.sqlite')))
        self.stack.enter_context(patch.object(paths, 'dict_path', return_value=str(dictionary)))
        self.eng = engine.Engine(self.work / 'events.sqlite', lemma)
        self.stack.callback(self.eng.conn.close)
        self.stack.callback(self.eng.learning_conn.close)
        self.stack.callback(self.eng._close_translation_lookup)
        self.eng._proactive_dispatch_ts = float('inf')
        self.stack.enter_context(patch.object(api, 'get_engine', return_value=self.eng))
        self.stack.enter_context(patch.object(engine, 'get_engine', return_value=self.eng))

    async def call(self, path, body=None, *, origin=None, site=None, host='127.0.0.1:41815',
                   key=None, content_type='application/json', method='POST'):
        headers = [(b'host', host.encode())]
        for name, value in ((b'origin', origin), (b'sec-fetch-site', site),
                            (b'x-pae-key', key), (b'content-type', content_type)):
            if value is not None:
                headers.append((name, value.encode()))
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': method, 'scheme': 'http', 'path': path, 'query_string': b'',
                 'headers': headers, 'server': ('127.0.0.1', 41815),
                 'client': ('127.0.0.1', 40101)}
        sent = []

        async def receive():
            return {'type': 'http.request', 'body': json.dumps(body or {}).encode(), 'more_body': False}

        async def send(message):
            sent.append(message)

        await api.app(scope, receive, send)
        payload = b''.join(item.get('body', b'') for item in sent
                           if item['type'] == 'http.response.body')
        return sent[0]['status'], json.loads(payload)

    def snapshot(self):
        # Rejections must not mutate the learning DB, settings, audit, or live states.
        return (tuple(self.eng.conn.iterdump()), repr(self.eng.states), repr(self.eng.contacts),
                [(name, (self.work / name).read_bytes() if (self.work / name).exists() else None)
                 for name in ('controls.json', 'keys.json', 'persona.json')])

    async def assert_rejected(self, path, body, **headers):
        before = self.snapshot()
        status, result = await self.call(path, body, **headers)
        self.assertEqual(status, 403, (path, {k: v for k, v in headers.items() if k != 'key'}, result))
        self.assertEqual(self.snapshot(), before)

    async def test_cross_site_event_and_annotate_cannot_write(self):
        for ctype in (None, 'application/json'):
            for path, body in (
                ('/v1/event', {'type': 'known_click', 'lemma': WORD}),
                ('/v1/annotate', {'text': WORD, 'page_id': 'synthetic-untrusted'}),
            ):
                with self.subTest(path=path, content_type=ctype):
                    await self.assert_rejected(path, body, origin='https://fixture.invalid',
                                               site='cross-site', content_type=ctype)

    async def test_shared_capabilities_cannot_bypass_source_guard(self):
        for name, body in (
            ('pae.mute', {'muted': True}),
            ('pae.propose', {'patch': [{'key': 'max_ann_per_page', 'to': 11}]}),
            ('pae.event', {'type': 'hover', 'lemma': WORD}),
            ('pae.annotate', {'text': WORD}),
            ('pae.persona.say', {'stimulus': 'synthetic only'}),
            ('pae.params', {}),
        ):
            with self.subTest(name=name):
                await self.assert_rejected('/v1/cap/' + name, body,
                                           origin='https://fixture.invalid', site='cross-site',
                                           content_type=None)

    async def test_absent_origin_does_not_override_cross_site_metadata(self):
        for site in ('cross-site', 'same-site'):
            for path, body in (
                ('/v1/event', {'type': 'hover', 'lemma': WORD}),
                ('/v1/cap/pae.mute', {'muted': True}),
                ('/v1/manage/mute', {'muted': True}),
            ):
                with self.subTest(site=site, path=path):
                    await self.assert_rejected(path, body, site=site, content_type=None)

    async def test_null_wrong_port_invalid_origin_and_nonloopback_host_rejected(self):
        cases = [dict(origin=origin) for origin in (
            'null', 'http://localhost:41815', 'http://127.0.0.1:41816',
            'https://127.0.0.1:41815', EXTENSION + '.invalid',
            EXTENSION + '/forged', 'chrome-extension://short',
            'http://@127.0.0.1:41815', BASE + '/forged',
            BASE + '?forged=1', BASE + '#forged',
            'http://127.0.0.1:invalid', 'http://[', EXTENSION + ':41815',
        )]
        cases.append(dict(host='fixture.invalid:41815'))
        for headers in cases:
            with self.subTest(headers=headers):
                await self.assert_rejected('/v1/cap/pae.mute', {'muted': True}, **headers)

    async def test_legitimate_extension_reading_and_feedback_actually_commit(self):
        status, rows = await self.call('/v1/annotate', {
            'text': WORD, 'page_id': 'synthetic-extension', 'max_annotations': 1,
        }, origin=EXTENSION, site='cross-site')
        self.assertEqual(status, 200, rows)
        self.assertEqual([row['lemma'] for row in rows], [WORD])
        self.assertFalse(self.eng.feedback_state(WORD)['recorded'])
        status, result = await self.call('/v1/event', {
            'type': 'known_click', 'lemma': WORD, 'session_id': 'synthetic-extension',
        }, origin=EXTENSION, site='cross-site')
        self.assertEqual(status, 200, result)
        self.assertTrue(result['counted'])
        self.assertTrue(self.eng.feedback_state(WORD)['recorded'])
        self.assertEqual(self.eng.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type='known_click'").fetchone()[0], 1)
        status, result = await self.call('/v1/cap/pae.mute', {'muted': True}, origin=EXTENSION)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['result']['muted'])

    async def test_native_and_same_origin_management_remain_password_free(self):
        for headers in ({}, {'site': 'none'}, {'origin': BASE, 'site': 'same-origin'}):
            for value in (True, False):
                status, result = await self.call('/v1/manage/mute', {'muted': value}, **headers)
                self.assertEqual(status, 200, result)
                self.assertEqual(result['result']['muted'], value)
        status, result = await self.call('/v1/manage/page-cap', {'max_ann_per_page': 12}, origin=BASE)
        self.assertEqual(status, 200, result)
        self.assertEqual(json.loads(self.eng.conn.execute(
            "SELECT value FROM params WHERE key='max_ann_per_page'").fetchone()[0]), 12)
        status, rows = await self.call('/v1/annotate', {'text': WORD}, content_type=None)
        self.assertEqual(status, 200, rows)
        self.assertEqual(rows[0]['lemma'], WORD)

    async def test_localhost_ipv6_and_default_http_port_keep_same_origin_use(self):
        for host, origin in (
            ('localhost:41815', 'http://localhost:41815'),
            ('[::1]:41815', 'http://[::1]:41815'),
            ('127.0.0.1:80', 'http://127.0.0.1'),
        ):
            status, result = await self.call('/v1/cap/pae.mute', {'muted': True},
                                             host=host, origin=origin, site='same-origin')
            self.assertEqual(status, 200, result)
            self.assertTrue(result['result']['muted'])

    async def test_extension_does_not_gain_dedicated_management_access(self):
        for path, body in (
            ('/v1/manage/mute', {'muted': True}),
            ('/v1/manage/page-cap', {'max_ann_per_page': 12}),
            ('/v1/manage/ai', {'ai_enabled': True}),
            ('/v1/persona', {'params': {}}),
            ('/v1/persona/say', {'stimulus': 'synthetic only'}),
        ):
            await self.assert_rejected(path, body, origin=EXTENSION, site='cross-site')
        self.assertFalse(user_controls.allowed('manual'))

    async def test_key_identity_cannot_be_overridden_and_scope_still_applies(self):
        local = keys.issue('local', ['read', 'write'])
        status, result = await self.call('/v1/cap/pae.mute', {
            'muted': True, 'actor': 'ext:synthetic-mask',
        }, key=local)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['actor'], 'local')
        row = self.eng.conn.execute(
            "SELECT actor FROM cap_calls WHERE capability='pae.mute' ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual(row[0], 'local')
        status, result = capsurface.handle('pae.mute', {'muted': False, 'actor': 'user'}, local)
        self.assertEqual((status, result['actor']), (200, 'local'))
        status, result = capsurface.handle('pae.mute', {'muted': False, 'actor': 'user'})
        self.assertEqual((status, result['actor']), (200, 'user'))
        status, result = capsurface.handle('pae.mute', {'muted': True}, actor_hint='companion')
        self.assertEqual((status, result['actor']), (200, 'companion'))
        for name, body in (
            ('pae.propose', {'patch': [{'key': 'max_ann_per_page', 'to': 13}]}),
            ('pae.persona.update', {'params': {}}),
        ):
            status, result = await self.call('/v1/cap/' + name, body, key=local)
            self.assertEqual((status, result['error']), (403, 'reserved_role'))
        reader = keys.issue('ext:synthetic-reader', ['read'])
        status, result = await self.call('/v1/cap/pae.mute', {'muted': False, 'actor': 'local'}, key=reader)
        self.assertEqual((status, result['error']), (403, 'scope_denied'))
        writer = keys.issue('ext:synthetic-writer', ['read', 'write'])
        status, result = await self.call('/v1/cap/pae.mute', {'muted': False, 'actor': 'local'}, key=writer)
        self.assertEqual((status, result['actor']), (200, 'ext:synthetic-writer'))
        status, result = await self.call('/v1/cap/pae.params', key=reader, method='GET')
        self.assertEqual((status, result['actor']), (200, 'ext:synthetic-reader'))
        status, result = await self.call('/v1/cap/pae.params', key='synthetic-invalid')
        self.assertEqual((status, result['error']), (401, 'unauthorized'))

    async def test_valid_key_does_not_bypass_http_source_check(self):
        writer = keys.issue('ext:synthetic-writer', ['read', 'write'])
        await self.assert_rejected('/v1/cap/pae.mute', {'muted': True}, key=writer,
                                   origin='https://fixture.invalid', site='cross-site')


    async def test_key_memory_source_uses_authenticated_actor(self):
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        for spoof in (None, 'user'):
            with self.subTest(spoof=spoof):
                body = {'kind': 'preference', 'text': 'synthetic fact', 'evidence': ['synthetic']}
                if spoof is not None:
                    body['actor'] = spoof
                status, result = await self.call('/v1/cap/pae.remember', body, key=writer)
                self.assertEqual(status, 200, result)
                row = self.eng.conn.execute('SELECT source_actor FROM facts WHERE fact_id=?',
                                            (result['result']['fact_id'],)).fetchone()
                self.assertEqual(row[0], 'ext:fixture-writer')

    async def test_key_cannot_forget_a_protected_user_fact(self):
        from pae_core import memory
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        for spoof in (None, 'user'):
            with self.subTest(spoof=spoof):
                ok, fact = memory.remember(self.eng.conn, 'user', 'goal', 'synthetic user fact', ['fixture'])
                self.assertTrue(ok)
                body = {'fact_id': fact['fact_id']}
                if spoof is not None:
                    body['actor'] = spoof
                status, result = await self.call('/v1/cap/pae.forget', body, key=writer)
                self.assertEqual(status, 500, result)  # Existing business-error envelope, unchanged here.
                self.assertIn('protected_fact', result['error'])
                self.assertIsNone(self.eng.conn.execute('SELECT superseded_by FROM facts WHERE fact_id=?',
                                                        (fact['fact_id'],)).fetchone()[0])

    async def test_key_pin_cannot_evade_existing_twenty_event_quota(self):
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        for i in range(20):
            self.assertTrue(self.eng.record_event('user_pin', 'fixturequota' + str(i),
                                                 source='ext:fixture-writer')['counted'])
        for spoof in (None, 'user', 'ext:fixture-mask'):
            with self.subTest(spoof=spoof):
                body = {'lemma': 'fixtureexcess'}
                if spoof is not None:
                    body['actor'] = spoof
                status, result = await self.call('/v1/cap/pae.pin', body, key=writer)
                self.assertEqual(status, 500, result)
                self.assertIn('pin_quota_exceeded', result['error'])
        self.assertEqual(self.eng.conn.execute("SELECT COUNT(*) FROM events WHERE type='user_pin'").fetchone()[0], 20)

    async def test_key_pin_persisted_source_matches_audit_identity(self):
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        status, result = await self.call('/v1/cap/pae.pin', {'lemma': WORD, 'actor': 'user'}, key=writer)
        self.assertEqual(status, 200, result)
        self.assertTrue(result['result']['counted'])
        self.assertEqual(self.eng.conn.execute("SELECT source FROM events WHERE type='user_pin'").fetchone()[0],
                         result['actor'])

    async def test_key_session_actor_matches_authenticated_actor(self):
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        status, result = await self.call('/v1/cap/pae.session.open', {'actor': 'user'}, key=writer)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['result']['actor'], result['actor'])
        self.assertEqual(self.eng.conn.execute('SELECT actor FROM chat_sessions WHERE session_id=?',
                         (result['result']['session_id'],)).fetchone()[0], result['actor'])

    async def test_key_push_logs_use_authenticated_actor(self):
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        for name, body, kind in (
            ('pae.say', {'text': 'synthetic message', 'actor': 'user'}, 'say'),
            ('pae.hint', {'lemma': WORD, 'actor': 'user'}, 'hint'),
        ):
            with self.subTest(name=name):
                status, result = await self.call('/v1/cap/' + name, body, key=writer)
                self.assertEqual(status, 200, result)
                self.assertEqual(self.eng.conn.execute('SELECT actor FROM push_log WHERE kind=? ORDER BY id DESC LIMIT 1',
                                                      (kind,)).fetchone()[0], result['actor'])

    async def test_key_restore_records_authenticated_source(self):
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        status, result = await self.call('/v1/cap/pae.familiarity.restore', {'lemma': WORD, 'actor': 'user'}, key=writer)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.eng.conn.execute('SELECT source FROM familiarity_override WHERE lemma=?',
                                              (WORD,)).fetchone()[0], result['actor'])

    async def test_key_tick_uses_authenticated_actor_without_model_call(self):
        import time
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        self.eng.conn.execute('INSERT INTO agent_log(ts,actor) VALUES(?,?)', (int(time.time()), 'fixture'))
        self.eng.conn.commit()
        status, result = await self.call('/v1/cap/pae.agent.tick', {'actor': 'user'}, key=writer)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['result']['error'], 'rate_limited')
        self.assertEqual(result['result']['actor'], result['actor'])
        self.assertEqual(self.eng.conn.execute('SELECT actor FROM agent_log ORDER BY id DESC LIMIT 1').fetchone()[0],
                         result['actor'])

    async def test_native_identity_read_filter_and_input_object_are_preserved(self):
        status, result = await self.call('/v1/cap/pae.session.open', {'actor': 'user'})
        self.assertEqual((status, result['result']['actor']), (200, 'user'))
        body = {'actor': 'user', 'kind': 'preference', 'text': 'synthetic original input', 'evidence': ['fixture']}
        writer = keys.issue('ext:fixture-writer', ['read', 'write'])
        status, result = capsurface.handle('pae.remember', body, writer)
        self.assertEqual(status, 200, result)
        self.assertEqual(body['actor'], 'user')
        self.assertEqual(self.eng.conn.execute('SELECT source_actor FROM facts WHERE fact_id=?',
                         (result['result']['fact_id'],)).fetchone()[0], 'ext:fixture-writer')
        status, result = await self.call('/v1/cap/pae.propose', {
            'actor': 'user', 'patch': [{'key': 'max_ann_per_page', 'to': 11}], 'rationale': 'synthetic filter',
        })
        self.assertEqual(status, 200, result)
        reader = keys.issue('ext:fixture-reader', ['read'])
        status, result = await self.call('/v1/cap/pae.decisions', {'actor': 'user'}, key=reader)
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result['result']), 1)
        self.assertEqual(result['result'][0]['actor'], 'user')


    async def test_process_cannot_forget_protected_user_facts(self):
        from pae_core import memory
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            for spoof in ('user', 'local'):
                with self.subTest(role=role, spoof=spoof):
                    ok, fact = memory.remember(self.eng.conn, 'user', 'goal', 'synthetic protected', ['fixture'])
                    self.assertTrue(ok)
                    status, result = capsurface.handle('pae.forget',
                        {'fact_id': fact['fact_id'], 'actor': spoof}, actor_hint=role)
                    self.assertEqual(status, 500, result)
                    self.assertIn('protected_fact', result['error'])
                    self.assertIsNone(self.eng.conn.execute('SELECT superseded_by FROM facts WHERE fact_id=?',
                        (fact['fact_id'],)).fetchone()[0])

    async def test_process_memory_source_and_original_params(self):
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            for spoof in (None, 'user'):
                with self.subTest(role=role, spoof=spoof):
                    body = {'kind': 'preference', 'text': 'synthetic role fact', 'evidence': ['fixture']}
                    if spoof is not None:
                        body['actor'] = spoof
                    before = body.copy()
                    status, result = capsurface.handle('pae.remember', body, actor_hint=role)
                    self.assertEqual(status, 200, result)
                    self.assertEqual(body, before)
                    self.assertEqual(self.eng.conn.execute('SELECT source_actor FROM facts WHERE fact_id=?',
                        (result['result']['fact_id'],)).fetchone()[0], role)

    async def test_process_pin_cannot_evade_existing_quota(self):
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            for i in range(20):
                self.assertTrue(self.eng.record_event('user_pin', 'fixturequota' + role + str(i), source=role)['counted'])
            for spoof in ('user', 'local', 'ext:fixture-mask'):
                with self.subTest(role=role, spoof=spoof):
                    status, result = capsurface.handle('pae.pin',
                        {'lemma': 'fixtureexcess', 'actor': spoof}, actor_hint=role)
                    self.assertEqual(status, 500, result)
                    self.assertIn('pin_quota_exceeded', result['error'])
        self.assertEqual(self.eng.conn.execute("SELECT COUNT(*) FROM events WHERE type='user_pin'").fetchone()[0], 60)

    async def test_process_pin_persisted_source(self):
        for index, role in enumerate(('companion', 'analyst', 'ext:fixture-process')):
            with self.subTest(role=role):
                lemma = 'fixturepin' + str(index)
                status, result = capsurface.handle('pae.pin', {'lemma': lemma, 'actor': 'user'}, actor_hint=role)
                self.assertEqual(status, 200, result)
                self.assertTrue(result['result']['counted'])
                self.assertEqual(self.eng.conn.execute("SELECT source FROM events WHERE type='user_pin' ORDER BY rowid DESC LIMIT 1").fetchone()[0], role)

    async def test_process_session_uses_established_role(self):
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            for body in ({}, {'actor': 'user'}):
                with self.subTest(role=role, body=body):
                    status, result = capsurface.handle('pae.session.open', body, actor_hint=role)
                    self.assertEqual(status, 200, result)
                    self.assertEqual(result['result']['actor'], role)
                    self.assertEqual(self.eng.conn.execute('SELECT actor FROM chat_sessions WHERE session_id=?',
                        (result['result']['session_id'],)).fetchone()[0], role)

    async def test_process_push_uses_established_role(self):
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            for name, body, kind in (
                ('pae.say', {'text': 'synthetic message', 'actor': 'user'}, 'say'),
                ('pae.hint', {'lemma': WORD, 'actor': 'user'}, 'hint'),
            ):
                with self.subTest(role=role, name=name):
                    status, result = capsurface.handle(name, body, actor_hint=role)
                    self.assertEqual(status, 200, result)
                    self.assertEqual(self.eng.conn.execute('SELECT actor FROM push_log WHERE kind=? ORDER BY id DESC LIMIT 1',
                        (kind,)).fetchone()[0], role)

    async def test_process_restore_uses_established_role(self):
        for index, role in enumerate(('companion', 'analyst', 'ext:fixture-process')):
            with self.subTest(role=role):
                lemma = 'fixturerestore' + str(index)
                status, result = capsurface.handle('pae.familiarity.restore',
                    {'lemma': lemma, 'actor': 'user'}, actor_hint=role)
                self.assertEqual(status, 200, result)
                self.assertEqual(self.eng.conn.execute('SELECT source FROM familiarity_override WHERE lemma=?',
                    (lemma,)).fetchone()[0], role)

    async def test_process_tick_keeps_role_when_rate_limited(self):
        import time
        self.eng.conn.execute('INSERT INTO agent_log(ts,actor) VALUES(?,?)', (int(time.time()), 'fixture'))
        self.eng.conn.commit()
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            with self.subTest(role=role):
                status, result = capsurface.handle('pae.agent.tick', {'actor': 'user'}, actor_hint=role)
                self.assertEqual(status, 200, result)
                self.assertEqual(result['result']['error'], 'rate_limited')
                self.assertEqual(result['result']['actor'], role)
                self.assertEqual(self.eng.conn.execute('SELECT actor FROM agent_log ORDER BY id DESC LIMIT 1').fetchone()[0], role)

    async def test_nested_agent_and_repaired_action_cannot_impersonate_user(self):
        from pae_core import agent, memory
        ok, fact = memory.remember(self.eng.conn, 'user', 'goal', 'synthetic nested protection', ['fixture'])
        self.assertTrue(ok)
        action = {'name': 'pae.forget', 'params': {'fact_id': fact['fact_id'], 'actor': 'user'}}
        repaired = {'name': 'pae.forget', 'params': {'fact_id': fact['fact_id'], 'actor': 'local'}}
        # Replace only provider output; observe, parse, dispatch, retry and persistence are real.
        replies = [{'content': json.dumps({'thought': 'synthetic', 'actions': [action]})},
                   {'content': json.dumps(repaired)}]
        with patch.object(agent.llm_mod, 'chat', side_effect=replies) as provider:
            tick = agent.tick(self.eng.conn, actor='ext:fixture-process')
        self.assertIsNone(tick['error'], tick)
        self.assertEqual(tick['actor'], 'ext:fixture-process')
        self.assertEqual(len(tick['actions']), 2, tick)
        self.assertEqual(provider.call_count, 2)
        for item in tick['actions']:
            self.assertEqual(item['status'], 500, item)
            self.assertIn('protected_fact', item['body']['error'])
        self.assertTrue(tick['actions'][1]['repaired'])
        self.assertIsNone(self.eng.conn.execute('SELECT superseded_by FROM facts WHERE fact_id=?',
            (fact['fact_id'],)).fetchone()[0])

    async def test_nested_agent_and_repaired_pin_cannot_bypass_quota(self):
        from pae_core import agent
        role = 'ext:fixture-process'
        for i in range(20):
            self.assertTrue(self.eng.record_event('user_pin', 'fixturenested' + str(i), source=role)['counted'])
        action = {'name': 'pae.pin', 'params': {'lemma': 'fixtureexcess', 'actor': 'user'}}
        repaired = {'name': 'pae.pin', 'params': {'lemma': 'fixtureexcess', 'actor': 'local'}}
        replies = [{'content': json.dumps({'thought': 'synthetic', 'actions': [action]})},
                   {'content': json.dumps(repaired)}]
        with patch.object(agent.llm_mod, 'chat', side_effect=replies) as provider:
            tick = agent.tick(self.eng.conn, actor=role)
        self.assertIsNone(tick['error'], tick)
        self.assertEqual(len(tick['actions']), 2, tick)
        self.assertEqual(provider.call_count, 2)
        for item in tick['actions']:
            self.assertEqual(item['status'], 500, item)
            self.assertIn('pin_quota_exceeded', item['body']['error'])
        self.assertTrue(tick['actions'][1]['repaired'])
        self.assertEqual(self.eng.conn.execute("SELECT COUNT(*) FROM events WHERE type='user_pin'").fetchone()[0], 20)

    async def test_process_read_filter_and_local_user_operations_preserved(self):
        from pae_core import memory
        for hint in (None, 'local', 'user'):
            ok, fact = memory.remember(self.eng.conn, 'user', 'goal', 'synthetic user choice', ['fixture'])
            self.assertTrue(ok)
            status, result = capsurface.handle('pae.forget',
                {'actor': 'user', 'fact_id': fact['fact_id']}, actor_hint=hint)
            self.assertEqual(status, 200, result)
            self.assertTrue(result['result']['changed'])
        status, result = capsurface.handle('pae.propose', {
            'actor': 'user', 'patch': [{'key': 'max_ann_per_page', 'to': 11}], 'rationale': 'synthetic filter',
        })
        self.assertEqual(status, 200, result)
        body = {'actor': 'user'}
        for role in ('companion', 'analyst', 'ext:fixture-process'):
            status, result = capsurface.handle('pae.decisions', body, actor_hint=role)
            self.assertEqual(status, 200, result)
            self.assertEqual(len(result['result']), 1)
            self.assertEqual(result['result'][0]['actor'], 'user')
        self.assertEqual(body, {'actor': 'user'})


    async def test_release_manifest_has_stable_public_identity(self):
        key = json.loads(MANIFEST.read_text(encoding='utf-8')).get('key')
        self.assertIsInstance(key, str)
        self.assertTrue(base64.b64decode(key, validate=True))

    async def test_other_extension_origin_cannot_write(self):
        writer = keys.issue('local', ['read', 'write'])
        for token in (None, writer):
            for path, body in (
                ('/v1/annotate', {'text': WORD, 'page_id': 'synthetic-foreign'}),
                ('/v1/event', {'type': 'known_click', 'lemma': WORD}),
                ('/v1/cap/pae.propose', {'actor': 'user', 'patch': [
                    {'key': 'max_ann_per_page', 'to': 13}]}),
            ):
                with self.subTest(path=path, keyed=token is not None):
                    await self.assert_rejected(path, body, origin=FOREIGN, site='cross-site', key=token)

    async def test_foreign_extension_cannot_use_manual_ai_permission(self):
        user_controls.save({'ai_enabled': True, 'key': 'synthetic-not-a-real-key',
                            'base': 'https://fixture.invalid', 'model': 'synthetic-model'})
        calls = []
        def provider(*args, **kwargs):
            calls.append(True)
            return io.BytesIO(json.dumps({'choices': [{'message': {
                'content': 'synthetic reply'}}]}).encode())
        with patch('urllib.request.urlopen', side_effect=provider):
            await self.assert_rejected('/v1/cap/pae.persona.say', {
                'stimulus': 'synthetic-only'}, origin=FOREIGN, site='cross-site')
        self.assertEqual(calls, [])

    async def test_project_extension_manual_ai_keeps_user_permission(self):
        calls = []
        def provider(*args, **kwargs):
            calls.append(True)
            return io.BytesIO(json.dumps({'choices': [{'message': {
                'content': 'synthetic reply'}}]}).encode())
        with patch('urllib.request.urlopen', side_effect=provider):
            status, result = await self.call('/v1/cap/pae.persona.say', {
                'stimulus': 'synthetic-only'}, origin=EXTENSION)
            self.assertEqual(status, 500, result)
            self.assertIn('AI call disabled', result['error'])
            self.assertEqual(calls, [])
            user_controls.save({'ai_enabled': True, 'key': 'synthetic-not-a-real-key',
                                'base': 'https://fixture.invalid', 'model': 'synthetic-model'})
            status, result = await self.call('/v1/cap/pae.persona.say', {
                'stimulus': 'synthetic-only'}, origin=EXTENSION)
            self.assertEqual(status, 200, result)
            self.assertEqual(len(calls), 1)
        controls = user_controls.load()
        for field in ('shadow_ai_enabled', 'proactive_ai_enabled', 'scheduled_ai_enabled'):
            self.assertFalse(controls[field])

    async def test_invalid_identity_fails_closed_without_blocking_local_management(self):
        path = self.work / 'identity-manifest.json'
        for raw in (None, '{', '[]', '{}', '{"key":""}', '{"key":23}',
                    '{"key":null}', '{"key":"%%%"}', '{"key":"===="}'):
            with self.subTest(raw=raw):
                if path.exists():
                    path.unlink()
                if raw is not None:
                    path.write_text(raw, encoding='utf-8')
                with patch.object(api, 'EXTENSION_MANIFEST_PATH', path):
                    self.assertIsNone(api._extension_origin())
                    await self.assert_rejected('/v1/event', {
                        'type': 'known_click', 'lemma': WORD}, origin=EXTENSION)
                    for origin in (None, BASE):
                        status, result = await self.call('/v1/manage/mute', {'muted': False}, origin=origin)
                        self.assertEqual(status, 200, result)

    async def test_identity_is_stable_after_directory_copy(self):
        moved = self.work / 'moved' / 'manifest.json'
        moved.parent.mkdir()
        moved.write_bytes(MANIFEST.read_bytes())
        with patch.object(api, 'EXTENSION_MANIFEST_PATH', moved):
            self.assertEqual(api._extension_origin(), EXTENSION)
            status, result = await self.call('/v1/cap/pae.mute', {'muted': False}, origin=EXTENSION)
            self.assertEqual(status, 200, result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
