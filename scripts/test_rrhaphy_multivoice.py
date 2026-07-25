#!/usr/bin/env python3
"""
TTS→STT round-trip test for rare medical "-rrhaphy" terms.
10 Deepgram TTS voices × 8 terms × 3 trials × 2 conditions (no keyterms / with keyterms).
Uses /api/tts-transcribe on deepgram-python-stt.fly.dev.
"""
import os
import httpx
import csv
import sys
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Output lands next to this script. DO NOT hardcode an absolute /coding path:
# it pins the harness to one machine.
SCRIPT_DIR = Path(__file__).resolve().parent

BASE_URL = "https://deepgram-python-stt.fly.dev"
# The API is token-gated. Export APP_ACCESS_TOKEN (same value as the fly secret)
# so the harness can reach /api/tts-transcribe.
APP_TOKEN = os.getenv("APP_ACCESS_TOKEN", "")
AUTH_HEADERS = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}

STT_MODEL = "nova-3-medical"

VOICES = [
    "aura-2-asteria-en",
    "aura-2-apollo-en",
    "aura-2-arcas-en",
    "aura-2-luna-en",
    "aura-2-orion-en",
    "aura-2-athena-en",
    "aura-2-zeus-en",
    "aura-2-hera-en",
    "aura-2-orpheus-en",
    "aura-2-thalia-en",
]

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

TEMPLATES = [
    "The surgeon performed a {term} to repair the damaged tissue.",
    "Following the examination, the physician recommended a {term} as the most appropriate intervention.",
    "The operative report documented a successful {term} with no complications noted.",
]


