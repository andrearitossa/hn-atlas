import tempfile
import unittest
from unittest.mock import patch

import requests

import hn_sync


class RuntimeTests(unittest.TestCase):
    def test_first_sync_retry_keeps_original_checkpoint_after_committed_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = hn_sync.open_db(f'{directory}/hn.db')
            hn_sync.save_articles(conn, [dict(id=100, type='story', title='Baseline')])
            conn.commit()
            class Pool:
                def map(self, fn, ids):
                    return map(fn, ids)
            def item(ident):
                if ident == 102:
                    raise requests.ConnectionError('interrupted')
                return dict(id=ident, type='story', title=str(ident))
            with patch.object(hn_sync, 'get_json', return_value=104), patch.object(hn_sync, 'fetch_item', side_effect=item):
                with self.assertRaises(requests.ConnectionError):
                    hn_sync.catch_up(conn, Pool(), chunk=2)
            self.assertEqual(conn.execute('SELECT max(id) FROM stories').fetchone()[0], 104)
            self.assertEqual(hn_sync.get_state(conn, 'high'), 100)
            with patch.object(hn_sync, 'get_json', return_value=104), patch.object(hn_sync, 'fetch_item',
                    side_effect=lambda ident: dict(id=ident, type='story', title=str(ident))) as fetch:
                hn_sync.catch_up(conn, Pool(), chunk=2)
            self.assertEqual({call.args[0] for call in fetch.call_args_list}, {101, 102, 103, 104})
            self.assertEqual(hn_sync.get_state(conn, 'high'), 104)
            conn.close()

    def test_sync_failure_does_not_advance_high_water_mark(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = hn_sync.open_db(f'{directory}/hn.db')
            hn_sync.set_state(conn, 'high', 100)
            conn.commit()
            with patch.object(hn_sync, 'get_json', side_effect=lambda path: 102 if path == 'maxitem' else None), \
                 patch.object(hn_sync, 'fetch_item', side_effect=requests.ConnectionError('offline')):
                with self.assertRaises(requests.ConnectionError):
                    with __import__('concurrent.futures').futures.ThreadPoolExecutor(2) as pool:
                        hn_sync.catch_up(conn, pool, chunk=2)
            self.assertEqual(hn_sync.get_state(conn, 'high'), 100)
            conn.close()



if __name__ == '__main__':
    unittest.main()
