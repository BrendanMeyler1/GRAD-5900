import shutil
from pathlib import Path
from uuid import uuid4

from backend.core.conversation_history import ConversationHistoryManager


def test_conversation_history_persists_and_sorts():
    base_dir = Path("data") / f"test_conversations_{uuid4().hex}"
    manager = ConversationHistoryManager(base_dir=str(base_dir))

    try:
        first = manager.create_conversation("First Chat")
        second = manager.create_conversation("Second Chat")

        manager.append_messages(
            first["id"],
            [{"role": "user", "content": "hello"}],
        )
        manager.rename_conversation(second["id"], "Renamed Chat")

        conversations = manager.list_conversations()

        assert {conversation["title"] for conversation in conversations} >= {
            "First Chat",
            "Renamed Chat",
        }
        loaded = manager.load_conversation(first["id"])
        assert loaded["messages"][0]["content"] == "hello"
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_conversation_history_clear_removes_all_saved_conversations():
    base_dir = Path("data") / f"test_conversations_{uuid4().hex}"
    manager = ConversationHistoryManager(base_dir=str(base_dir))

    try:
        manager.create_conversation("One")
        manager.create_conversation("Two")

        removed = manager.clear()

        assert removed == 2
        assert manager.list_conversations() == []
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
