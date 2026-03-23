import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend.core.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationHistoryManager:
    def __init__(self, base_dir: Optional[str] = None):
        artifacts_dir = Path(base_dir or settings.artifacts_dir)
        self.conversations_dir = artifacts_dir / "conversations"
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

    def _path_for_id(self, conversation_id: str) -> Path:
        return self.conversations_dir / f"{conversation_id}.json"

    def create_conversation(self, title: str = "New Chat") -> Dict[str, Any]:
        now = _utc_now()
        conversation = {
            "id": uuid4().hex,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self.save_conversation(conversation)
        return conversation

    def load_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        path = self._path_for_id(conversation_id)
        if not path.exists():
            return None

        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def save_conversation(self, conversation: Dict[str, Any]) -> str:
        payload = deepcopy(conversation)
        payload["updated_at"] = _utc_now()
        path = self._path_for_id(payload["id"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return str(path)

    def rename_conversation(self, conversation_id: str, title: str) -> Optional[Dict[str, Any]]:
        conversation = self.load_conversation(conversation_id)
        if conversation is None:
            return None

        conversation["title"] = title.strip() or "Untitled Chat"
        self.save_conversation(conversation)
        return conversation

    def append_messages(self, conversation_id: str, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        conversation = self.load_conversation(conversation_id)
        if conversation is None:
            return None

        conversation.setdefault("messages", [])
        conversation["messages"].extend(messages)
        self.save_conversation(conversation)
        return conversation

    def delete_conversation(self, conversation_id: str) -> bool:
        path = self._path_for_id(conversation_id)
        if not path.exists():
            return False

        path.unlink()
        return True

    def clear(self) -> int:
        removed = 0
        for path in self.conversations_dir.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def list_conversations(self) -> List[Dict[str, Any]]:
        conversations = []
        for path in self.conversations_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    payload = json.load(f)

                messages = payload.get("messages", [])
                preview = ""
                for message in messages:
                    if message.get("role") == "user":
                        preview = message.get("content", "")
                        break

                conversations.append(
                    {
                        "id": payload.get("id", path.stem),
                        "title": payload.get("title", "Untitled Chat"),
                        "created_at": payload.get("created_at", ""),
                        "updated_at": payload.get("updated_at", ""),
                        "message_count": len(messages),
                        "preview": preview,
                    }
                )
            except Exception:
                continue

        conversations.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return conversations
