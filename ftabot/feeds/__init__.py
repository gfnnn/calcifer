"""Feed consumers for the integration hub.

Each source turns an external feed (Wowhead RSS, Raider.IO, Raid-Helper, …) into
normalized :class:`~ftabot.feeds.base.FeedEvent` objects. The runner dedups them
against the ``seen_items`` table, the router decides where each goes, and the
publisher renders + posts them in Calcifer's voice.
"""

from .base import FeedEvent, FeedSource

__all__ = ["FeedEvent", "FeedSource"]
