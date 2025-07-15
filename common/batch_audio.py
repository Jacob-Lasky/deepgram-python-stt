import requests
import json
import pandas as pd
import os
import logging
import mimetypes

logger = logging.getLogger("BatchAudio")

DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]
DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"


def process_audio(
    audio_path: str,
    params: dict,
    status_csv: str | None = None,
    deepgram_api_url: str = DEEPGRAM_API_URL,
    verbose: bool = False,
):
    print(f"Processing audio file: {audio_path}")
    print(f"Parameters: {params}")

    if status_csv is None:
        logger.warning("Status CSV not provided, will not save results")
    else:
        if not os.path.exists(status_csv):
            logger.warning(f"Status CSV not found, creating new file: {status_csv}")
            pd.DataFrame(
                columns=[
                    "status",
                    "request_url",
                    "file_path",
                    "request_id",
                    "internal_tools_url",
                ]
            ).to_csv(status_csv, index=False)
        else:
            logger.info(f"Status CSV found: {status_csv}")

        status_df = pd.read_csv(status_csv)

    if audio_path.startswith("http"):
        url_path = audio_path.split("?")[0]  # Remove query parameters
        json_data = {
            "url": audio_path,
        }
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
        # Detect content type from file extension
        content_type, _ = mimetypes.guess_type(audio_path)
        if not content_type or not content_type.startswith("audio/"):
            # Default to audio/wav if we can't detect or it's not audio
            content_type = "audio/wav"
        print(f"Detected content type: {content_type}")

    headers = {
        "accept": "application/json",
        "content-type": content_type,
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
    }
    print(f"Request headers: {headers}")
    print(f"Request URL: {deepgram_api_url}")
    print(f"Request params: {params}")

    # POST request
    try:
        print(f"Making HTTPS request to: {deepgram_api_url}")
        response = requests.post(
            deepgram_api_url,
            params=params,
            data=audio_data,
            json=json_data,
            headers=headers,
            timeout=600,
            verify=True,  # Ensure SSL verification
        )
        print(f"Response status code: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")

        if response.status_code != 200:
            print(f"Error response: {response.text}")
            print(f"Error response status: {response.status_code}")
            # Try to parse error as JSON if possible
            try:
                error_json = response.json()
                print(f"Error JSON: {error_json}")
            except:
                pass
            # Still try to return the response for debugging
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
                    response_json["results"]["channels"][0]["alternatives"][0][
                        "transcript"
                    ]
                )
            if status_csv is not None:
                status_df = pd.concat(
                    [
                        status_df,
                        pd.DataFrame(
                            [
                                {
                                    "status": "success",
                                    "request_url": response.request.url,
                                    "file_path": audio_path,
                                    "request_id": response_json["metadata"][
                                        "request_id"
                                    ],
                                    "internal_tools_url": f"https://internal.tools.deepgram.com/rraid_request?request_id={response_json['metadata']['request_id']}",
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
        else:
            if verbose:
                print(response_json)
            if status_csv is not None:
                status_df = pd.concat(
                    [
                        status_df,
                        pd.DataFrame(
                            [
                                {
                                    "status": "failure",
                                    "request_url": response.request.url,
                                    "file_path": audio_path,
                                    "request_id": response_json["request_id"],
                                    "internal_tools_url": f"https://internal.tools.deepgram.com/rraid_request?request_id={response_json['request_id']}",
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )

        if status_csv is not None:
            status_df.to_csv(status_csv, index=False)

        return response_json
    except Exception as e:
        print(f"Error making request: {str(e)}")
        raise
