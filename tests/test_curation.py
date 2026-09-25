import unittest
import numpy as np
import curation


class CuratedRoutingTests(unittest.TestCase):
    def test_two_centers_in_one_subscription_do_not_compete_for_margin(self):
        model=dict(mean=np.zeros(2),prototype_centroids=np.array([[1.,0.],[.999,.01],[0.,1.]]),prototype_topic_ids=np.array([1001,1001,1002]))
        labels,fit,margin=curation.classify(np.array([[1.,0.]]),model)
        self.assertEqual(labels.tolist(),[1001])
        self.assertGreater(margin[0],.9)
        self.assertAlmostEqual(fit[0],1.)

    def test_noncontiguous_ids_and_unsorted_prototypes_route_correctly(self):
        model=dict(mean=np.zeros(2),prototype_centroids=np.array([[0.,1.],[1.,0.],[.1,.9]]),prototype_topic_ids=np.array([1700,1004,1700]))
        labels,_,_=curation.classify(np.array([[1.,0.],[0.,1.]]),model)
        self.assertEqual(labels.tolist(),[1004,1700])

    def test_retired_source_can_alias_without_retaining_its_bad_center(self):
        plan=dict(groups=[dict(id=1000,source_ids=[0,1],prototype_ids=[0],name='A',description='A'),dict(id=1002,source_ids=[2],prototype_ids=[2],name='B',description='B')])
        curation.validate_plan(plan,3)
        plan['groups'][1]['source_ids'].append(1)
        with self.assertRaises(ValueError):curation.validate_plan(plan,3)

    def test_a_group_cannot_claim_another_groups_center(self):
        plan=dict(groups=[dict(id=1000,source_ids=[0],prototype_ids=[1],name='A',description='A'),dict(id=1001,source_ids=[1],prototype_ids=[0],name='B',description='B')])
        with self.assertRaises(ValueError):curation.validate_plan(plan,2)

    def test_editorial_correction_keeps_real_scores_and_is_batch_local(self):
        model=dict(mean=np.zeros(2),prototype_centroids=np.array([[1.,0.],[0.,1.]]),prototype_topic_ids=np.array([1001,1002]))
        edits=[dict(id=42,topic=1002)]
        labels,fit,margin,accepted=curation.route([42,43],np.array([[1.,0.],[1.,0.]]),model,edits)
        self.assertEqual(labels.tolist(),[1002,1001])
        self.assertEqual(accepted.tolist(),[True,True])
        self.assertEqual(float(fit[0]),0.)
        self.assertEqual(float(margin[0]),-1.)
