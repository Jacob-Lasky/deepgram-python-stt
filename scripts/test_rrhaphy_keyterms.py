#!/usr/bin/env python3
"""
TTS→STT round-trip test for rare medical "-rrhaphy" terms.
Runs triplicates without keyterms, then triplicates with keyterms.
Uses the /api/tts-transcribe endpoint on deepgram-python-stt.fly.dev.
"""
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Reads APP_ACCESS_TOKEN from the repo's gitignored .env so the harness gets the
# privileged (un-rate-limited) tier. Must run before the getenv calls below.
load_dotenv()

# Output lands next to this script. DO NOT hardcode an absolute /coding path:
# it pins the harness to one machine.
SCRIPT_DIR = Path(__file__).resolve().parent

BASE_URL = "https://deepgram-python-stt.fly.dev"
# The API is token-gated. Export APP_ACCESS_TOKEN (same value as the fly secret)
# so the harness can reach /api/tts-transcribe.
APP_TOKEN = os.getenv("APP_ACCESS_TOKEN", "")
AUTH_HEADERS = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}

TTS_MODEL = "aura-2-asteria-en"
STT_MODEL = "nova-3-medical"

TERMS = [
    "perineorrhaphy",
    "tarsorrhaphy",
    "colporrhaphy",
    "herniorrhaphy",
    "capsulorrhaphy",
    "splenorrhaphy",
    "hymenorrhaphy",
    "duodenorrhaphy",
]

# Three different sentence templates per term for triplicates
TEMPLATES = [
    "The surgeon performed a {term} to repair the damaged tissue.",
    "Following the examination, the physician recommended a {term} as the most appropriate intervention.",
    "The operative report documented a successful {term} with no complications noted.",
]


def extract_transcript(result: dict) -> str:
    """Pull transcript text from Deepgram batch response."""
    try:
        return (
            result["results"]["channels"][0]["alternatives"][0]["transcript"]
        )
    except (KeyError, IndexError):
        return f"[ERROR: unexpected response shape: {json.dumps(result)[:200]}]"


def run_test(use_keyterms: bool) -> list[dict]:
    """Run all term/template combos. Returns list of result dicts."""
    rows = []
    stt_params = {"model": STT_MODEL, "smart_format": True}
    if use_keyterms:
        # Keyterm Prompting: bare terms, no intensifiers (that is `keywords`).
        # Canonical Deepgram wire name. stt.options.PARAM_ALIASES also maps the
        # UI's plural "keyterms", but be explicit here: sending the wrong name
        # to the batch API is silently ignored, which invalidated an earlier run.
        stt_params["keyterm"] = list(TERMS)

    label = "WITH keyterms" if use_keyterms else "WITHOUT keyterms"
    print(f"\n{'='*60}")
    print(f"  Running {label}")
    print(f"{'='*60}")

    with httpx.Client(timeout=120.0) as client:
        for term in TERMS:
            for i, template in enumerate(TEMPLATES):
                sentence = template.format(term=term)
                payload = {
                    "text": sentence,
                    "tts_model": TTS_MODEL,
                    "tts_provider": "deepgram",
                    "stt_params": stt_params,
                    "mode": "batch",
                }
                print(f"\n  [{term}] trial {i+1}: {sentence}")
                try:
                    resp = client.post(f"{BASE_URL}/api/tts-transcribe", json=payload, headers=AUTH_HEADERS)
                    resp.raise_for_status()
                    data = resp.json()
                    transcript = extract_transcript(data)
                except Exception as e:
                    transcript = f"[REQUEST ERROR: {e}]"

                match = term.lower() in transcript.lower()
                print(f"    → {transcript}")
                print(f"    {'✓ MATCH' if match else '✗ MISS'}")

                rows.append({
                    "term": term,
                    "trial": i + 1,
                    "input_sentence": sentence,
                    "transcript": transcript,
                    "match": match,
                    "keyterms": use_keyterms,
                })
    return rows


def print_summary(rows: list[dict]):
    """Print a summary table comparing with/without keyterms."""
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    print(f"\n{'Term':<22} {'No KT (3 trials)':<20} {'With KT (3 trials)':<20}")
    print("-" * 62)

    for term in TERMS:
        no_kt = sum(1 for r in rows if r["term"] == term and not r["keyterms"] and r["match"])
        with_kt = sum(1 for r in rows if r["term"] == term and r["keyterms"] and r["match"])
        print(f"{term:<22} {no_kt}/3{' ':>15} {with_kt}/3")

    no_kt_total = sum(1 for r in rows if not r["keyterms"] and r["match"])
    with_kt_total = sum(1 for r in rows if r["keyterms"] and r["match"])
    total_each = len(TERMS) * 3
    print("-" * 62)
    print(f"{'TOTAL':<22} {no_kt_total}/{total_each}{' ':>14} {with_kt_total}/{total_each}")


def main():
    print(f"TTS→STT Round-Trip: -rrhaphy Medical Terms")
    print(f"TTS model: {TTS_MODEL} | STT model: {STT_MODEL}")
    print(f"Endpoint: {BASE_URL}/api/tts-transcribe")
    print(f"Timestamp: {datetime.now().isoformat()}")

    all_rows = []
    all_rows.extend(run_test(use_keyterms=False))
    all_rows.extend(run_test(use_keyterms=True))

    print_summary(all_rows)

    # Save CSV
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = str(SCRIPT_DIR / f"rrhaphy_test_{ts}.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nResults saved to {csv_path}")


if __name__ == "__main__":
    main()
