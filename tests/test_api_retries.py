import base64
import os
import unittest
from unittest.mock import Mock,patch

import requests

import embed
import llm


class ApiRetryTests(unittest.TestCase):
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
