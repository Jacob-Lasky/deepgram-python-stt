#!/usr/bin/env python3
"""
TTS→STT round-trip test for time pronunciation.

Tests whether Deepgram TTS mispronounces times (e.g., "three fifty-one AM"
spoken as "three hundred fifty-one AM"). Generates audio via TTS, transcribes
via STT, and flags any transcript containing "hundred".

Usage:
    uv run scripts/test_time_pronunciation.py
    uv run scripts/test_time_pronunciation.py --hours 8 9 10 --minutes 0 15 30 45
    uv run scripts/test_time_pronunciation.py --all          # every minute 8AM-6PM
    uv run scripts/test_time_pronunciation.py --voice aura-2-thalia-en
    uv run scripts/test_time_pronunciation.py --concurrency 20
"""
import argparse
import asyncio
import csv
import io
import json
import os
import sys
import time as time_mod
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# --- Number-to-words conversion (simple, covers 0-59 and hours 1-12) ---

# Output lands next to this script. DO NOT hardcode an absolute /coding path:
# it pins the harness to one machine.
SCRIPT_DIR = Path(__file__).resolve().parent

ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty"]


def num_to_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    ten, one = divmod(n, 10)
    return f"{TENS[ten]}-{ONES[one]}" if one else TENS[ten]


def time_to_spelled(hour: int, minute: int, period: str) -> str:
    """Convert time to spelled-out words. e.g. (3, 51, 'AM') -> 'three fifty-one AM'"""
    h = num_to_words(hour)
    if minute == 0:
        return f"{h} {period}"
    elif minute < 10:
        return f"{h} oh {num_to_words(minute)} {period}"
    else:
        return f"{h} {num_to_words(minute)} {period}"


def time_to_numeral(hour: int, minute: int, period: str) -> str:
    """e.g. (3, 51, 'AM') -> '3:51 AM'"""
    return f"{hour}:{minute:02d} {period}"


def make_sentence(time_str: str) -> str:
    return (
        f"I'm reaching out regarding your appointment tomorrow at {time_str}, "
        f"will you be joining us at that time?"
    )


# --- API calls ---

TTS_URL = "https://api.deepgram.com/v1/speak"
STT_URL = "https://api.deepgram.com/v1/listen"


async def tts_then_stt(
    client: httpx.AsyncClient,
    api_key: str,
    text: str,
    voice: str,
) -> str:
    """Send text to TTS, pipe audio to STT, return transcript."""
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}

    # TTS
    tts_resp = await client.post(
        TTS_URL,
        params={"model": voice, "encoding": "mp3"},
        headers=headers,
        json={"text": text},
        timeout=30,
    )
    tts_resp.raise_for_status()
    audio = tts_resp.content

    # STT (pre-recorded)
    stt_headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/mpeg",
    }
    stt_resp = await client.post(
        STT_URL,
        params={"model": "nova-3", "smart_format": "true", "punctuate": "true"},
        headers=stt_headers,
        content=audio,
        timeout=30,
    )
    stt_resp.raise_for_status()
    data = stt_resp.json()

    transcript = (
        data.get("results", {})
        .get("channels", [{}])[0]
        .get("alternatives", [{}])[0]
        .get("transcript", "")
    )
    return transcript


# --- Test runner ---


async def run_test(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    api_key: str,
    voice: str,
    hour: int,
    minute: int,
    period: str,
    fmt: str,
    results: list,
    progress: dict,
):
    if fmt == "spelled":
        time_str = time_to_spelled(hour, minute, period)
    else:
        time_str = time_to_numeral(hour, minute, period)

    sentence = make_sentence(time_str)

    async with sem:
        try:
            transcript = await tts_then_stt(client, api_key, sentence, voice)
        except Exception as e:
            transcript = f"ERROR: {e}"

    has_hundred = "hundred" in transcript.lower()

    result = {
        "hour": hour,
        "minute": minute,
        "period": period,
        "format": fmt,
        "input_time": time_str,
        "input_sentence": sentence,
        "transcript": transcript,
        "has_hundred": has_hundred,
    }
    results.append(result)

    progress["done"] += 1
    flag = " *** HUNDRED DETECTED ***" if has_hundred else ""
    print(
        f"  [{progress['done']}/{progress['total']}] "
        f"{fmt:8s} {time_str:25s} -> {transcript[:80]}{flag}"
    )


