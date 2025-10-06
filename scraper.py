import math
import time
import sys
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Keep a persistent Playwright browser to avoid cold starts
_PLAY: Optional[Tuple[Any, Any, Any]] = None  # (p, browser, context)


def _launch():
    global _PLAY
    from playwright.sync_api import sync_playwright
    if _PLAY is None:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        _PLAY = (p, browser, context)
    else:
        p, browser, context = _PLAY
    page = context.new_page()
    return p, browser, context, page


def _ensure_playwright_runtime() -> None:
    try:
        import playwright  # noqa: F401
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])  # install lib
    # Ensure chromium is installed
    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"])  # --with-deps harmless on Windows
    except Exception:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])  # fallback


def _to_file_url(path: Path) -> str:
    return path.resolve().as_uri()


def _click_by_text(page, text: str) -> bool:
    try:
        locator = page.get_by_text(text, exact=False)
        locator.first.click(timeout=8000)
        return True
    except Exception:
        return False


def _wait_ready(page) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        for c in ("Pred. Results", "Plans", "Draws"):
            try:
                if page.get_by_text(c, exact=False).count() > 0:
                    return
            except Exception:
                pass
        time.sleep(0.2)


def _extract(page) -> List[Dict[str, Any]]:
    script = r"""
        () => {
          const labelMatches = Array.from(document.querySelectorAll('*'))
            .filter(el => /hit\s*rate/i.test(el.textContent || ''));
          const containers = new Set();
          function closestContainer(node) {
            if (!node) return null;
            return node.closest('li, article, section, div, tr, tbody, card, .card, .item, .row');
          }
          for (const el of labelMatches) {
            const c = closestContainer(el);
            if (c) containers.add(c);
          }
          const unique = Array.from(containers);
          function extractFromContainer(c) {
            const text = (c.innerText || '').replace(/\s+/g, ' ').trim();
            let name = '';
            const title = c.querySelector('h1,h2,h3,h4,h5,strong,b,.name,.title');
            if (title && title.textContent) {
              name = title.textContent.trim();
            } else {
              const parts = text.split(/\s{2,}|\n/).map(s => s.trim()).filter(Boolean);
              if (parts.length) name = parts[0];
            }
            let hitRate = null;
            const hitNode = Array.from(c.querySelectorAll('*')).find(el => /hit\s*rate/i.test(el.textContent || ''));
            if (hitNode && hitNode.textContent) {
              const m = hitNode.textContent.match(/(\d{1,3}(?:\.\d+)?)\s*%/);
              if (m) hitRate = parseFloat(m[1]);
            }
            if (hitRate == null) {
              const m = text.match(/hit\s*rate[^\d]*(\d{1,3}(?:\.\d+)?)%/i);
              if (m) hitRate = parseFloat(m[1]);
            }
            let trade = null;
            const tradeNode = Array.from(c.querySelectorAll('*')).find(el => /trade/i.test(el.textContent || ''));
            if (tradeNode && tradeNode.textContent) {
              const m2 = tradeNode.textContent.match(/(\d+[\d,]*)(?:\s*trades?)?/i);
              if (m2) trade = parseFloat(m2[1].replace(/,/g, ''));
            }
            if (trade == null) {
              const m2 = text.match(/trade[^\d]*(\d+[\d,]*)/i);
              if (m2) trade = parseFloat(m2[1].replace(/,/g, ''));
            }
            let plan = '';
            const planNode = Array.from(c.querySelectorAll('*')).find(el => /\bplan\b/i.test(el.textContent || ''));
            if (planNode && planNode.textContent) {
              const t = planNode.textContent.replace(/\s+/g, ' ');
              const m3 = t.match(/plan\s*[:\-]?\s*([^|\n\r]+)/i);
              if (m3) plan = m3[1].trim();
            }
            if (!plan) {
              const m3 = text.match(/plan\s*[:\-]?\s*([^|\n\r]+)/i);
              if (m3) plan = m3[1].trim();
            }
            return { name, hitRate, trade, plan, rawText: text };
          }
          return unique.map(extractFromContainer);
        }
    """
    return page.evaluate(script) or []


def _score(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in items:
        hit = float(r.get("hitRate") or 0.0)
        trd = float(r.get("trade") or 0.0)
        score = hit * (1.0 + math.log1p(max(trd, 0.0)))
        out.append({
            "name": (r.get("name") or "").strip(),
            "hit_rate": hit if hit else None,
            "trade": trd if trd else None,
            "plan": (r.get("plan") or "").strip(),
            "score": score,
        })
    out.sort(key=lambda x: (x["score"], x["hit_rate"] or -1.0, x["trade"] or -1.0), reverse=True)
    return out[:20]


def scrape_plans(html_path: Path) -> Dict[str, Any]:
    from playwright.sync_api import TimeoutError
    _ensure_playwright_runtime()
    p, browser, context, page = _launch()
    try:
        page.set_default_timeout(12000)
        page.goto(_to_file_url(html_path) + "#/wingo_30s", wait_until="load")
        _wait_ready(page)
        # Navigate to Pred. Results -> Plans
        _click_by_text(page, "Pred. Results")
        _click_by_text(page, "Plans")
        page.wait_for_timeout(800)
        raw = _extract(page)
        top = _score(raw)
        best = top[0] if top else None
        return {"items": top, "best": best}
    finally:
        try:
            page.close()
        except Exception:
            pass


def analyze_big_small(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"decision": None, "confidence": 0.0, "reasons": []}
    # Map plan text to Big/Small votes using stronger heuristics
    def vote(plan: str) -> Optional[str]:
        t = (plan or "").lower()
        if "big" in t and "small" not in t:
            return "Big"
        if "small" in t and "big" not in t:
            return "Small"
        # numeric hints (e.g., 6-9 big, 0-5 small) if present
        if any(ch in t for ch in list("6789")) and not any(ch in t for ch in list("012345")):
            return "Big"
        if any(ch in t for ch in list("012345")) and not any(ch in t for ch in list("6789")):
            return "Small"
        # common synonyms
        if "high" in t or "up" in t:
            return "Big"
        if "low" in t or "down" in t:
            return "Small"
        return None

    votes: List[Dict[str, Any]] = []
    for it in items:
        v = vote(it.get("plan") or "")
        if not v:
            continue
        # Emphasize high hit-rate and non-trivial trade; small epsilon to avoid zeroing
        weight = (max(it.get("hit_rate") or 0.0, 0.0) ** 1.15) * (1.0 + math.log1p(max(it.get("trade") or 0.0, 0.0)))
        votes.append({"side": v, "weight": weight, "name": it.get("name"), "plan": it.get("plan")})

    big_score = sum(v["weight"] for v in votes if v["side"] == "Big")
    small_score = sum(v["weight"] for v in votes if v["side"] == "Small")
    total = big_score + small_score
    if total <= 0:
        return {"decision": None, "confidence": 0.0, "reasons": []}
    if big_score >= small_score:
        decision = "Big"
        confidence = big_score / total
    else:
        decision = "Small"
        confidence = small_score / total

    # Top contributors
    votes.sort(key=lambda x: x["weight"], reverse=True)
    reasons = votes[:5]
    return {"decision": decision, "confidence": confidence, "reasons": reasons}


