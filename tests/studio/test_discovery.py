import json

import httpx
import pytest

from deeptutor.studio import discovery
from deeptutor.studio.database import Conflict, Studio


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", "a")


@pytest.mark.asyncio
async def test_crossref_pages_preserve_exact_queries_raw_snapshots_and_cursors(store):
    calls = []

    def handle(request):
        params = dict(request.url.params)
        calls.append(params)
        assert "cursor" not in params
        first = params["offset"] == "0"
        return httpx.Response(
            200,
            json={
                "message": {
                    "total-results": 3,
                    "next-cursor": "opaque-next" if first else None,
                    "items": [
                        {
                            "DOI": f"10.1234/{i}",
                            "title": [f"Paper {i}"],
                            "URL": f"https://doi.org/10.1234/{i}",
                        }
                        for i in ([1, 2] if first else [3])
                    ],
                }
            },
        )

    transport = httpx.MockTransport(handle)
    run = await discovery.search(
        store, "HOI", ["crossref"], limit=2, order="newest", year_from=2020, transport=transport
    )
    first_hash = run["data"]["pages"][0]["raw_hash"]
    assert run["data"]["status"] == "partial"
    complete = await discovery.next_page(store, run["id"], "crossref", run["revision"], transport)
    assert complete["data"]["status"] == "complete"
    assert complete["data"]["pages"][0]["raw_hash"] == first_hash
    assert (
        json.loads(store.blob_path(first_hash).read_bytes())["message"]["items"][0]["DOI"]
        == "10.1234/1"
    )
    assert calls[1]["offset"] == "2"
    assert calls[1]["filter"] == "from-pub-date:2020-01-01"
    assert calls[1]["sort"] == "published"
    with pytest.raises(Conflict):
        await discovery.next_page(store, run["id"], "crossref", run["revision"], transport)


@pytest.mark.asyncio
async def test_zero_rate_limit_failed_and_authorization_are_distinct(store, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "TEST_KEY_NEVER_PERSISTED")
    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    run = await discovery.search(store, "HOI", ["openalex"], transport=transport)
    assert run["data"]["status"] == "failed"
    assert run["data"]["pages"][0]["status"] == "authorization_required"
    assert "TEST_KEY" not in json.dumps(run)
    rate = await discovery.search(
        store,
        "HOI",
        ["crossref"],
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"Retry-After": "300"})
        ),
    )
    assert rate["data"]["pages"][0]["status"] == "rate_limited"
    with pytest.raises(ValueError, match="retry delay"):
        await discovery.next_page(store, rate["id"], "crossref", rate["revision"], transport)
    zero = await discovery.search(
        store,
        "HOI",
        ["crossref"],
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": {"total-results": 0, "items": []}})
        ),
    )
    assert zero["data"]["status"] == "complete"
    assert zero["data"]["pages"][0]["items"] == []
    invalid = await discovery.search(
        store,
        "HOI",
        ["arxiv"],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<unclosed>")),
    )
    assert invalid["data"]["pages"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_arxiv_paging_and_version_are_preserved(store):
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>2</o:totalResults><entry><id>http://arxiv.org/abs/2103.05399v2</id><title>QPIC</title><published>2021-03-01</published><author><name>A</name></author></entry></feed>"""
    result = await discovery.search(
        store,
        "cat:cs.CV",
        ["arxiv"],
        limit=1,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=xml)),
    )
    page = result["data"]["pages"][0]
    assert page["items"][0]["arxiv"] == "2103.05399v2"
    assert page["next_cursor"] == "1"
    assert page["query"]["search_query"] == "cat:cs.CV"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,payload",
    [
        ("crossref", {}),
        ("crossref", {"message": {"items": [{"published": {"date-parts": [[]]}}]}}),
        ("openalex", {"results": None}),
        ("openalex", []),
    ],
)
async def test_malformed_provider_output_is_failure_not_zero_results(store, provider, payload):
    result = await discovery.search(
        store,
        "HOI",
        [provider],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
    )
    assert result["data"]["status"] == "failed"
    assert result["data"]["pages"][0]["raw_hash"]
