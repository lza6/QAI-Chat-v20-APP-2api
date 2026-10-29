"""Real-browser verification of demo.html (Playwright chromium).

Asserts the page loads, health badge resolves, a streamed reply renders into the
assistant bubble, meta line shows a finish reason, and no console errors appear.
"""

from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8931/"
OUT = "shots"


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(URL, wait_until="networkidle")
        page.wait_for_function(
            "document.getElementById('upstream').textContent.includes('.')", timeout=20000)
        badge = page.text_content("#health") or ""
        upstream = page.text_content("#upstream") or ""
        print(f"badge={badge!r} upstream={upstream!r}")
        assert "ok" in badge, f"health badge not ok: {badge!r}"
        page.screenshot(path=f"{OUT}/01_home.png")

        page.fill("#input", "用两句话介绍杭州西湖的特点。")
        page.click("#send")
        page.wait_for_function(
            """() => {
                 const m = document.querySelectorAll('#chat .msg.assistant');
                 if (!m.length) return false;
                 const last = m[m.length - 1];
                 const body = last.querySelector('.body');
                 return body && body.textContent.trim().length > 15;
               }""", timeout=180000)
        # wait until the finish reason lands in the meta line
        page.wait_for_function(
            """() => {
                 const m = document.querySelectorAll('#chat .msg.assistant');
                 const last = m[m.length - 1];
                 const meta = last.querySelector('.meta');
                 return meta && (meta.textContent.includes('stop') || meta.textContent.includes('length'));
               }""", timeout=180000)
        body = page.evaluate(
            "() => { const m=document.querySelectorAll('#chat .msg.assistant');"
            " return m[m.length-1].querySelector('.body').textContent; }")
        meta = page.evaluate(
            "() => { const m=document.querySelectorAll('#chat .msg.assistant');"
            " return m[m.length-1].querySelector('.meta').textContent; }")
        stats = page.text_content("#stats") or ""
        assert len(body.strip()) > 15, f"empty reply body: {body!r}"
        print(f"CHAT_OK meta={meta!r} stats={stats!r}")
        print("body:", body.replace("\n", " ")[:160])
        page.screenshot(path=f"{OUT}/02_chat.png")

        # multi-turn: second message must reuse history without page reload
        page.fill("#input", "我刚才问的是哪个地方？只回答地名。")
        page.click("#send")
        page.wait_for_function(
            """() => {
                 const m = document.querySelectorAll('#chat .msg.assistant');
                 if (m.length < 3) return false;
                 const last = m[m.length - 1];
                 const body = last.querySelector('.body');
                 const meta = last.querySelector('.meta');
                 return body && body.textContent.trim().length > 1
                        && meta && (meta.textContent.includes('stop') || meta.textContent.includes('length'));
               }""", timeout=180000)
        second = page.evaluate(
            "() => { const m=document.querySelectorAll('#chat .msg.assistant');"
            " return m[m.length-1].querySelector('.body').textContent; }")
        print("second reply:", second.replace("\n", " ")[:120])
        assert "西湖" in second or "杭州" in second, f"multi-turn context lost: {second!r}"
        page.screenshot(path=f"{OUT}/03_multiturn.png")

        assert not errors, f"console errors: {errors}"
        print("BROWSER_OK")
        browser.close()


if __name__ == "__main__":
    main()
