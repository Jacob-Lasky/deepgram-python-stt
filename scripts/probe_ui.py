#!/usr/bin/env python3
"""Visual/DOM probe for UI changes: proves a field rendered, or is really gone.

WHY THIS EXISTS: a passing test suite is not evidence about the DOM. Contract
tests assert on fixtures whose shape we wrote ourselves, so they pass while the
rendered page differs. Any change to templates/index.html or static/app.js needs
an artifact from a real browser, and without a committed harness every such
change re-invents this setup from scratch.

Usage:
    # Terminal 1
    DEEPGRAM_API_KEY=probe-key uv run uvicorn app:app --port 8899
    # Terminal 2
    uv run python scripts/probe_ui.py
    uv run python scripts/probe_ui.py --expect-removed Keywords --expect-present Keyterms
    uv run python scripts/probe_ui.py --mode streaming \
        --expect-absent "Filler Words" Utterances Paragraphs

Exits non-zero when an expectation is unmet, so it works as a gate rather than
just a screenshot printer.

Requires the browser binary once: `uv run playwright install chromium`.

Writes screenshots next to this script under probe-artifacts/, which is
gitignored: attach them to the PR, do not commit them.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ARTIFACTS = Path(__file__).resolve().parent / "probe-artifacts"


def _param_key(label: str) -> str:
    """UI label -> Alpine params key. "Filler Words" -> "filler_words"."""
    return label.strip().lower().replace(" ", "_")


async def probe(
    url: str,
    expect_present: list[str],
    expect_absent: list[str],
    expect_removed: list[str],
    mode: str | None,
) -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    failures: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 1400})

        console_errors: list[str] = []
        page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        try:
            await page.goto(url, wait_until="networkidle")
        except Exception as exc:
            # A raw ERR_CONNECTION_REFUSED traceback reads like a probe bug. It
            # almost always means the app is not running; say so.
            await browser.close()
            print(f"could not load {url}: {exc}", file=sys.stderr)
            print(
                "\nIs the app running? Start it with:\n"
                "  DEEPGRAM_API_KEY=probe-key uv run uvicorn app:app --port 8899",
                file=sys.stderr,
            )
            return 1
        await page.wait_for_timeout(1200)

        # Expand every collapsible param section, or a field can be "absent"
        # only because its accordion happened to be shut. Some params are
        # mode-gated (batch-only features are hidden while streaming), so --mode
        # picks which surface is being asserted.
        await page.evaluate(
            """(m) => {
            const d = Alpine.$data(document.querySelector('[x-data]'));
            if (m) d.mode = m;
            Object.keys(d.sections || {}).forEach(k => d.sections[k] = true);
        }""",
            mode,
        )
        await page.wait_for_timeout(700)
        if mode:
            print(f"mode: {mode}")

        # VISIBLE labels only, and both label flavours (field rows use
        # .field-label, checkboxes use <label for>). A mode-gated control is
        # still in the DOM with x-show false, so presence is not the question —
        # whether the user can see it is.
        labels = await page.evaluate("""() => {
            const seen = document.querySelectorAll('.field-label, .checkbox-item label');
            return [...seen]
                .filter(e => e.offsetParent !== null)
                .map(e => e.textContent.trim());
        }""")
        bindings = await page.evaluate("""() => {
            return [...document.querySelectorAll('[x-model]')]
                .filter(e => e.offsetParent !== null)
                .map(e => e.getAttribute('x-model'));
        }""")
        params = await page.evaluate(
            "() => JSON.parse(JSON.stringify("
            "Alpine.$data(document.querySelector('[x-data]')).params))"
        )

        print(f"fields rendered: {len(labels)}")
        print(json.dumps(sorted(labels), indent=1))
        print(f"\nconsole errors: {console_errors or 'none'}")
        if console_errors:
            failures.append(f"console errors on load: {console_errors}")

        for name in expect_present:
            if name not in labels:
                failures.append(f"expected field {name!r} to render; it did not")

        # Two distinct kinds of absence, deliberately NOT conflated:
        #  --expect-absent   not visible to the user (e.g. a batch-only control
        #                    while streaming). The param legitimately stays in
        #                    Alpine state; only the control is hidden.
        #  --expect-removed  gone entirely — no label, no binding, and not in
        #                    state. A removed label with a live binding left
        #                    behind still ships the param, which is the failure
        #                    the state check catches.
        for name in expect_absent:
            key = _param_key(name)
            if name in labels:
                failures.append(f"{name!r} is still visible")
            if f"params.{key}" in bindings:
                failures.append(f"{name!r} still has a visible x-model binding")

        for name in expect_removed:
            key = _param_key(name)
            if name in labels:
                failures.append(f"{name!r} is still visible")
            if f"params.{key}" in bindings:
                failures.append(f"{name!r} still has a visible x-model binding")
            if key in params:
                failures.append(f"{key!r} is still in the live Alpine params state")

        # Mode in the filename so a streaming run does not overwrite a batch one.
        shot = ARTIFACTS / f"ui_{mode or 'default'}.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"\nartifact: {shot}")
        await browser.close()

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK: all DOM expectations met")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8899/")
    ap.add_argument("--expect-present", nargs="*", default=[])
    ap.add_argument(
        "--expect-absent", nargs="*", default=[],
        help="not visible to the user; may still exist in Alpine state (mode-gated)",
    )
    ap.add_argument(
        "--expect-removed", nargs="*", default=[],
        help="gone entirely: no label, no binding, and not in Alpine state",
    )
    ap.add_argument(
        "--mode", default=None,
        help="set the app mode first (streaming|batch); some controls are mode-gated",
    )
    a = ap.parse_args()
    return asyncio.run(
        probe(a.url, a.expect_present, a.expect_absent, a.expect_removed, a.mode)
    )


if __name__ == "__main__":
    sys.exit(main())
