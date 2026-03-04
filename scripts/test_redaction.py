#!/usr/bin/env python3
"""
Test Deepgram redaction params: streaming vs batch comparison.
Usage: uv run scripts/test_redaction.py --entities credit_card ssn --audio path/to/file.wav [--url https://...]
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from stt.client import STTClient
from stt.options import Mode


def main():
    parser = argparse.ArgumentParser(description="Test Deepgram redaction")
    parser.add_argument("--entities", nargs="+", default=["pci", "ssn"],
                        help="Redaction entity types to test")
    parser.add_argument("--audio", help="Path to audio file")
    parser.add_argument("--url", help="URL to audio file (alternative to --audio)")
    parser.add_argument("--model", default="nova-3")
    parser.add_argument("--streaming-only", action="store_true")
    parser.add_argument("--batch-only", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("Error: DEEPGRAM_API_KEY not set")
        sys.exit(1)

    audio_source = args.url or args.audio
    if not audio_source:
        print("Error: --audio or --url required")
        sys.exit(1)

    client = STTClient(api_key)
    params = {
        "model": args.model,
        "redact": args.entities,
        "smart_format": True,
    }

    print(f"\n=== Deepgram Redaction Test ===")
    print(f"Entities: {args.entities}")
    print(f"Audio: {audio_source}")
    print(f"Model: {args.model}\n")

    # Batch test
    if not args.streaming_only:
        print("--- BATCH (Pre-recorded) ---")
        batch_url = client.build_url(params, Mode.BATCH)
        print(f"URL: {batch_url}")
        try:
            result = client.transcribe_batch(audio_source, params)
            transcript = (
                result.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
            )
            print(f"Transcript: {transcript}\n")
        except Exception as e:
            print(f"Error: {e}\n")

    if not args.batch_only and not args.streaming_only:
        print("--- STREAMING ---")
        print("(Streaming requires an audio file to be chunked)")
        streaming_url = client.build_url(params, Mode.STREAMING)
        print(f"URL: {streaming_url}")
        print("(Run with --streaming-only to test streaming if audio file provided)\n")


if __name__ == "__main__":
    main()
