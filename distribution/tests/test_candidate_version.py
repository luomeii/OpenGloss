from pathlib import Path
import sys, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from distribution import build_release, package


class CandidateVersionContracts(unittest.TestCase):
    def test_rc_also_requires_public_ca(self):
        files={name:{'sha256':'a'*64,'size':1} for name in (
            'runtime/python.exe','runtime/pythonw.exe','distribution/bootstrap.py',
            'app/pae/config.json','app/pae/persona/default.json')}
        with self.assertRaises(ValueError):package.check_manifest({
            'format':1,'product':'OpenGloss','platform':'windows-x64',
            'version':'0.5.0-rc.1','files':files})

    def test_extension_numeric_version_increases_from_candidate_to_stable(self):
        rc=build_release.extension_version('0.5.0-rc.1')
        stable=build_release.extension_version('0.5.0')
        self.assertLess(tuple(map(int,rc.split('.'))),tuple(map(int,stable.split('.'))))
        self.assertEqual(rc,'0.5.0.1')
        with self.assertRaises(ValueError):build_release.extension_version('0.5.0-rc.65535')

    def test_explicit_recovery_entry_is_valid_public_payload(self):
        self.assertEqual(str(package.payload_path('Recover.cmd')),'Recover.cmd')


if __name__=='__main__':unittest.main()
