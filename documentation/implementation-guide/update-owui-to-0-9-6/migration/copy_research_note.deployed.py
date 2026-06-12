"""
title: Copy Research Note
author: ai-stack
version: 5.0.0
description: One-click action that finds the Open WebUI Note the write_note tool created *in this chat* (by reading the note id from the persisted chat record) and presents it with a copy button — native code-block copy, or a select-all modal.
required_open_webui_version: 0.4.0
"""

import re
import json
import html as _html
from typing import Optional, Tuple

from pydantic import BaseModel, Field

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# A write_note result object, e.g.
#   {"status":"success","id":"<uuid>","title":"...","created_at":<ns>}
# It is persisted inside the chat record, often as an escaped JSON string, so
# the extractors below also run on a backslash-unescaped variant.
_ID_RE = re.compile(r'"id"\s*:\s*"(' + _UUID + r')"')
_TITLE_RE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"')


class Action:
    class Valves(BaseModel):
        scope_to_user: bool = Field(
            default=True,
            description="Only return a note if it belongs to the current user (recommended).",
        )
        fallback_latest_user_note: bool = Field(
            default=True,
            description=(
                "If the note id can't be found in the chat record, fall back to "
                "the current user's most recently created note. Less precise, but "
                "avoids a hard failure when only one research ran."
            ),
        )
        delivery: str = Field(
            default="append",
            description=(
                "How to deliver: "
                "'append' = add a copyable fenced code block to the message "
                "(native copy button, works for any size — recommended). "
                "'modal' = popup with select-all text (clean, but truncates long notes). "
                "'auto' = modal for short notes, append otherwise."
            ),
        )
        modal_threshold_chars: int = Field(
            default=4000,
            description="When delivery='auto', use the modal only if the note is shorter than this.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> Optional[dict]:
        user_id = (__user__ or {}).get("id", "")
        if not user_id:
            await self._notify(
                __event_emitter__,
                "warning",
                "No user id available — cannot locate the note.",
            )
            return

        chat_id = self._chat_id(body, __metadata__)

        # 1) Trimmed body (rarely has the tool result, but cheap to check).
        note_id, hint_title, how = (*self._latest_note_ref(body), "chat body")
        # 2) The persisted chat record DOES contain the write_note tool result.
        if not note_id and chat_id:
            chat_obj = self._get_chat(chat_id)
            if chat_obj is not None:
                note_id, hint_title = self._latest_note_ref(chat_obj)
                how = "chat record"
        # 3) Last resort: the user's most recently created note.
        if not note_id and self.valves.fallback_latest_user_note:
            note_id = self._latest_user_note_id(user_id)
            hint_title, how = "", "most recent user note (fallback)"

        if not note_id:
            await self._notify(
                __event_emitter__,
                "warning",
                "Couldn't find a write_note result for this chat. "
                "Open the note from the Notes panel, or tell me the chat structure.",
            )
            return

        note = self._get_note(note_id)
        if note is None:
            await self._notify(
                __event_emitter__,
                "warning",
                f"Note {note_id[:8]}… not found in Open WebUI (it may have been deleted).",
            )
            return

        owner = getattr(note, "user_id", None)
        if self.valves.scope_to_user and owner and owner != user_id:
            await self._notify(
                __event_emitter__,
                "error",
                "That note belongs to another user — refusing to show it.",
            )
            return

        title = (getattr(note, "title", None) or hint_title or "Research Note").strip()
        content = self._note_text(note)
        if not content.strip():
            await self._notify(
                __event_emitter__, "warning", f"“{title}” has no readable text content."
            )
            return

        mode = (self.valves.delivery or "append").lower()
        if mode == "auto":
            mode = (
                "modal"
                if len(content) <= self.valves.modal_threshold_chars
                else "append"
            )

        if mode == "modal" and __event_call__:
            await __event_call__(
                {
                    "type": "input",
                    "data": {
                        "title": title,
                        "message": "Select all (Ctrl/Cmd+A) and copy (Ctrl/Cmd+C).",
                        "placeholder": "Note will appear here",
                        "value": content,
                    },
                }
            )
        elif __event_emitter__:
            fence = self._safe_fence(content)
            block = (
                f"\n\n---\n**{title}**  \n"
                f"<sub>Open WebUI note · {note_id} · via {how}</sub>\n\n"
                f"{fence}\n{content}\n{fence}\n"
            )
            await __event_emitter__({"type": "message", "data": {"content": block}})

        await self._notify(
            __event_emitter__,
            "success",
            f"“{title}” ready to copy ({len(content):,} chars).",
        )

    # ------------------------------------------------------------------ #
    # Locating the note id
    # ------------------------------------------------------------------ #

    @staticmethod
    def _chat_id(body: dict, metadata: Optional[dict]) -> str:
        for src in (
            body or {},
            (body or {}).get("metadata") or {},
            metadata or {},
        ):
            cid = src.get("chat_id") or src.get("id")
            if isinstance(cid, str) and cid:
                return cid
        return ""

    def _latest_note_ref(self, obj) -> Tuple[Optional[str], str]:
        """Walk a structure in order; return (note_id, title) for the LAST
        write_note success result encountered."""
        refs: list = []

        def scan_text(text: str):
            for variant in (text, text.replace('\\"', '"').replace("\\\\", "\\")):
                ids = list(_ID_RE.finditer(variant))
                if not ids:
                    continue
                titles = _TITLE_RE.findall(variant)
                for i, m in enumerate(ids):
                    t = titles[i] if i < len(titles) else (titles[-1] if titles else "")
                    refs.append((m.group(1), self._unescape(t)))

        def walk(o):
            if isinstance(o, str):
                if '"id"' in o:
                    scan_text(o)
            elif isinstance(o, dict):
                oid = o.get("id")
                if (
                    isinstance(oid, str)
                    and re.fullmatch(_UUID, oid)
                    and ("title" in o or "created_at" in o or o.get("status"))
                ):
                    refs.append((oid, str(o.get("title") or "")))
                for v in o.values():
                    walk(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    walk(v)

        try:
            walk(obj)
        except Exception:
            return None, ""
        if not refs:
            return None, ""
        return refs[-1]

    @staticmethod
    def _unescape(s: str) -> str:
        return (
            s.replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\/", "/")
            .replace("\\\\", "\\")
        )

    # ------------------------------------------------------------------ #
    # Open WebUI internal-model access (same pattern Fileshed uses)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_chat(chat_id: str):
        """Return the full persisted chat (the .chat dict) by id, or None."""
        try:
            from open_webui.models.chats import Chats
        except ImportError:
            try:
                from open_webui.apps.webui.models.chats import Chats  # older layout
            except ImportError:
                return None
        try:
            row = Chats.get_chat_by_id(chat_id)
        except Exception:
            return None
        if row is None:
            return None
        # ChatModel.chat holds the full conversation tree (messages + tool
        # results). json round-trip normalises it for the walker.
        chat = getattr(row, "chat", None)
        try:
            return json.loads(json.dumps(chat, default=str))
        except Exception:
            return chat

    @staticmethod
    def _get_note(note_id: str):
        try:
            from open_webui.models.notes import Notes
        except ImportError:
            try:
                from open_webui.apps.webui.models.notes import Notes  # older layout
            except ImportError:
                return None
        try:
            return Notes.get_note_by_id(note_id)
        except Exception:
            return None

    @staticmethod
    def _latest_user_note_id(user_id: str) -> Optional[str]:
        try:
            from open_webui.models.notes import Notes
        except ImportError:
            try:
                from open_webui.apps.webui.models.notes import Notes  # older layout
            except ImportError:
                return None
        rows = None
        for getter in ("get_notes_by_user_id", "get_notes"):
            fn = getattr(Notes, getter, None)
            if fn is None:
                continue
            try:
                rows = fn(user_id) if getter == "get_notes_by_user_id" else fn()
                break
            except Exception:
                continue
        if not rows:
            return None
        try:
            rows = [r for r in rows if getattr(r, "user_id", user_id) == user_id]
            rows.sort(key=lambda r: getattr(r, "created_at", 0) or 0, reverse=True)
        except Exception:
            pass
        return getattr(rows[0], "id", None) if rows else None

    # ------------------------------------------------------------------ #
    # Note content extraction
    # ------------------------------------------------------------------ #

    def _note_text(self, note) -> str:
        """Extract readable markdown/plain text from a Note model regardless of
        how write_note populated `data` (str, {'content': str|dict}, tiptap)."""
        data = getattr(note, "data", None)
        candidates = []

        def collect(obj, key=""):
            if obj is None:
                return
            if isinstance(obj, str):
                candidates.append((key, obj))
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    collect(v, k)
            elif isinstance(obj, list):
                for v in obj:
                    collect(v, key)

        collect(data)

        for want in ("md", "markdown", "text", "content", "html"):
            for key, val in candidates:
                if key == want and val.strip():
                    return self._to_text(val) if key == "html" else val
        if candidates:
            key, val = max(candidates, key=lambda kv: len(kv[1]))
            return self._to_text(val) if "<" in val and ">" in val else val
        return ""

    @staticmethod
    def _to_text(s: str) -> str:
        """Best-effort HTML → text for notes stored as HTML."""
        s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
        s = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|tr)\s*>", "\n", s)
        s = re.sub(r"<[^>]+>", "", s)
        return _html.unescape(s).strip()

    # ------------------------------------------------------------------ #
    # Output helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_fence(content: str) -> str:
        """Pick a backtick fence longer than any backtick run in the content so
        embedded code blocks don't break out of the copy block."""
        longest = 0
        run = 0
        for ch in content:
            if ch == "`":
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        return "`" * max(3, longest + 1)

    async def _notify(self, emitter, level: str, message: str) -> None:
        if emitter:
            await emitter(
                {"type": "notification", "data": {"type": level, "content": message}}
            )
