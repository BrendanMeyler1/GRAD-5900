"""Tests for mcp_servers.browser_bridge."""

from __future__ import annotations

from mcp_servers.browser_bridge import BrowserBridgeMCPServer


class _FakeDriver:
    def __init__(self, headless=True, humanizer=None):
        self.headless = headless
        self.humanizer = humanizer
        self.started = False
        self.stopped = False
        self.current_url = ""

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def goto(self, url: str):
        self.current_url = url
        return {"url": url}

    async def get_dom_snapshot(self, form_selector: str = "form"):
        return {
            "url": self.current_url,
            "form_selector": form_selector,
            "fields": [{"selector": "#first_name"}],
        }

    async def fill_field(self, selector: str, value, use_humanizer: bool = True):
        return {"selector": selector, "value": value, "status": "filled"}

    async def upload_file(self, selector: str, file_path: str):
        return {"selector": selector, "file_path": file_path, "status": "uploaded"}

    async def click(self, selector: str):
        return {"selector": selector, "status": "clicked"}

    async def screenshot(self, path: str, full_page: bool = True):
        return {"path": path, "status": "saved"}


def test_browser_bridge_session_lifecycle_and_actions():
    server = BrowserBridgeMCPServer(driver_factory=lambda **kwargs: _FakeDriver(**kwargs))

    started = server.start_session(headless=True, use_humanizer=True)
    session_id = started["session_id"]
    assert started["status"] == "started"
    assert len(server.list_sessions()) == 1

    nav = server.navigate(session_id, "https://boards.greenhouse.io/acme/jobs/123#app")
    assert nav["status"] == "navigated"

    dom = server.get_dom_tree(session_id=session_id)
    assert dom["fields"][0]["selector"] == "#first_name"

    fill = server.fill_field(session_id=session_id, selector="#first_name", value="Jane")
    assert fill["status"] == "filled"

    upload = server.upload_file(session_id=session_id, selector="input[type=file]", file_path="resume.pdf")
    assert upload["status"] == "uploaded"

    click = server.click(session_id=session_id, selector="button[type=submit]")
    assert click["status"] == "clicked"

    shot = server.screenshot(session_id=session_id, path=".tmp/mcp_bridge.png")
    assert shot["status"] == "saved"

    stopped = server.stop_session(session_id=session_id)
    assert stopped["status"] == "stopped"
    assert server.list_sessions() == []

