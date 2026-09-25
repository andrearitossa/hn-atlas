"""Measure observed map changes; these diagnostics are not quality labels."""
from collections import Counter
import json
from pathlib import Path

import numpy as np


def measure(out=Path('data/comparison-2020')):
    progress=json.loads((out/'stream-progress.json').read_text())
    if not progress.get('complete'):
        raise ValueError('Wait for the full replay')
    directory=out/'maps'
    paths=[directory/'stream-initial.json',*sorted(directory.glob('week-*.json'))]
    frames=[json.loads(p.read_text()) for p in paths]
    weeks=[json.loads(line) for line in (out/'weeks.jsonl').read_text().splitlines()]
    assert len(frames)==len(weeks)+1
    records=[];counts=Counter();births={};retirements={}
    for previous,current,week in zip(frames,frames[1:],weeks):
        a={n['id']:n for n in previous['nodes']};b={n['id']:n for n in current['nodes']}
        added=set(b)-set(a);removed=set(a)-set(b);common=set(a)&set(b)
        renamed=[dict(id=i,before=a[i]['name'],after=b[i]['name']) for i in sorted(common)
                 if a[i]['name']!=b[i]['name'] or a[i]['description']!=b[i]['description']]
        for i in added:
            births[i]=current['at']
        for i in removed:
            retirements[i]=current['at']
        kinds=Counter(e[0] for e in week['events']);counts.update(kinds)
        assert len(b)-len(a)==kinds['birth']+kinds['split']-kinds['merge']
        records.append(dict(date=week['date'],active=len(b),events=week['events'],
            added=[b[i]['name'] for i in sorted(added)],removed=[a[i]['name'] for i in sorted(removed)],
            renamed=renamed,seconds=week['seconds']))
    initial={n['id']:n for n in frames[0]['nodes']};final={n['id']:n for n in frames[-1]['nodes']}
    first=np.load(directory/'stream-initial.npz');last=np.load(paths[-1].with_suffix('.npz'))
    c0=dict(zip(first['ids'].tolist(),first['centers']));c1=dict(zip(last['ids'].tolist(),last['centers']))
    drift=[]
    for ident in sorted(set(initial)&set(final)):
        before=initial[ident];after=final[ident]
        neighbors0={n['id'] for n in before['neighbors']};neighbors1={n['id'] for n in after['neighbors']}
        drift.append(dict(id=ident,initial=before['name'],final=after['name'],
            boundary_changed=before['name']!=after['name'] or before['description']!=after['description'],
            center_cosine=float(c0[ident]@c1[ident]),
            neighbors_retained=len(neighbors0&neighbors1)/max(1,len(neighbors0))))
    durations=np.array([w['seconds'] for w in weeks])
    data=dict(initial_topics=len(initial),final_topics=len(final),weeks=len(weeks),event_counts=dict(counts),
        weeks_without_structural_changes=sum(not w['events'] for w in weeks),
        initial_ids_still_active=len(set(initial)&set(final)),
        added_ids=len(births),added_ids_still_active=len(set(births)&set(final)),
        added_ids_retired_within_28_days=sum(i in retirements and retirements[i]-at<=28*86400 for i,at in births.items()),
        weekly_seconds=dict(median=float(np.median(durations)),p95=float(np.quantile(durations,.95)),total=float(durations.sum())),
        retained_topic_drift=sorted(drift,key=lambda r:r['center_cosine']),timeline=records,
        note='Stable IDs, centroid movement, and neighbor retention describe evolution, not semantic quality. Early-retirement counts are censored for recently born topics.')
    path=out/'map-evolution-metrics.json';path.write_text(json.dumps(data,indent=2))
    print(json.dumps({k:v for k,v in data.items() if k not in ('timeline','retained_topic_drift')},indent=2))
    return data


if __name__=='__main__':
    measure()
