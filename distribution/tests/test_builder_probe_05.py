from pathlib import Path
import io, json, sys, tempfile, types, unittest, zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, build_release, package


class BuilderAndProbeContracts(unittest.TestCase):
    def test_builder_requires_one_matching_runtime_pth_and_zip(self):
        def pair(names):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for name in names:
                    archive.writestr(name, b'synthetic')
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                return build_release.runtime_pth_pair(archive)
        self.assertEqual(pair(['python315._pth', 'python315.zip']), ('python315._pth', 'python315.zip'))
        for names in (['python315.zip'], ['python315._pth'],
                      ['python315._pth', 'python314.zip'],
                      ['python315._pth', 'python314._pth', 'python315.zip']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                pair(names)

    def test_release_metadata_tracks_050_audited_source(self):
        self.assertEqual(build_release.DEFAULT_VERSION, '0.5.0-rc.4')
        self.assertEqual(build_release.SOURCE_BASE_COMMIT,
                         'fdd2acccb0b46969055a4867f0be7f5136998c76')

    def test_supervisor_checks_public_instance_identity_without_bearer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); data = root / 'data'; data.mkdir()
            package.atomic_json(root / 'installation.json', {'product': 'OpenGloss',
                'instance_id': 'a' * 32, 'engine_port': 54975, 'manager_port': 54976})
            package.atomic_json(data / 'http-auth.json', {'token': 'b' * 43})
            version = root / 'versions/0.5.0'; version.mkdir(parents=True)
            seen = []
            class Child:
                stdin = io.StringIO()
                def poll(self): return None
                def wait(self, timeout): return 0
            class Opener:
                def open(self, request, timeout):
                    seen.append(request.get_header('Authorization'))
                    return io.BytesIO(json.dumps({'ok': True, 'instance_id': 'a'*32}).encode())
            supervisor = bootstrap.Supervisor(root)
            with patch.object(package, 'verify_directory'), \
                 patch.object(bootstrap.subprocess, 'Popen', return_value=Child()) as popen, \
                 patch.object(bootstrap.urllib.request, 'build_opener', return_value=Opener()):
                self.assertTrue(supervisor.start(version))
                supervisor.stop()
            self.assertEqual(seen, [None])
            self.assertNotIn('PAE_HTTP_TOKEN',popen.call_args.kwargs['env']);self.assertEqual(popen.call_args.kwargs['env']['PAE_INSTANCE_ID'],'a'*32)
            self.assertEqual(popen.call_args.kwargs['env']['PAE_ENGINE_PORT'], '54975')


if __name__ == '__main__':
    unittest.main()
