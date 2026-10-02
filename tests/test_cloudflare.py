import os
import unittest
from unittest.mock import Mock, patch
import requests
from cloudflare import Cloudflare


class CloudflareTests(unittest.TestCase):
    def client(self):
        with patch.dict(os.environ, {'CLOUDFLARE_API_TOKEN': 'test'}):
            return Cloudflare(dict(account_id='a', database_id='d', index_name='v'))

    def test_parameterized_query_checks_results_and_accounts_for_writes(self):
        cloud = self.client()
        with patch.object(cloud, 'api', return_value=[dict(success=True, results=[{'id': 1}], meta={'rows_written': 2})]) as api:
            self.assertEqual(cloud.d1_query('SELECT ?', [1]), [{'id': 1}])
            self.assertEqual(api.call_args.kwargs['json'], {'sql': 'SELECT ?', 'params': [1]})
            self.assertEqual(cloud.written, 2)
        with patch.object(cloud, 'api', return_value=[dict(success=False)]):
            with self.assertRaises(RuntimeError):
                cloud.sql('SELECT 1')

    def test_transient_api_errors_retry_and_vector_mutations_are_retained(self):
        cloud = self.client()
        response = Mock(ok=True)
        response.json.return_value = dict(success=True, result={'mutationId': 'mutation'})
        with patch.object(cloud.session, 'request', side_effect=[requests.Timeout(), response]) as request, patch('cloudflare.time.sleep'):
            cloud.delete_vectors(['1'])
            self.assertEqual(request.call_count, 2)
            self.assertEqual(cloud.last_mutation, 'mutation')
        with patch.object(cloud, 'api', return_value={'mutationId': 'upsert'}):
            cloud.upsert([dict(id='1', values=[1])])
            self.assertEqual(cloud.last_mutation, 'upsert')
