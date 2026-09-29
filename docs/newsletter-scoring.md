# Newsletter shortlist scoring: four smoke tests

I compared four mixes of Hacker News upvotes and comments on four populated topic pools. The window was the complete Sunday issue from **20 September 2026, 16:00 UTC (exclusive)** through **27 September 2026, 16:00 UTC (inclusive)**, corresponding to 18:00 Stockholm time. The database snapshot was current through 28 September. Each pool contained live, nondeleted stories already assigned to its topic. No topic-relevance score or minimum engagement threshold was applied.

For each candidate, `upvotes = max(HN score - 1, 0)` and `comments = max(descendants, 0)`. The smoke score was `upvote_weight * log1p(upvotes) + comment_weight * log1p(comments)`, descending, with story ID as the tie-breaker. Logarithms keep a single viral post from dominating the scale; the formula remains a simple ordering heuristic, not a measure of editorial quality. The comparisons below are top-15 shortlists before fetching article pages, URL/title deduplication, and LLM selection.

| Topic | Pool | 90/10 vs 80/20 | 70/30 vs 80/20 | 60/40 vs 80/20 |
| --- | ---: | ---: | ---: | ---: |
| AI governance and societal impacts | 221 | 15 shared | 14 shared | 14 shared |
| LLMs and AI applications | 188 | 14 shared | 15 shared | 14 shared |
| AI assistants and agents | 133 | 14 shared | 14 shared | 14 shared |
| Apple ecosystem | 89 | 15 shared | 15 shared | 14 shared |

The useful differences were at the cutoff:

- In LLMs, **80/20 admitted “Paul Graham on LLMs ‘Thinking’”** (12 points, 30 comments) where 90/10 admitted a model-version comparison (18 points, 1 comment). Discussion helped surface a candidate worth examining.
- In AI governance, 70/30 and 60/40 admitted **“If you start writing today, there's no way to know if you can write without AI”** (17 points, 25 comments) in place of **“Big AI to humanity: drop dead”** (44 points, 0 comments). The discussion signal looks helpful in this pair.
- In AI agents, 70/30 and 60/40 admitted **“Agent manager now with mouse support”** (5 points, 2 comments) in place of **“AI-CAD: An OSS Multi-Agent Harness for Mech. Eng. CAD”** (7 points, 0 comments). This is too little evidence to treat the extra comments as a quality improvement.
- In Apple, only 60/40 changed membership: **“Ask HN: Do You Use Siri?”** (5 points, 10 comments) displaced an older iPhone press-conference video (14 points, 0 comments). An editorial model should make that final judgment.

**Recommendation: start at 80% upvotes / 20% comments.** It gives discussion enough influence to rescue an overlooked candidate in the LLM pool while preserving more of the upvote-supported shortlist than 60/40. Four topic weeks do not establish an optimal weight. Inspect the eventual five-story outputs, especially when a highly discussed but weakly voted candidate enters the shortlist, before tuning further.

Live preparation check (28 September): the Apple pool produced 15 candidates,
15 completed page requests, 13 usable overviews, and five final stories with
nonempty overviews. The ranked output included Apple Intelligence controls,
browser-emulated Copland, an iPod NAND upgrade, Linux on the M4 Mac mini, and iOS
service promotions. Main technical details were checked against the fetched
excerpts. Two source pages yielded no readable content; the pipeline leaves their
overviews empty. One oversized page exposed a fetch issue, fixed by retaining a
bounded excerpt instead of discarding the page. The tone prompt was also adjusted
to avoid formulaic “This covers” openings. LLM wording still merits editorial
review: the Copland overview's “first time” wording was broader than the source's
more specific first availability in emulation.

Local review artifacts are in `report/newsletter-smoke/`: `issue.html`,
`issue.json`, `candidates.json`, and `sources.json`. Nothing was sent or deployed.
