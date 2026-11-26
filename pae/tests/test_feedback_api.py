"""Direct HTTP handler contract checks; never start the production singleton."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

research=Path(__file__).resolve().parents[3]
source=Path(__file__).resolve().parents[1]
if '--baseline' in sys.argv:
    sys.argv.remove('--baseline');source=research/'repository'/'pae'
for name in list(os.environ):
    if name.startswith('PAE_') or any(t in name.upper() for t in ('API_KEY','TOKEN','SECRET')):
        os.environ.pop(name,None)
sys.path.insert(0,str(source))
from fastapi import Response
from pae_core import api, engine


class HandlerTests(unittest.TestCase):
    def test_health_reports_storage_pause(self):
        class Paused:
            _learning_write_fault=True
        with patch.object(api,'get_engine',return_value=Paused()):
            response=Response()
            result=api.health(response)
        self.assertEqual(response.status_code,503)
        self.assertFalse(result['ok'])

    def test_annotation_pause_has_explicit_unavailable_response(self):
        class Paused:
            def annotate(self,*args):
                raise getattr(engine,'LearningWritePaused',RuntimeError)('synthetic pause')
        with patch.object(api,'get_engine',return_value=Paused()):
            try:result=api.annotate(api.AnnotateBody(text='synthetic'))
            except RuntimeError:self.fail('storage pause must have an explicit HTTP response')
        self.assertEqual(result.status_code,503)
        self.assertEqual(json.loads(result.body)['error'],'learning_write_paused')

    def test_normal_annotation_response_is_unchanged(self):
        class Healthy:
            def annotate(self,*args):return [{'surface':'synthetic','s_value':0.196}]
        with patch.object(api,'get_engine',return_value=Healthy()):
            self.assertEqual(api.annotate(api.AnnotateBody(text='synthetic')),
                             [{'surface':'synthetic','s_value':0.196}])

    def test_feedback_read_route_returns_backend_state(self):
        reader=getattr(api,'feedback_state',None)
        self.assertTrue(callable(reader))
        class Healthy:
            def feedback_state(self,lemma):return {'ok':True,'lemma':lemma,'recorded':True}
        with patch.object(api,'get_engine',return_value=Healthy()):
            self.assertTrue(reader('synthetic')['recorded'])


if __name__=='__main__':unittest.main(verbosity=2)
