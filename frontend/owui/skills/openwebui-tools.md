---
name: openwebui-tools
description: Complete guide for creating OpenWebUI Tools — the single-file Python pattern, HTML card rendering, event emitters, valves, error handling, and design conventions.
version: 2.0.0
---
 
# Open WebUI Tools Development
 
Create, update, and maintain Open WebUI Workspace Tools — single-file Python tools that run arbitrary code on the server and render rich UI cards in chat.
 
## Quick Reference
 
Tools live in `Tools/` directory as single `.py` files. Each tool is a class `Tools` with one or more async methods.
 
## 1. File Structure
 
```python
"""
title: Tool Name
description: >
    One-line or multi-line description of what the tool does.
    Include example commands users can type.
author: YourName
version: 1.0.0
license: MIT
requirements: httpx, pydantic  # pip installable packages
"""
 
import httpx
from typing import Optional, Awaitable, Callable
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
 
class Tools:
    def __init__(self):
        self.valves = self.Valves()  # optional
 
    class Valves(BaseModel):
        api_key: str = Field("", description="Your API key")
 
    async def main_method(
        self,
        query: str,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> "HTMLResponse | str":
        """
        Detailed method docstring — this is the tool's prompt to the LLM.
        Write it like instructions for the LLM, not a human.
        :param query: Description of the parameter
        :return: What the method returns
        """
        ...
```
 
## 2. Docstring Metadata (Top-Level)
 
| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | Display name of the tool |
| `description` | Yes | What it does + example commands |
| `author` | Yes | Your name |
| `version` | Yes | Semantic version (e.g. `1.0.0`) |
| `license` | Yes | e.g. `MIT` |
| `requirements` | No | Comma-separated pip packages |
| `author_url` | No | Your website/GitHub |
| `funding_url` | No | Donation link |
 
## 3. Class Structure
 
### Valves (Optional but Recommended)
 
```python
class Valves(BaseModel):
    default_setting: str = Field("default_value", description="What this does")
    max_items: int = Field(10, description="Maximum items to return")
```
 
### UserValves (Optional — user-editable via Open WebUI UI)
 
Same as Valves but named `UserValves` — the Open WebUI admin panel lets users configure these values directly.
 
### Tools Class
 
```python
class Tools:
    def __init__(self):
        self.valves = self.Valves()
 
    async def my_tool(self, param: str) -> "HTMLResponse | str":
        ...
```
 
**Important:** All tool methods MUST be `async`. The backend is moving toward fully async execution.
 
## 4. Event Emitter
 
The `__event_emitter__` parameter lets you send live status updates to the chat while the tool runs.
 
### Status Events (Native Mode Compatible)
 
```python
await __event_emitter__({
    "type": "status",
    "data": {
        "description": "Fetching data…",
        "done": False,       # False = processing, True = done
        "hidden": False      # False = visible, True = auto-hide when done
    }
})
```
 
### Citation Events (Native Mode Compatible)
 
```python
await __event_emitter__({
    "type": "citation",
    "data": {
        "document": [content_string],
        "metadata": [{
            "source": "Title",
            "url": "https://example.com",
            "date_accessed": datetime.now().isoformat()
        }],
        "source": {"name": "Title", "url": "https://example.com"}
    }
})
```
 
**⚠️ Critical Citation Warning:** Set `self.citation = False` in `__init__` if you use custom citations, otherwise automatic citations will override them.
 
### Other Compatible Event Types
 
```python
# Notification (toast)
await __event_emitter__({"type": "notification", "data": {"content": "Done!"}})
 
# Follow-ups
await __event_emitter__({"type": "chat:message:follow_ups", "data": {"follow_ups": ["Tell me more?", "Show another"]}})
 
# Files
await __event_emitter__({"type": "files", "data": {"files": [{"name": "report.pdf", "url": "/files/report.pdf"}]}})
 
# Title update
await __event_emitter__({"type": "chat:title", "data": {"title": "New Chat Title"}})
```
 
### Incompatible Event Types (Will be overwritten in Native Mode)
 
**DO NOT USE:** `message`, `chat:message:delta`, `chat:message`, `replace` — these get overwritten by native completion snapshots.
 
## 5. Rich HTML Cards (Inline Embedding)
 
The best tools return `HTMLResponse` objects that render as inline iframes in the chat.
 
### Basic Pattern
 
```python
from fastapi.responses import HTMLResponse
 
html_content = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{background:transparent;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:6px;}
</style>
</head>
<body>
<div class="card">Your content here</div>
</body>
</html>"""
 
return HTMLResponse(content=html_content, headers={"content-disposition": "inline"})
```
 
### Key HTML Card Conventions
 
