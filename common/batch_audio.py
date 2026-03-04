import requests
import json
import os
import logging
import mimetypes
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("BatchAudio")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"


def process_audio(
    audio_path: str,
    params: dict,
    deepgram_api_url: str = DEEPGRAM_API_URL,
    verbose: bool = False,
):
    print(f"Processing audio file: {audio_path}")
    print(f"Parameters: {params}")

    if audio_path.startswith("http"):
        url_path = audio_path.split("?")[0]  # Remove query parameters
        json_data = {"url": audio_path}
        content_type = "application/json"
        audio_data = None
        print(f"Processing remote file: {url_path}")
    else:
        print("Processing local file")
        try:
            with open(audio_path, "rb") as audio_file:
                audio_data = audio_file.read()
            print(f"Read {len(audio_data)} bytes from file")
        except Exception as e:
            print(f"Error reading file: {str(e)}")
            raise
        json_data = None
        content_type, _ = mimetypes.guess_type(audio_path)
        if not content_type or not content_type.startswith("audio/"):
            content_type = "audio/wav"
        print(f"Detected content type: {content_type}")

    headers = {
        "accept": "application/json",
        "content-type": content_type,
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
    }
    print(f"Request URL: {deepgram_api_url}")
    print(f"Request params: {params}")

    try:
        print(f"Making HTTPS request to: {deepgram_api_url}")
        response = requests.post(
            deepgram_api_url,
            params=params,
            data=audio_data,
            json=json_data,
            headers=headers,
            timeout=600,
            verify=True,
        )
        print(f"Response status code: {response.status_code}")

        if response.status_code != 200:
            print(f"Error response: {response.text}")
            try:
                error_json = response.json()
                print(f"Error JSON: {error_json}")
            except Exception:
                pass
            return {
                "error": f"HTTP {response.status_code}: {response.text}",
                "status_code": response.status_code,
            }

        response_json = response.json()
        print(f"Response JSON keys: {list(response_json.keys())}")
        if verbose:
            print(f"Full Response JSON: {response_json}")

        if "metadata" in response_json:
            if verbose:
                print("Request ID: ", response_json["metadata"]["request_id"])
                print(json.dumps(response_json["metadata"], indent=4))
                print(
                    response_json["results"]["channels"][0]["alternatives"][0]["transcript"]
                )

        return response_json
    except Exception as e:
        print(f"Error making request: {str(e)}")
        raise
