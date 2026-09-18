import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"font_size": []}, {"font_size": True}, {"font_size": "NaN"}, {"position_y": "Infinity"}, {"position_y": False}])
async def test_caption_style_rejects_non_finite_values(client, auth_headers, payload):
    response = await client.patch("/tasks/missing/clips/missing/captions", headers=auth_headers, json=payload)
    assert response.status_code == 400
