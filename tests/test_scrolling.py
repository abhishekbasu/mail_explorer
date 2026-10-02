import pytest
from conftest import write_thread_mailbox
from fastapi.testclient import TestClient


@pytest.mark.parametrize("mode", ["messages", "threads"])
@pytest.mark.parametrize("query", ["", "selective"])
def test_continuous_scroll_cursors_work_in_both_directions(archive, mode, query):
    app, catalog, mailbox_id, path, _ = archive
    write_thread_mailbox(
        path,
        [
            {
                "Message-ID": f"<message{number}@example.com>",
                "Subject": f"{'Selective' if number % 2 == 0 else 'Other'} {number}",
                # Equal dates exercise the thread cursor's message-id tie breaker.
                "Date": "Thu, 1 Oct 2026 10:00:00 +0530",
            }
            for number in range(15)
        ],
    )
    with TestClient(app) as client:
        base = f"/api/mailboxes/{mailbox_id}"
        client.post(f"{base}/index")
        catalog.get(mailbox_id)._thread.join(timeout=5)
        assert catalog.get(mailbox_id).status()["state"] == "complete"
        expected = list(range(1, 16, 2)) if query else list(range(1, 16))
        if mode == "threads":
            expected.reverse()

        def get(**cursors):
            response = client.get(f"{base}/{mode}", params={"limit": 3, "q": query, **cursors})
            assert response.status_code == 200
            return response.json()

        def ids(page):
            key = "latest_message_id" if mode == "threads" else "id"
            return [item[key] for item in page["items"]]

        pages = [get()]
        assert pages[0]["previous_cursor"] is None
        while pages[-1]["next_cursor"] is not None:
            pages.append(get(after=pages[-1]["next_cursor"]))
        assert [item for page in pages for item in ids(page)] == expected
        assert len(pages) >= 3

        # Reload every evicted batch going upward. The last batch may be shorter.
        collected = ids(pages[-1])
        page = pages[-1]
        while page["previous_cursor"] is not None:
            older = get(before=page["previous_cursor"])
            assert older["next_cursor"] is not None
            following = get(after=older["next_cursor"])
            assert ids(following) == ids(page)
            collected = ids(older) + collected
            page = older
        assert collected == expected
        assert ids(page) == expected[:3]
        beginning = "999999999999:999999999999" if mode == "threads" else 1
        assert not get(before=beginning)["items"]


def test_before_cursors_are_validated(archive):
    app, _, mailbox_id, _, _ = archive
    with TestClient(app) as client:
        base = f"/api/mailboxes/{mailbox_id}"
        for cursor in [0, -1, "not-a-number"]:
            assert client.get(f"{base}/messages", params={"before": cursor}).status_code == 422
        for cursor in ["1", "1:invalid", "1:2:3", "1 OR 1=1"]:
            assert client.get(f"{base}/threads", params={"before": cursor}).status_code == 422
