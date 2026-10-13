import pytest
from unittest.mock import MagicMock, patch

from app.config import get_settings
from app.services.plaid import PlaidClient


def test_create_link_token_default_and_update_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGERPILOT_PLAID_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("LEDGERPILOT_PLAID_SECRET", "test-secret")
    monkeypatch.setenv("LEDGERPILOT_PLAID_ENVIRONMENT", "sandbox")
    get_settings.cache_clear()

    client = PlaidClient()
    with patch.object(client, "_post", return_value={"link_token": "link-sandbox-123"}) as mock_post:
        token = client.create_link_token(profile_id="user-1", person_name="Alex Smith")
        assert token == "link-sandbox-123"
        payload = mock_post.call_args[0][1]
        assert payload["products"] == ["investments"]
        assert "access_token" not in payload

    with patch.object(client, "_post", return_value={"link_token": "link-sandbox-update-456"}) as mock_post:
        token = client.create_link_token(
            profile_id="user-1", person_name="Alex Smith", access_token="access-sandbox-abc"
        )
        assert token == "link-sandbox-update-456"
        payload = mock_post.call_args[0][1]
        assert payload["access_token"] == "access-sandbox-abc"
        assert "products" not in payload


def test_remove_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGERPILOT_PLAID_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("LEDGERPILOT_PLAID_SECRET", "test-secret")
    monkeypatch.setenv("LEDGERPILOT_PLAID_ENVIRONMENT", "sandbox")
    get_settings.cache_clear()

    client = PlaidClient()
    with patch.object(client, "_post", return_value={"removed": True}) as mock_post:
        result = client.remove_item("access-sandbox-abc")
        assert result["removed"] is True
        assert mock_post.call_args[0][0] == "/item/remove"
        assert mock_post.call_args[0][1] == {"access_token": "access-sandbox-abc"}

    # Test error handling when item is already deleted
    with patch.object(client, "_post", side_effect=ValueError("Plaid request failed: ITEM_NOT_FOUND")):
        result = client.remove_item("access-sandbox-abc")
        assert result["removed"] is True
        assert "note" in result
