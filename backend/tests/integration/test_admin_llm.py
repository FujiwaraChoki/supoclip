"""The admin page for the chain of models and the keys of the providers."""

from types import SimpleNamespace

import pytest

from src.api.routes import admin as admin_routes
from src.llm_fallback import Attempt, FallbackResult
from tests.fixtures.factories import create_user


@pytest.mark.asyncio
async def test_the_llm_page_needs_an_admin(client, db_session, auth_headers):
    await create_user(
        db_session, user_id="user-1", email="owner@example.com", is_admin=False
    )

    response = await client.get("/admin/llm", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_llm_page_shows_the_chain_and_the_marks_of_the_keys(
    client, db_session, auth_headers
):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    response = await client.get("/admin/llm", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["primary"]
    assert body["chain"][0] == body["primary"]
    assert "google-gla" in body["providers"]
    # A key is never shown, only its mark.
    for entries in body["keys"].values():
        for entry in entries:
            assert "key" not in entry
            assert entry["fingerprint"]


@pytest.mark.asyncio
async def test_a_model_without_a_provider_is_refused(client, db_session, auth_headers):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    response = await client.patch(
        "/admin/llm/chain",
        headers=auth_headers,
        json={"fallbacks": ["gemini-2.5-flash"]},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_provider_is_refused(client, db_session, auth_headers):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    response = await client.patch(
        "/admin/llm/chain",
        headers=auth_headers,
        json={"fallbacks": ["made-up:some-model"]},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_the_fallback_models_are_stored(client, db_session, auth_headers):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    response = await client.patch(
        "/admin/llm/chain",
        headers=auth_headers,
        json={
            "fallbacks": [
                "google-gla:gemini-2.5-flash",
                "google-gla:gemini-2.5-flash-lite",
                "google-gla:gemini-2.5-flash",
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fallbacks"] == [
        "google-gla:gemini-2.5-flash",
        "google-gla:gemini-2.5-flash-lite",
    ]
    assert body["chain"] == [body["primary"], *body["fallbacks"]]


@pytest.mark.asyncio
async def test_a_key_is_added_and_removed_by_its_mark(
    client, db_session, auth_headers
):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    added = await client.post(
        "/admin/llm/keys",
        headers=auth_headers,
        json={"provider": "google", "key": "super-secret-key-1234"},
    )
    assert added.status_code == 200
    fingerprint = added.json()["fingerprint"]
    assert "super" not in fingerprint
    assert "1234" in fingerprint

    listed = await client.get("/admin/llm", headers=auth_headers)
    marks = [entry["fingerprint"] for entry in listed.json()["keys"].get("google", [])]
    assert fingerprint in marks

    removed = await client.request(
        "DELETE",
        "/admin/llm/keys",
        headers=auth_headers,
        json={"provider": "google", "fingerprint": fingerprint},
    )
    assert removed.status_code == 200

    listed = await client.get("/admin/llm", headers=auth_headers)
    marks = [entry["fingerprint"] for entry in listed.json()["keys"].get("google", [])]
    assert fingerprint not in marks


@pytest.mark.asyncio
async def test_an_empty_key_is_refused(client, db_session, auth_headers):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    response = await client.post(
        "/admin/llm/keys",
        headers=auth_headers,
        json={"provider": "google", "key": "   "},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_the_check_of_one_model_reports_the_answer(
    client, db_session, auth_headers, monkeypatch
):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    async def fake_fallback(build_agent, run, **kwargs):
        attempt = Attempt(
            model="google-gla:gemini-2.5-flash",
            provider="google-gla",
            key_fingerprint="…1234·abcdef",
            seconds=0.2,
            ok=True,
        )
        return FallbackResult(
            value=SimpleNamespace(output="ok"), model=attempt.model, attempts=(attempt,)
        )

    monkeypatch.setattr(admin_routes, "run_with_fallback", fake_fallback)

    response = await client.post(
        "/admin/llm/test/model",
        headers=auth_headers,
        json={"model": "google-gla:gemini-2.5-flash"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["answer"] == "ok"


@pytest.mark.asyncio
async def test_the_run_of_the_chain_reports_every_attempt(
    client, db_session, auth_headers, monkeypatch
):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )

    async def fake_fallback(build_agent, run, **kwargs):
        attempts = (
            Attempt(
                model="google-gla:gemini-3-flash-preview",
                provider="google-gla",
                key_fingerprint=None,
                seconds=1.0,
                ok=False,
            ),
            Attempt(
                model="google-gla:gemini-2.5-flash",
                provider="google-gla",
                key_fingerprint=None,
                seconds=0.3,
                ok=True,
            ),
        )
        return FallbackResult(
            value=SimpleNamespace(output="ok"),
            model="google-gla:gemini-2.5-flash",
            attempts=attempts,
        )

    monkeypatch.setattr(admin_routes, "run_with_fallback", fake_fallback)

    response = await client.post("/admin/llm/test/chain", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["answered_by"] == "google-gla:gemini-2.5-flash"
    assert len(body["attempts"]) == 2
    assert body["attempts"][0]["ok"] is False
    assert body["attempts"][1]["ok"] is True
