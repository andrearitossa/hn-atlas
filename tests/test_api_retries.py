import base64
import os
import unittest
from unittest.mock import Mock,patch

import requests

import embed
import llm


class ApiRetryTests(unittest.TestCase):
    def test_semantic_rate_limit_waits_for_server_retry_window(self):
        limited = Mock(ok=False, status_code=429, headers={'retry-after':'45'})
        success = Mock(ok=True)
        success.json.return_value = {'choices':[{'message':{'content':'{"ok":true}'}}]}
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test'}), \
             patch.object(llm.requests, 'post', side_effect=[limited, success]), \
             patch.object(llm.time, 'sleep') as sleep:
            self.assertEqual(llm.ask_json('Prompt'), {'ok':True})
            sleep.assert_called_once_with(45)

    def test_long_subject_reviews_split_without_losing_ids(self):
        import routing
        stories = [{'id':i,'subject':'x'*8000} for i in range(40)]
        sizes = []
        def review(topics, batch, model, effort):
            sizes.append(len(batch))
            return [{'id':s['id'],'topic':None} for s in batch]
        with patch.object(routing, '_semantic_decisions', side_effect=review):
            result = routing.semantic_decisions([], stories)
        self.assertEqual([r['id'] for r in result], list(range(40)))
        self.assertEqual(sizes, [10,10,10,10])

    def test_embedding_recovers_from_transient_connection_failure(self):
        vector=b'\0'*(embed.DIM*4)
        response=Mock(status_code=200)
        response.json.return_value={'data':[{'index':0,'embedding':base64.b64encode(vector).decode()}]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(embed.requests,'post',
                side_effect=[requests.ConnectionError('DNS failed'),response]) as request,patch.object(embed.time,'sleep'):
            self.assertEqual(embed.embed_batch(['Subject']),[vector])
            self.assertEqual(request.call_count,2)

    def test_semantic_request_recovers_without_losing_model_options(self):
        response=Mock(ok=True)
        response.json.return_value={'choices':[{'message':{'content':'{"ok":true}'}}]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(llm.requests,'post',
                side_effect=[requests.Timeout('timed out'),response]) as request,patch.object(llm.time,'sleep'):
            self.assertEqual(llm.ask_json('Prompt',model='gpt-6-luna',reasoning_effort='low'),{'ok':True})
            self.assertEqual(request.call_count,2)
            self.assertEqual(request.call_args.kwargs['json']['reasoning_effort'],'low')
