# Worker feed proposal — superseded by the simpler implementation

The implemented version cuts the original four-table proposal to two additions:
a single private D1 catalog row and persistent user/article state. It retains
server-selected unseen stories, cursor pagination, daily updates, email profiles,
and cross-device history.

See [the implemented architecture](personalized-feed-architecture.md) and
[setup and rollout](../README.md#personalized-daily-feed).