def extract_transcript(result: dict) -> str:
    try:
        return result["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError):
        return "[ERROR]"


def classify_match(term: str, transcript: str) -> str:
    """Classify how the STT handled the term.
    Returns: 'exact', 'single_r', or 'miss'.
    """
    t_lower = transcript.lower()
    term_lower = term.lower()
    if term_lower in t_lower:
        return "exact"
    # Build single-r variant: e.g. "colporrhaphy" -> "colporaphy", "colporhaphy"
    # The double-r can appear as "rrh" -> "rh" or "rr" -> "r"
    single_r_variants = set()
    # rrhaphy -> rhaphy
    single_r_variants.add(term_lower.replace("rrhaphy", "rhaphy"))
    # rrhaphy -> raphy
    single_r_variants.add(term_lower.replace("rrhaphy", "raphy"))
    # rrhaphy -> rraphy (dropped h)
    single_r_variants.add(term_lower.replace("rrhaphy", "rraphy"))
    # rrhaphy -> raphe (alternate ending)
    single_r_variants.add(term_lower.replace("rrhaphy", "raphe"))
    # orr -> or (single r in the join)
    single_r_variants.add(term_lower.replace("orrh", "orh"))
    single_r_variants.add(term_lower.replace("orrh", "or"))
    # Also check without "aphy" ending variations
    stem = term_lower.replace("rrhaphy", "")
    single_r_variants.add(stem + "rophy")
    single_r_variants.add(stem + "rphy")
    single_r_variants.add(stem + "rafy")
    single_r_variants.add(stem + "raphy")
    single_r_variants.add(stem + "rrhafy")
    single_r_variants.add(stem + "rafi")
    single_r_variants.add(stem + "rafy")
    single_r_variants.add(stem + "raphey")
    single_r_variants.add(stem + "rafe")
    single_r_variants.discard(term_lower)  # don't count exact as single_r
    for variant in single_r_variants:
        if variant in t_lower:
            return "single_r"
    return "miss"


def run_single(client: httpx.Client, voice: str, term: str, trial: int, template: str, use_keyterms: bool) -> dict:
    sentence = template.format(term=term)
    stt_params = {"model": STT_MODEL, "smart_format": True}
    if use_keyterms:
        stt_params["keyterms"] = list(TERMS)

    payload = {
        "text": sentence,
        "tts_model": voice,
        "tts_provider": "deepgram",
        "stt_params": stt_params,
        "mode": "batch",
    }
    try:
        resp = client.post(f"{BASE_URL}/api/tts-transcribe", json=payload, headers=AUTH_HEADERS)
        resp.raise_for_status()
        transcript = extract_transcript(resp.json())
    except Exception as e:
        transcript = f"[ERROR: {e}]"

    category = classify_match(term, transcript)
    return {
        "voice": voice,
        "term": term,
        "trial": trial,
        "input_sentence": sentence,
        "transcript": transcript,
        "match": category,
        "keyterms": use_keyterms,
    }


def main():
    total = len(VOICES) * len(TERMS) * len(TEMPLATES) * 2
    print(f"TTS→STT Multi-Voice -rrhaphy Test")
    print(f"STT: {STT_MODEL} | Voices: {len(VOICES)} | Terms: {len(TERMS)} | Trials: 3 | Conditions: 2")
    print(f"Total requests: {total}")
    print(f"Started: {datetime.now().isoformat()}\n")

    # Build all jobs
    jobs = []
    for use_kt in [False, True]:
        for voice in VOICES:
            for term in TERMS:
                for trial_idx, template in enumerate(TEMPLATES):
                    jobs.append((voice, term, trial_idx + 1, template, use_kt))

    # Run in parallel — 20 workers keeps good throughput without hammering the server
    all_rows = [None] * len(jobs)
    done = 0

    def _run_job(idx, job):
        voice, term, trial, template, use_kt = job
        with httpx.Client(timeout=120.0) as client:
            return idx, run_single(client, voice, term, trial, template, use_kt)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_run_job, i, job): i for i, job in enumerate(jobs)}
        for future in as_completed(futures):
            idx, row = future.result()
            all_rows[idx] = row
            done += 1
            if done % 20 == 0 or done == total:
                print(f"  [{done}/{total}] completed...")

    def _counts(rows, kt_filter):
        """Return (exact, single_r, miss) counts for a filtered set."""
        filtered = [r for r in rows if r["keyterms"] == kt_filter]
        exact = sum(1 for r in filtered if r["match"] == "exact")
        single_r = sum(1 for r in filtered if r["match"] == "single_r")
        miss = sum(1 for r in filtered if r["match"] == "miss")
        return exact, single_r, miss

    n_per_term = len(VOICES) * 3
    n_per_voice = len(TERMS) * 3
    n_total = len(TERMS) * len(VOICES) * 3

    # Per-term summary
    print(f"\n{'='*100}")
    print(f"  AGGREGATE SUMMARY (across {len(VOICES)} voices, 3 trials each = {n_per_term} samples/term)")
    print(f"{'='*100}")

    hdr = (f"{'':>22}"
           f"  {'--- No Keyterms ---':^30}"
           f"  {'--- With Keyterms ---':^30}")
    sub = (f"{'Term':<22}"
           f"  {'Exact':>6} {'Single-R':>9} {'Miss':>6}"
           f"  {'Exact':>6} {'Single-R':>9} {'Miss':>6}")
    print(f"\n{hdr}")
    print(sub)
    print("-" * 90)

    totals = {"no_kt": [0, 0, 0], "kt": [0, 0, 0]}
    for term in TERMS:
        term_rows = [r for r in all_rows if r["term"] == term]
        e0, s0, m0 = _counts(term_rows, False)
        e1, s1, m1 = _counts(term_rows, True)
        totals["no_kt"] = [totals["no_kt"][i] + v for i, v in enumerate([e0, s0, m0])]
        totals["kt"] = [totals["kt"][i] + v for i, v in enumerate([e1, s1, m1])]
        print(f"{term:<22}"
              f"  {e0:>3}/{n_per_term:<3} {s0:>6}/{n_per_term:<3} {m0:>3}/{n_per_term:<3}"
              f"  {e1:>3}/{n_per_term:<3} {s1:>6}/{n_per_term:<3} {m1:>3}/{n_per_term:<3}")

    print("-" * 90)
    print(f"{'TOTAL':<22}"
          f"  {totals['no_kt'][0]:>3}/{n_total:<4}"
          f"{totals['no_kt'][1]:>5}/{n_total:<4}"
          f"{totals['no_kt'][2]:>3}/{n_total:<4}"
          f" {totals['kt'][0]:>3}/{n_total:<4}"
          f"{totals['kt'][1]:>5}/{n_total:<4}"
          f"{totals['kt'][2]:>3}/{n_total:<4}")

    # Per-voice summary
    print(f"\n{'='*100}")
    print(f"  PER-VOICE SUMMARY ({n_per_voice} samples per voice per condition)")
    print(f"{'='*100}")

    hdr_v = (f"{'':>22}"
             f"  {'--- No Keyterms ---':^30}"
             f"  {'--- With Keyterms ---':^30}")
    sub_v = (f"{'Voice':<22}"
             f"  {'Exact':>6} {'Single-R':>9} {'Miss':>6}"
             f"  {'Exact':>6} {'Single-R':>9} {'Miss':>6}")
    print(f"\n{hdr_v}")
    print(sub_v)
    print("-" * 90)

    for voice in VOICES:
        v_short = voice.replace("aura-2-", "").replace("-en", "")
        voice_rows = [r for r in all_rows if r["voice"] == voice]
        e0, s0, m0 = _counts(voice_rows, False)
        e1, s1, m1 = _counts(voice_rows, True)
        print(f"{v_short:<22}"
              f"  {e0:>3}/{n_per_voice:<3} {s0:>6}/{n_per_voice:<3} {m0:>3}/{n_per_voice:<3}"
              f"  {e1:>3}/{n_per_voice:<3} {s1:>6}/{n_per_voice:<3} {m1:>3}/{n_per_voice:<3}")

    # Save CSV
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = str(SCRIPT_DIR / f"rrhaphy_multivoice_{ts}.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\nResults saved to {csv_path}")
    print(f"Finished: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
