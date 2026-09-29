import sqlite3
import unittest
from unittest.mock import patch

import numpy as np

import catalog


class TopicActivityTests(unittest.TestCase):
    def test_windows_and_tombstones_in_shared_overview(self):
        c = sqlite3.connect(':memory:')
        self.addCleanup(c.close)
        c.row_factory = sqlite3.Row
        c.executescript('''
            CREATE TABLE stories(id INTEGER PRIMARY KEY,time INTEGER,dead INTEGER,deleted INTEGER);
            CREATE TABLE story_topics(id INTEGER PRIMARY KEY,topic INTEGER);
            CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT,description TEXT,size INTEGER,x REAL,y REAL);
            CREATE TABLE topic_registry(id INTEGER PRIMARY KEY,status TEXT);
            INSERT INTO topics VALUES(1,'A','',0,0,0),(2,'B','',0,0,0);
            INSERT INTO topic_registry VALUES(1,'active'),(2,'active');
        ''')
        now = 1800000000
        # The seven-day boundary is inclusive. Future/dead/deleted rows count nowhere.
        for ident, topic, age, dead, deleted in [
            (1, 1, 0, 0, 0), (2, 1, 7, 0, 0),
            (3, 1, 35, 0, 0), (4, 2, 8, 0, 0),
            (5, 1, 36, 0, 0), (6, 1, -1, 0, 0),
            (7, 1, 1, 1, 0), (8, 1, 1, 0, 1),
        ]:
            c.execute('INSERT INTO stories VALUES(?,?,?,?)', (ident, now-age*catalog.DAY, dead, deleted))
            c.execute('INSERT INTO story_topics VALUES(?,?)', (ident, topic))
        with patch.object(catalog.time, 'time', return_value=now), patch.object(
                catalog.topics, 'model', return_value=(None, np.array([1, 2]), np.eye(2))):
            result = catalog.overview(c)
        topic = next(t for t in result['topics'] if t['id'] == 1)
        self.assertEqual(result['as_of'], now)
        self.assertEqual(topic['last_7d'], 2)
        self.assertEqual(topic['last_30d'], 2)
        self.assertNotIn('momentum', topic)
        self.assertNotIn('rising_score', topic)
        self.assertNotIn('is_rising', topic)
