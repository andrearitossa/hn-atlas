"""Static curated taxonomies: preserve multiple subject centers per subscription."""
import numpy as np
from core import unit


def validate_plan(plan, count):
    groups=plan['groups']
    ids=[g['id'] for g in groups]
    sources=[i for g in groups for i in g['source_ids']]
    prototypes=[i for g in groups for i in g['prototype_ids']]
    if len(ids)!=len(set(ids)) or sorted(sources)!=list(range(count)):
        raise ValueError('Each source topic must have exactly one curated destination')
    if len(prototypes)!=len(set(prototypes)) or any(not 0<=i<count for i in prototypes):
        raise ValueError('Invalid or duplicate prototype')
    if any(target not in ids for target in plan.get('legacy_aliases',{}).values()):
        raise ValueError('Legacy alias target must be active')
    decisions=plan.get('story_overrides',[])
    if len({d['id'] for d in decisions})!=len(decisions) or any(d['topic'] not in ids for d in decisions):
        raise ValueError('Invalid editorial decisions')
    for g in groups:
        if not g['prototype_ids'] or not set(g['prototype_ids'])<=set(g['source_ids']):
            raise ValueError('Each group needs its own retained evidence prototypes')
        if not g['name'].strip() or not g['description'].strip():
            raise ValueError('A curated topic needs a name and description')


def classify(vectors, model):
    """Margin is between subscriptions, never two prototypes of the same topic."""
    owners=np.asarray(model['prototype_topic_ids'])
    order=np.argsort(owners,kind='stable')
    ids,starts=np.unique(owners[order],return_index=True)
    if len(ids)<2:
        raise ValueError('At least two subscriptions are required')
    scores=unit(vectors-model['mean']) @ model['prototype_centroids'][order].T
    grouped=np.maximum.reduceat(scores,starts,axis=1)
    top=grouped.argmax(1)
    fit=grouped[np.arange(len(vectors)),top].copy()
    grouped[np.arange(len(vectors)),top]=-np.inf
    margin=fit-grouped.max(1)
    return ids[top],fit,margin


def route(ids, vectors, model, overrides=()):
    """Apply explicit editorial decisions, retaining their actual vector scores."""
    labels,fit,margin=classify(vectors,model)
    accepted=(fit>=float(model.get('min_fit',.2862)))&(margin>=float(model.get('min_margin',.02)))
    if overrides:
        positions={int(ident):i for i,ident in enumerate(ids)}
        for decision in overrides:
            row=positions.get(decision['id'])
            if row is None:continue
            target=decision['topic'];members=model['prototype_topic_ids']==target
            if not members.any():raise ValueError('Editorial target is not an active topic')
            scores=unit(vectors[row]-model['mean'])@model['prototype_centroids'].T
            labels[row]=target;fit[row]=scores[members].max();margin[row]=fit[row]-scores[~members].max()
            accepted[row]=True
    return labels,fit,margin,accepted
