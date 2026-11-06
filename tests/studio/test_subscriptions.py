import httpx
import pytest

from deeptutor.studio import discovery, subscriptions
from deeptutor.studio.database import Conflict, Studio


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", "a")


@pytest.mark.asyncio
async def test_rss_snapshots_deduplicate_seen_entries_and_failures_are_not_zero(store):
    row = subscriptions.save(
        store,
        subscriptions.SubscriptionInput(
            title="Fixture", kind="rss", url="https://example.org/feed.xml"
        ),
    )
    feed = b"<rss><channel><item><guid>entry-1</guid><title>Paper</title><link>https://example.org/paper</link></item></channel></rss>"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=feed))
    first = await subscriptions.refresh(store, row["id"], transport=transport)
    assert first["data"]["new_ids"] == ["entry-1"]
    first_run = store.get(first["data"]["last_discovery_id"])
    assert store.blob_path(first_run["data"]["pages"][0]["raw_hash"]).read_bytes() == feed
    second = await subscriptions.refresh(store, row["id"], transport=transport)
    assert second["data"]["new_ids"] == []
    assert second["data"]["last_discovery_id"] != first["data"]["last_discovery_id"]
    failed = await subscriptions.refresh(
        store, row["id"], transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    assert failed["data"]["last_status"] == "failed"
    assert store.get(first_run["id"])["data"]["pages"][0]["items"][0]["id"] == "entry-1"
    collected = discovery.collect(store, first_run["id"], "rss", "entry-1")
    repeated = discovery.collect(store, first_run["id"], "rss", "entry-1")
    assert collected["edition_id"] == repeated["edition_id"]
    assert len(store.list("work")) == 1
    assert collected["provenance"]["data"]["query"] == "Fixture"


@pytest.mark.asyncio
async def test_disabled_subscription_never_auto_fetches_and_toggle_is_cas(store, monkeypatch):
    command = subscriptions.SubscriptionInput(title="HOI", query="human-object interaction")
    row = subscriptions.save(store, command)
    calls = []

    async def refresh(store, identifier, **kwargs):
        calls.append(identifier)

    monkeypatch.setattr(subscriptions, "refresh", refresh)
    await subscriptions.run_due(store)
    assert calls == []
    enabled = subscriptions.save(
        store, command.model_copy(update={"enabled": True}), row["id"], row["revision"]
    )
    with pytest.raises(Conflict):
        subscriptions.save(store, command, row["id"], row["revision"])
    await subscriptions.run_due(store)
    assert calls == [enabled["id"]]


def test_feed_parsing_never_renders_external_markup_or_embedded_entities():
    value = subscriptions.parse_feed(
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>x</id><title>&lt;script&gt;hi&lt;/script&gt;</title><link href="javascript:alert(1)"/></entry></feed>'
    )
    assert value[0]["url"] == ""
    with pytest.raises(Exception):
        subscriptions.parse_feed(
            b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&x;</title></item></channel></rss>'
        )


@pytest.mark.asyncio
async def test_saved_search_preserves_filters_pause_keeps_inflight_and_due_rechecks(store):
    command = subscriptions.SubscriptionInput(
        title="HOI 2020–2024",
        query="HOI",
        providers=["crossref"],
        order="newest",
        year_from=2020,
        year_to=2024,
        enabled=True,
    )
    row = subscriptions.save(store, command)

    def handler(request):
        assert request.url.params["filter"] == "from-pub-date:2020-01-01,until-pub-date:2024-12-31"
        current = store.get(row["id"])
        paused = subscriptions.save(
            store, command.model_copy(update={"enabled": False}), row["id"], current["revision"]
        )
        assert paused["data"]["inflight_at"]
        with pytest.raises(Conflict):
            subscriptions.save(
                store, command.model_copy(update={"year_to": 2025}), row["id"], paused["revision"]
            )
        return httpx.Response(200, json={"message": {"items": [], "total-results": 0}})

    result = await subscriptions.refresh(store, row["id"], transport=httpx.MockTransport(handler))
    assert result["data"]["enabled"] is False
    assert result["data"]["inflight_at"] is None
    assert (await subscriptions.refresh(store, row["id"], only_due=True))["revision"] == result[
        "revision"
    ]