- `background:transparent` on html/body — the card lives inside a chat iframe
- Inline `<style>` only — no external CSS files
- Use `.card`, `.header`, `.body` class names
- Dark theme is preferred (most tools use dark backgrounds)
- Max-width around 600-800px for readability
- Use CSS variables for color theming (e.g., `--accent: #58a6ff`)
 
### Height Reporting (Auto-sizing)
 
```html
<script>
function reportHeight() {
    const h = document.documentElement.scrollHeight;
    parent.postMessage({type: 'iframe:height', height: h}, '*');
}
window.addEventListener('load', reportHeight);
if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(reportHeight).observe(document.body);
}
</script>
```
 
## 6. HTTP Library Patterns
 
### httpx (Async — Preferred for new tools)
 
```python
import httpx
 
def _fetch(url: str, params: dict = None) -> dict:
    with httpx.Client(timeout=10) as client:
        r = client.get(url, params=params, headers={"User-Agent": "OpenWebUI-Tool/1.0"})
        r.raise_for_status()
        return r.json()
```
 
### requests (Sync — Widely used in existing tools)
 
```python
import requests
 
def _fetch(url: str, params: dict = None) -> dict:
    r = requests.get(url, params=params, headers={"User-Agent": "OpenWebUI-Tool/1.0"}, timeout=10)
    r.raise_for_status()
    return r.json()
```
 
### aiohttp (For concurrent requests)
 
```python
import aiohttp
 
async def _fetch_concurrent(urls: list) -> list:
    async with aiohttp.ClientSession() as session:
        tasks = [session.get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        return responses
```
 
### Common HTTP Conventions
 
- Always set `User-Agent` header
- Always set `timeout` (8-15 seconds)
- Always handle `raise_for_status()`
- Use `headers={"content-disposition": "inline"}` for HTMLResponse
 
## 7. LLM Instructions in Docstrings
 
The method docstring acts as the **LLM prompt** — write it like instructions for the model:
 
```python
async def my_tool(self, query: str) -> "HTMLResponse | str":
    """
    Look up information about X and display a beautiful card.
 
    USE THIS when the user asks about: X, Y, Z
    DO NOT use this for: A, B, C
 
    :param query: What to look up
    :return: An HTML card rendered in chat
    """
```
 
**Guidelines:**
- Start with what the tool does
- List trigger conditions ("Use this when...")
- List what NOT to use it for
- Document parameters with `:param name:`
- Document return value with `:return:`
- For complex tools, include usage examples
 
## 8. Python 3.10/3.11 F-String Compatibility
 
⚠️ **Critical:** OpenWebUI Tools runs on Python 3.10 or 3.11 (NOT 3.12). F-strings with `[]` bracket access inside `{}` expressions fail with "unmatched '['" errors. PEP 701 (Python 3.12+) fixed this.
 
**BAD:**
```python
return f"Last price: ${prices[-1]}"  # SyntaxError in Python 3.10/3.11
```
 
**GOOD:**
```python
last_price = prices[-1]
return f"Last price: ${last_price}"
```
 
**PRE-COMPUTE ALL BRACKET ACCESS BEFORE F-STRINGS:**
```python
# At the top of the function or card builder:
name = data["name"]
value = result[0]["price"]
padding_left = padding["left"]
 
# Then reference by name in f-string:
return f"""...{name}...{value}...{padding_left}..."""
```
 
## 9. Error Handling Pattern
 
```python
try:
    data = _fetch(url)
    card = _build_card(data)
    await _emit(__event_emitter__, "✅ Done!", done=True)
    return HTMLResponse(content=card, headers={"content-disposition": "inline"})
except ValueError as ve:
    msg = f"❌ {ve}"
    await _emit(__event_emitter__, msg, done=True)
    return msg  # Plain string — shown as LLM text
except Exception as exc:
    msg = f"❌ Error: {exc}"
    await _emit(__event_emitter__, msg, done=True)
    return msg
```
 
## 10. Return Types
 
| Return Type | Behavior |
|-------------|----------|
| `HTMLResponse` | Renders as inline iframe in chat (rich UI) |
| `str` | Sent to LLM as text response |
| Tuple `(HTMLResponse, str)` | Renders card AND sends text to LLM (used in Steam tool) |
 
## 11. Additional Optional Parameters
 
You can add these to any tool method signature:
 
| Parameter | Description |
|-----------|-------------|
| `__event_emitter__` | Send status updates during execution |
| `__event_call__` | User interactions (e.g., confirmation dialogs) |
| `__user__` | Dictionary with user info + `__user__["valves"]` |
| `__metadata__` | Dictionary with chat metadata (check `params.function_calling` for mode detection) |
| `__messages__` | List of previous messages |
| `__files__` | Attached files |
| `__model__` | Dictionary with model information |
| `__oauth_token__` | OAuth token for authenticated API calls |
 
