"""Tests for the manual admin setup script. Nothing here reaches a real database."""
import runpy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from werkzeug.security import check_password_hash

SCRIPT = Path(__file__).resolve().parent.parent / "create_admin_user.py"
USERNAME = "owner@example.test"
PASSWORD = "Not-a-real-value-1"


@pytest.fixture
def cosmos(monkeypatch):
    client = MagicMock()
    database = client.return_value.create_database_if_not_exists.return_value
    container = database.create_container_if_not_exists.return_value
    container.query_items.return_value = []
    monkeypatch.setattr("azure.cosmos.CosmosClient", client)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("COSMOS_URI", "https://cosmos.example.test")
    monkeypatch.setenv("COSMOS_KEY", "fake")
    monkeypatch.setenv("COSMOS_DB_NAME", "fake")
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    return client, container


def run_script():
    return runpy.run_path(str(SCRIPT), run_name="__main__")


def answer_prompts(monkeypatch, username, password):
    monkeypatch.setattr("builtins.input", lambda *args: username)
    monkeypatch.setattr("getpass.getpass", lambda *args: password)


def test_uses_settings_and_never_prints_password(cosmos, monkeypatch, capsys):
    _, container = cosmos
    monkeypatch.setenv("ADMIN_USERNAME", USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)

    run_script()

    created = container.create_item.call_args.kwargs["body"]
    assert created["username"] == USERNAME
    assert check_password_hash(created["password"], PASSWORD)
    assert PASSWORD not in capsys.readouterr().out


def test_prompts_when_settings_are_missing(cosmos, monkeypatch):
    _, container = cosmos
    answer_prompts(monkeypatch, USERNAME, PASSWORD)

    run_script()

    created = container.create_item.call_args.kwargs["body"]
    assert created["username"] == USERNAME
    assert check_password_hash(created["password"], PASSWORD)


@pytest.mark.parametrize("username,password", [("", PASSWORD), (USERNAME, ""), ("   ", "")])
def test_stops_before_connecting_when_empty(cosmos, monkeypatch, username, password):
    client, _ = cosmos
    answer_prompts(monkeypatch, username, password)

    with pytest.raises(SystemExit) as stopped:
        run_script()

    assert stopped.value.code == 1
    client.assert_not_called()
