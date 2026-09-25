"""Map update behavior: stable links, coherent evidence, retryable transactions."""
import os
from unittest.mock import patch
import numpy as np
import production
import routing
import structure
import weekly
import unittest
from tests import test_weekly


class DiscoveryTests(unittest.TestCase):
    setUp = test_weekly.WeeklyTests.setUp
    story = test_weekly.WeeklyTests.story
    def packet(self):
        return dict(topics=[dict(id=i,name=f'Topic {i}') for i in (0,1)],groups=[dict(titles=[dict(id=i,title=f'Subject {i}',time=(i-1)*7*86400) for i in range(1,5)]),dict(titles=[dict(id=i,title=f'Subject {i}',time=(i-5)*7*86400) for i in range(5,9)])])

    def subject(self, ident, groups, name='Concrete subject'):
        return dict(id=ident,groups=groups,name=name,description='A clear subject boundary',
                    why_not_existing='A durable broad reader interest that the existing directory does not accommodate.',
                    anchors=[i for g in groups for i in range(g*4+1,g*4+5)])

    def test_automatic_split_is_a_logged_noop_without_damaging_subscription(self):
        packet=self.packet();packet['policy']=dict(new_topics_allowed=True)
        change=dict(topics=[0],subjects=[self.subject(0,[]),self.subject(None,[0],'Product subtype')])
        accepted,rejected=structure.accepted_changes(dict(changes=[change]),packet)
        self.assertEqual(accepted,[])
        self.assertTrue(rejected[0]['policy'])

    def test_new_interest_requires_durable_evidence_and_available_growth_allowance(self):
        packet=self.packet();packet['policy']=dict(new_topics_allowed=True)
        change=dict(topics=[],subjects=[self.subject(None,[0])])
        self.assertEqual(structure.accepted_changes(dict(changes=[change]),packet)[0],[change])
        packet['policy']['new_topics_allowed']=False
        self.assertEqual(structure.accepted_changes(dict(changes=[change]),packet)[0],[])
        packet['policy']['new_topics_allowed']=True
        packet['groups'][0]['titles'][-1]['time']=10*86400
        self.assertEqual(structure.accepted_changes(dict(changes=[change]),packet)[0],[])

    def test_growth_allowance_counts_merged_births_and_reopens_after_90_days(self):
        self.assertTrue(structure.growth_policy(self.conn,self.now)['new_topics_allowed'])
        self.conn.execute("INSERT INTO topic_registry(id,centroid,born,status) VALUES (9,?,?,'merged')",
                          (np.ones(3,dtype='f4').tobytes(),self.now))
        self.assertFalse(structure.growth_policy(self.conn,self.now+89*86400)['new_topics_allowed'])
        self.assertTrue(structure.growth_policy(self.conn,self.now+90*86400)['new_topics_allowed'])

    def apply_plan(self, changes):
        rows,x=production.recent_vectors(self.conn,0)
        groups=[np.arange(4),np.arange(4,8)]
        packet=self.packet()
        structure.validate(dict(changes=changes),packet)
        with self.conn:
            return structure.apply(self.conn,changes,groups,rows,x,self.now)

    def test_merge_keeps_old_links_and_subscription_tokens(self):
        for i in range(1,9):self.story(i,0 if i<5 else 1,[1,0,0])
        self.conn.execute("INSERT INTO subscriptions VALUES ('token','x@example.com',1,'weekly',1,0,0)")
        self.conn.commit()
        events=self.apply_plan([dict(topics=[0,1],subjects=[self.subject(0,[0,1])])])
        self.assertIn(('merge',1,0),events)
        self.assertEqual(production.resolve_topic(self.conn,1),0)
        self.assertEqual(self.conn.execute('SELECT token,topic FROM subscriptions').fetchone(),('token',1))
        self.assertEqual(self.conn.execute('SELECT DISTINCT topic FROM story_topics').fetchall(),[(0,)])

    def test_split_reassigns_history_without_copying_subscriptions(self):
        for i in range(1,9):self.story(i,0,[1,0,0] if i<5 else [0,0,1])
        self.conn.execute("INSERT INTO subscriptions VALUES ('token','x@example.com',0,'weekly',1,0,0)")
        self.conn.commit()
        self.apply_plan([dict(topics=[0],subjects=[self.subject(0,[0],'Retained subject'),self.subject(None,[1],'New subject')])])
        self.assertEqual(self.conn.execute('SELECT id,topic FROM story_topics ORDER BY id').fetchall(),[(i,0 if i<5 else 2) for i in range(1,9)])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM subscriptions').fetchone()[0],1)

    def test_stale_name_can_change_without_rotating_id(self):
        self.story(1,0,[1,0,0]);self.story(2,0,[1,0,0])
        before=self.conn.execute('SELECT centroid FROM topic_registry WHERE id=0').fetchone()
        self.apply_plan([dict(topics=[0],subjects=[self.subject(0,[],'Sports and Athletics')])])
        self.assertEqual(self.conn.execute('SELECT name FROM topics WHERE id=0').fetchone()[0],'Sports and Athletics')
        self.assertEqual(self.conn.execute('SELECT centroid FROM topic_registry WHERE id=0').fetchone(),before)

    def test_format_duplicate_and_invalid_id_plans_are_rejected(self):
        for subjects,old in [([self.subject(None,[0],'Year-End 2024 Recaps')],[]),
                             ([self.subject(None,[0],'Topic 1')],[]),
                             ([self.subject(99,[0])],[0]),
                             ([self.subject(None,[])],[]),
                             ([self.subject(0,[0]),self.subject(None,[0],'Other')],[0])]:
            with self.subTest(subjects=subjects),self.assertRaises(ValueError):
                structure.validate(dict(changes=[dict(topics=old,subjects=subjects)]),self.packet())

    def test_weak_reassignment_removes_old_public_membership(self):
        self.story(1,0,[1,0,0])
        routing.store(self.conn,[(1,0,.1,.001)],self.now)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM classification_queue').fetchone()[0],1)

    def test_all_stories_are_discovery_evidence_even_when_confidently_assigned(self):
        for i in range(60):
            self.story(i+1,0,[1,0,0],age=1 if i<30 else 10)
        rows,x=production.recent_vectors(self.conn,0)
        packet,groups=structure.evidence(self.conn,rows,x)
        self.assertEqual(len(groups),1)
        self.assertEqual(packet['groups'][0]['count'],60)
        self.conn.execute('UPDATE stories SET time=?',(self.now,))
        rows,x=production.recent_vectors(self.conn,0)
        self.assertEqual(structure.evidence(self.conn,rows,x)[1],[])

    def test_weekly_checkpoint_and_changes_rollback_together(self):
        self.story(1,0,[1,0,0])
        self.conn.execute("INSERT INTO maintenance VALUES ('weekly',?)",(self.now-8*86400,));self.conn.commit()
        def broken(*args):
            self.conn.execute("UPDATE topics SET name='Broken' WHERE id=0")
            raise ValueError('Invalid plan')
        with patch.object(structure,'maintain',side_effect=broken):
            with self.assertRaises(ValueError):weekly.maintain(self.conn,self.now)
        self.assertEqual(self.conn.execute('SELECT name FROM topics WHERE id=0').fetchone()[0],'Topic 0')
        with patch.object(structure,'maintain',return_value=[]) as review:
            weekly.maintain(self.conn,self.now)
            weekly.maintain(self.conn,self.now+60)
            self.assertEqual(review.call_count,1)

    def test_missing_key_keeps_week_retryable(self):
        self.story(1,0,[1,0,0])
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}),patch.object(structure,'evidence',return_value=(self.packet(),[np.array([0])])):
            with self.assertRaises(RuntimeError):weekly.maintain(self.conn,self.now)
        self.assertIsNone(self.conn.execute("SELECT value FROM maintenance WHERE key='weekly'").fetchone())

    def test_initial_naming_batches_and_keeps_duplicate_evidence(self):
        import topics
        def reply(prompt):
            import json
            batch=json.loads(prompt.split('\n',1)[1])
            return dict(subjects=[dict(id=r['id'],is_subject=True,name='Rust',description='Rust language') for r in batch])
        with patch.object(topics,'ask_json',side_effect=reply) as ask:
            named=topics.name_subjects([['Rust story']]*85)
        self.assertEqual(len(named),85)
        self.assertEqual(ask.call_count,3)

    def test_anchor_titles_must_come_from_supplied_evidence(self):
        subject=self.subject(None,[0]);subject['anchors']=['invented']*4
        with self.assertRaises(ValueError):
            structure.validate(dict(changes=[dict(topics=[],subjects=[subject])]),self.packet())

    def test_invalid_independent_change_does_not_block_valid_discovery(self):
        for i in range(1,9):self.story(i,0,[0,0,1] if i<5 else [1,0,0])
        valid=dict(topics=[],subjects=[self.subject(None,[0],'A new subject')])
        invalid=dict(topics=[99],subjects=[self.subject(99,[],'Unknown subject')])
        packet=self.packet();groups=[np.arange(4),np.arange(4,8)]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}), \
             patch.object(structure,'evidence',return_value=(packet,groups)), \
             patch.object(structure,'ask_json',return_value=dict(changes=[invalid,valid])) as ask:
            events=weekly.maintain(self.conn,self.now)
            self.assertIn(('birth',2),events)
            self.assertEqual(weekly.maintain(self.conn,self.now+1),[])
            self.assertEqual(ask.call_count,1)
        import json
        record=json.loads(self.conn.execute("SELECT details FROM topic_changes WHERE kind='review'").fetchone()[0])
        self.assertEqual(len(record['rejected']),1)
        self.assertEqual(self.conn.execute('SELECT topic FROM story_topics WHERE id=1').fetchone()[0],2)

    def test_entirely_invalid_review_remains_retryable(self):
        bad=dict(topics=[99],subjects=[self.subject(99,[])])
        with self.assertRaises(ValueError):
            structure.accepted_changes(dict(changes=[bad]),self.packet())

    def test_new_boundary_floor_does_not_change_legacy_topic_floor(self):
        self.story(1,0,[1,0,0]);self.story(2,1,[0,1,0]);self.story(3,1,[0,1,0])
        self.conn.execute('UPDATE topic_registry SET min_fit=? WHERE id=1',(routing.BOUNDARY_FIT,))
        routing.store(self.conn,[(1,0,.35,.1),(2,1,.35,.1),(3,1,.5,.1)],self.now)
        self.assertEqual(self.conn.execute('SELECT id FROM story_topics ORDER BY id').fetchall(),[(1,),(3,)])

    def test_legacy_registry_migration_preserves_ids_and_centers(self):
        before=self.conn.execute('SELECT id,centroid FROM topic_registry ORDER BY id').fetchall()
        self.conn.execute('ALTER TABLE topic_registry DROP COLUMN min_fit');self.conn.commit()
        production.setup(self.conn,self.now)
        self.assertEqual(self.conn.execute('SELECT id,centroid FROM topic_registry ORDER BY id').fetchall(),before)
        self.assertEqual(self.conn.execute('SELECT DISTINCT min_fit FROM topic_registry').fetchall(),[(routing.MIN_FIT,)])