## 12. Production / Multi-Worker Deployments
 
In production with `UVICORN_WORKERS > 1`, runtime `pip install` of requirements causes race conditions. Set:
 
```
ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False
```
 
Then pre-install dependencies in a custom Dockerfile:
```dockerfile
FROM ghcr.io/open-webui/open-webui:main
RUN pip install --no-cache-dir python-docx requests beautifulsoup4
```
 
## 13. Design Patterns from Existing Tools
 
### Pattern A: Simple API + Text Return
```python
# wiki.py — Wikipedia Lookup
async def lookup(self, query: str, detail: str = "standard", language: str = "en") -> str:
    """Search Wikipedia and return article content."""
    results = self._search_titles(query, lang, limit=6)
    return "\n\n".join(output_parts)
```
 
### Pattern B: API + HTMLCard (No Valves)
```python
# qr-codes.py — QR Code Generator
async def generate_qr_code(self, content: str, __event_emitter__=None) -> HTMLResponse:
    """Creates a QR code for the given text."""
    return HTMLResponse(content=html_content, headers={"Content-Disposition": "inline"})
```
 
### Pattern C: API + HTMLCard + Valves
```python
# weather.py — Weather Card
class Valves(BaseModel):
    default_location: str = Field("", description="Default location")
 
async def get_weather(self, location: Optional[str] = None) -> "HTMLResponse | str":
    loc = location or self.valves.default_location
    ...
```
 
### Pattern D: HTMLCard with Height Reporting Script
```python
html += """<script>
function reportHeight() {
    const h = document.documentElement.scrollHeight;
    parent.postMessage({type: 'iframe:height', height: h}, '*');
}
window.addEventListener('load', reportHeight);
</script>"""
```
 
### Pattern E: Multi-step with Event Emitter
```python
await _emit(__event_emitter__, "🔍 Searching…")
data = _search(query)
await _emit(__event_emitter__, "📦 Fetching details…")
card = _build_card(data)
await _emit(__event_emitter__, "✅ Done!", done=True)
return HTMLResponse(content=card, headers={"content-disposition": "inline"})
```
 
### Pattern F: Parallel API Calls (aiohttp)
```python
async def fetch_all(self, items: list) -> "HTMLResponse | str":
    async with aiohttp.ClientSession() as session:
        tasks = [session.get(url, ssl=False) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
    ...
```
 
### Pattern G: Offline Fallback
```python
# jokes.py — Multi-source with offline fallback
def _fetch_offline(self, amount: int, search: Optional[str]) -> list:
    pool = _OFFLINE_JOKES[:]
    # ... filter and return
```
 
## 14. CSS Styling Conventions
 
### Color Palette System
Most tools use HSL hue families for consistent colors:
 
```python
_HUE_FAMILIES = [
    (0, 18),       # Reds
    (25, 50),      # Oranges
    (90, 130),     # Greens
    (155, 175),    # Cyans
    (175, 200),    # Blues
    (250, 280),    # Purples
    (300, 330),    # Pinks
]
 
def _vivid_palette():
    fam = random.sample(_HUE_FAMILIES, 5)
    h = [random.randint(lo, hi) for lo, hi in fam]
    s = [random.randint(96, 100)] * 5
    l = [random.randint(60, 70)] * 5
    return [f"hsl({h[i]},{s[i]}%,{l[i]}%)" for i in range(5)]
```
 
### Dark Theme Defaults
```css
--bg0: #0d1117;    /* main background */
--bg1: #161b22;    /* card/surface */
--bg2: #1a2332;    /* lighter surface */
--line: rgba(255,255,255,0.08);  /* borders */
--text: #e6edf3;   /* primary text */
--text2: #8b949e;  /* secondary text */
--accent: #58a6ff; /* brand color */
```
 
## 15. Checklist Before Saving a Tool
 
- [ ] Top-level docstring with all metadata fields (title, description, author, version, license)
- [ ] `class Tools:` with `def __init__(self):` setting `self.valves`
- [ ] All methods are `async`
- [ ] Type hints on ALL parameters (required for JSON schema generation)
- [ ] Method docstring written as LLM instructions
- [ ] `HTMLResponse` with `content-disposition: inline` header (if returning rich UI)
- [ ] Height reporting `<script>` in HTML cards
- [ ] `background:transparent` on html/body
- [ ] Error handling with try/except and `__event_emitter__` status updates
- [ ] No `[]` bracket access inside f-string `{}` (Python 3.10/3.11 compat)
- [ ] `self.citation = False` if using custom citations
- [ ] `User-Agent` header on all HTTP requests
- [ ] Timeout set on all HTTP requests
- [ ] Valves defined with `pydantic.BaseModel` if tool needs configuration