async def main():
    parser = argparse.ArgumentParser(description="TTS→STT time pronunciation test")
    parser.add_argument(
        "--hours", nargs="+", type=int, default=None,
        help="Hours to test (default: 8-12, 1-5 representative set)",
    )
    parser.add_argument(
        "--minutes", nargs="+", type=int, default=None,
        help="Minutes to test (default: representative set)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Test every minute from 8AM to 6PM",
    )
    parser.add_argument(
        "--voice", default="aura-2-asteria-en",
        help="TTS voice model (default: aura-2-asteria-en)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=10,
        help="Max concurrent API calls (default: 10)",
    )
    parser.add_argument(
        "--output", default=None,
        help="CSV output file (default: auto-named in scripts/)",
    )
    parser.add_argument(
        "--formats", nargs="+", default=["spelled", "numeral"],
        choices=["spelled", "numeral"],
        help="Which input formats to test (default: both)",
    )
    args = parser.parse_args()

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("Error: DEEPGRAM_API_KEY not set")
        sys.exit(1)

    # Build time list
    if args.all:
        # 8:00 AM through 5:59 PM = hours 8-17
        times = []
        for h24 in range(8, 18):
            for m in range(60):
                period = "AM" if h24 < 12 else "PM"
                h12 = h24 if h24 <= 12 else h24 - 12
                if h12 == 0:
                    h12 = 12
                times.append((h12, m, period))
    else:
        hours = args.hours or [8, 9, 10, 11, 12, 1, 2, 3, 4, 5]
        minutes = args.minutes or [
            0, 1, 5, 10, 13, 15, 20, 21, 30, 31, 33, 40, 45, 50, 51, 55, 59,
        ]
        # Map hours to AM/PM
        times = []
        for h in hours:
            period = "AM" if h >= 8 and h <= 11 else "PM"
            for m in minutes:
                times.append((h, m, period))

    total_tests = len(times) * len(args.formats)
    print(f"\n=== Time Pronunciation Test ===")
    print(f"Voice: {args.voice}")
    print(f"Formats: {', '.join(args.formats)}")
    print(f"Times: {len(times)} unique times x {len(args.formats)} formats = {total_tests} tests")
    print(f"Concurrency: {args.concurrency}")
    print()

    sem = asyncio.Semaphore(args.concurrency)
    results = []
    progress = {"done": 0, "total": total_tests}

    start = time_mod.monotonic()

    async with httpx.AsyncClient() as client:
        tasks = []
        for hour, minute, period in times:
            for fmt in args.formats:
                tasks.append(
                    run_test(sem, client, api_key, args.voice, hour, minute, period, fmt, results, progress)
                )
        await asyncio.gather(*tasks)

    elapsed = time_mod.monotonic() - start

    # Summary
    hundred_cases = [r for r in results if r["has_hundred"]]
    errors = [r for r in results if r["transcript"].startswith("ERROR")]

    print(f"\n{'='*60}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests:       {len(results)}")
    print(f"Errors:            {len(errors)}")
    print(f"'Hundred' found:   {len(hundred_cases)} / {len(results) - len(errors)} successful")
    print(f"Time elapsed:      {elapsed:.1f}s")

    if hundred_cases:
        print(f"\n{'='*60}")
        print(f"FAILURES (transcript contains 'hundred'):")
        print(f"{'='*60}")
        for r in hundred_cases:
            print(f"  [{r['format']:8s}] Input: {r['input_time']}")
            print(f"           STT:   {r['transcript']}")
            print()

    # Breakdown by format
    for fmt in args.formats:
        fmt_results = [r for r in results if r["format"] == fmt and not r["transcript"].startswith("ERROR")]
        fmt_hundreds = [r for r in fmt_results if r["has_hundred"]]
        pct = (len(fmt_hundreds) / len(fmt_results) * 100) if fmt_results else 0
        print(f"  {fmt:8s}: {len(fmt_hundreds)}/{len(fmt_results)} contain 'hundred' ({pct:.1f}%)")

    # Save CSV
    if not args.output:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = str(SCRIPT_DIR / f"time_pronunciation_{ts}.csv")

    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "hour", "minute", "period", "format", "input_time",
            "input_sentence", "transcript", "has_hundred",
        ])
        writer.writeheader()
        writer.writerows(sorted(results, key=lambda r: (r["format"], r["hour"], r["minute"])))

    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
