import logging
import os
import json
import base64
import threading
import time
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv
from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
    DeepgramClientOptions,
)
from common.batch_audio import process_audio
from common.audio_settings import detect_audio_settings
import logging
import sounddevice as sd
from pydub import AudioSegment
from pydub.utils import mediainfo
import tempfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize Flask and SocketIO
app = Flask(__name__)
socketio = SocketIO(
    app, cors_allowed_origins="*"  # Allow all origins since we're in development
)

API_KEY = os.getenv("DEEPGRAM_API_KEY")

# Load default configuration
with open("config/defaults.json", "r") as f:
    DEFAULT_CONFIG = json.load(f)

# Global variables
dg_connection = None
deepgram = None

# Store streaming responses for raw response display
streaming_responses = {"microphone": [], "file_streaming": []}

# Store current session info for raw response display
current_session_info = {
    "microphone": {"parameters": {}, "request_id": None},
    "file_streaming": {"parameters": {}, "request_id": None},
    "file_upload": {"parameters": {}, "request_id": None},
}

# Client configuration will be set dynamically per connection

streaming_thread = None
stop_streaming = False


# Flask routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/test")
def test():
    logger.info("Test endpoint called")
    return jsonify({"status": "server is working", "timestamp": time.time()})


@app.route("/upload_for_streaming", methods=["POST"])
def upload_for_streaming():
    """Upload file for streaming - separate from Socket.IO to avoid size limits"""
    try:
        logger.info("=== UPLOAD FOR STREAMING RECEIVED ===")
        data = request.get_json()

        if not data or "file" not in data:
            return jsonify({"error": "No file data provided"}), 400

        file_data = data["file"]
        config = data.get("config", {})

        logger.info(f"File name: {file_data.get('name', 'unknown')}")
        logger.info(f"Config: {config}")

        # Decode the base64 file data
        if file_data["data"].startswith("data:"):
            # Remove the data URL prefix
            header, encoded = file_data["data"].split(",", 1)
            file_content = base64.b64decode(encoded)
        else:
            file_content = base64.b64decode(file_data["data"])

        logger.info(f"Decoded file size: {len(file_content)} bytes")

        # Save the file temporarily
        filename = file_data["name"]
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"streaming_{int(time.time())}_{filename}")

        with open(file_path, "wb") as f:
            f.write(file_content)

        logger.info(f"File saved to: {file_path}")

        return jsonify(
            {
                "success": True,
                "file_path": file_path,
                "message": "File uploaded successfully for streaming",
            }
        )

    except Exception as e:
        logger.error(f"Error in upload_for_streaming: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        data = request.get_json()

        if not data or "file" not in data:
            logger.warning("Error: No file provided in request")
            return jsonify({"error": "No file provided"}), 400

        file_info = data["file"]
        if not file_info:
            logger.warning("Error: Empty file object")
            return jsonify({"error": "No file selected"}), 400

        logger.info(f"Received file: {file_info['name']}")
        logger.info(f"File data length: {len(file_info['data'])}")

        # Save file temporarily
        temp_dir = "temp"
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.join(temp_dir, file_info["name"])
        logger.info(f"Will save to: {file_path}")

        # Save the file
        try:
            file_data = base64.b64decode(file_info["data"].split(",")[1])
            logger.info(f"Decoded file size: {len(file_data)} bytes")
            with open(file_path, "wb") as f:
                f.write(file_data)
            logger.info(f"File saved successfully to {file_path}")
        except Exception as e:
            logger.warning(f"Error saving file: {str(e)}")
            return jsonify({"error": f"Error saving file: {str(e)}"}), 500

        try:
            # Use the config parameters from the client
            params = data.get("config", {})
            # Remove base_url as it's not needed for file processing
            params.pop("baseUrl", None)
            logger.info("Processing audio with HTTPS")
            logger.info(f"File path: {file_path}")
            logger.info(f"File size: {os.path.getsize(file_path)} bytes")
            logger.info(f"Parameters: {params}")

            result = process_audio(file_path, params, verbose=False)
            logger.info(f"Processing completed successfully")
            logger.info(
                f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}"
            )

            # Store parameters for raw response display
            current_session_info["file_upload"]["parameters"] = params.copy()
            logger.info(
                f"Stored parameters for file upload: {current_session_info['file_upload']['parameters']}"
            )

            # Extract request_id from result if available
            request_id = None
            logger.info(f"Result structure: {type(result)}")
            if isinstance(result, dict):
                logger.info(f"Result keys: {list(result.keys())}")
                if "metadata" in result:
                    logger.info(f"Metadata keys: {list(result['metadata'].keys())}")
                    request_id = result["metadata"].get("request_id")
                    logger.info(f"Extracted request_id from metadata: {request_id}")
                else:
                    logger.info("No metadata in result")
            current_session_info["file_upload"]["request_id"] = request_id
            logger.info(
                f"Stored request_id: {current_session_info['file_upload']['request_id']}"
            )

            # Emit raw response data to frontend for debugging
            if result and not result.get("error"):
                raw_response_data = {
                    "type": "file_upload",
                    "data": result,
                    "request_id": current_session_info["file_upload"]["request_id"],
                    "parameters": current_session_info["file_upload"]["parameters"],
                }
                logger.info(
                    f"About to emit raw response: request_id={raw_response_data['request_id']}, parameters_keys={list(raw_response_data['parameters'].keys()) if raw_response_data['parameters'] else 'None'}"
                )
                socketio.emit("raw_response", raw_response_data)
                logger.info(f"Emitted raw response data for file upload")

            # Clean up
            os.remove(file_path)
            logger.info(f"Temporary file removed: {file_path}")

            return jsonify(result)

        except Exception as e:
            logger.warning(f"Error processing audio: {str(e)}")
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Temporary file removed after error: {file_path}")
            return jsonify({"error": str(e)}), 500

    except Exception as e:
        logger.error(f"Unexpected error in upload endpoint: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


# Deepgram connection handling
def initialize_deepgram_connection(config_options=None, base_url="api.deepgram.com"):
    global dg_connection, deepgram, current_session_info

    if not config_options:
        logger.warning("No configuration options provided")
        return

    # Store parameters for raw response display (including baseUrl for debugging)
    current_session_info["microphone"]["parameters"] = config_options.copy()

    # Remove baseUrl from config_options before passing to LiveOptions
    live_options_config = config_options.copy()
    live_options_config.pop("baseUrl", None)  # Remove baseUrl if present

    # Construct WebSocket URL with base URL
    # Use WS for local/custom instances, WSS for official Deepgram API
    if base_url == "api.deepgram.com":
        websocket_url = f"wss://{base_url}/v1/listen"
    else:
        # Custom instances (like AWS) likely use WS instead of WSS
        websocket_url = f"ws://{base_url}/v1/listen"
    logger.info(f"Using Deepgram WebSocket URL: {websocket_url}")

    # Create new client config with dynamic base URL
    client_config = DeepgramClientOptions(url=websocket_url)
    deepgram = DeepgramClient(API_KEY, client_config)

    dg_connection = deepgram.listen.websocket.v("1")

    def on_open(self, open, **kwargs):
        logger.info(f"\n\n{open}\n\n")

    def on_message(self, result, **kwargs):
        # Store the full result for raw response display
        try:
            if hasattr(result, "to_dict"):
                result_dict = result.to_dict()
            elif hasattr(result, "__dict__"):
                result_dict = result.__dict__
            else:
                result_dict = str(result)
            streaming_responses["microphone"].append(result_dict)
        except Exception as e:
            logger.error(f"Error storing streaming response: {e}")

        transcript = result.channel.alternatives[0].transcript
        if len(transcript) > 0:
            timing = {"start": result.start, "end": result.start + result.duration}

            # Extract request_id from metadata if available
            request_id = None
            if hasattr(result, "metadata") and result.metadata:
                if hasattr(result.metadata, "request_id"):
                    request_id = result.metadata.request_id
                    # Store request_id for raw response display
                    if (
                        request_id
                        and not current_session_info["microphone"]["request_id"]
                    ):
                        current_session_info["microphone"]["request_id"] = request_id

            socketio.emit(
                "transcription_update",
                {
                    "transcription": transcript,
                    "is_final": result.is_final,
                    "speech_final": (
                        result.speech_final
                        if hasattr(result, "speech_final")
                        else False
                    ),
                    "timing": timing,
                    "request_id": request_id,
                },
            )

            # Emit request_id separately when available (for first message)
            if request_id:
                socketio.emit("request_id_update", {"request_id": request_id})

    def on_close(self, close, **kwargs):
        logger.info(f"\n\n{close}\n\n")

        # Emit accumulated streaming responses for debugging
        if streaming_responses["microphone"]:
            try:
                socketio.emit(
                    "raw_response",
                    {
                        "type": "microphone_recording",
                        "data": {
                            "close_event": str(close),
                            "streaming_responses": streaming_responses["microphone"],
                            "total_responses": len(streaming_responses["microphone"]),
                        },
                        "request_id": current_session_info["microphone"]["request_id"],
                        "parameters": current_session_info["microphone"]["parameters"],
                    },
                )
                logger.info(
                    f"Emitted {len(streaming_responses['microphone'])} accumulated streaming responses"
                )

                # Clear the accumulated responses
                streaming_responses["microphone"] = []
            except Exception as e:
                logger.error(f"Error processing raw response: {e}")
                socketio.emit(
                    "raw_response",
                    {
                        "type": "microphone_recording",
                        "data": {"error": str(e), "raw": str(close)},
                    },
                )

    def on_error(self, error, **kwargs):
        logger.info(f"\n\n{error}\n\n")

    dg_connection.on(LiveTranscriptionEvents.Open, on_open)
    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
    dg_connection.on(LiveTranscriptionEvents.Close, on_close)
    dg_connection.on(LiveTranscriptionEvents.Error, on_error)

    options = LiveOptions(**live_options_config)

    logger.info(f"Starting Deepgram connection with options: {options}")
    logger.info(f"WebSocket URL being used: {websocket_url}")
    logger.info(f"Live options config (cleaned): {live_options_config}")
    
    try:
        connection_result = dg_connection.start(options)
        logger.info(f"Connection start result: {connection_result}")
        if connection_result is False:
            logger.warning("Failed to start connection - connection returned False")
            return
    except Exception as e:
        logger.error(f"Exception during connection start: {e}")
        logger.error(f"Exception type: {type(e)}")
        return


# SocketIO event handlers
@socketio.on("audio_stream")
def handle_audio_stream(data):
    if dg_connection:
        dg_connection.send(data)


@socketio.on("toggle_transcription")
def handle_toggle_transcription(data):
    global dg_connection
    logger.info("toggle_transcription", data)
    action = data.get("action")
    if action == "start":
        logger.info("Starting Deepgram connection")
        # Clear accumulated responses for new session
        streaming_responses["microphone"] = []
        # Clear session info for new session
        current_session_info["microphone"] = {"parameters": {}, "request_id": None}
        config = data.get("config", {})
        base_url = config.get(
            "baseUrl", "api.deepgram.com"
        )  # Get baseUrl from config object
        logger.info(f"DEBUG: Received baseUrl from frontend: '{base_url}'")
        logger.info(f"DEBUG: Full data received: {data}")
        initialize_deepgram_connection(config, base_url)
    elif action == "stop" and dg_connection:
        logger.info("Closing Deepgram connection")
        dg_connection.finish()
        dg_connection = None


@socketio.on("connect")
def server_connect():
    logger.info("Client connected")


@socketio.on("disconnect")
def server_disconnect():
    logger.info("Client disconnected")


@socketio.on("test_event")
def handle_test_event(data):
    logger.info(f"Received test_event: {data}")
    return {"status": "received", "data": data}


@socketio.on("start_file_streaming")
def handle_start_file_streaming(data, callback=None):
    """Start streaming a file that was already uploaded"""
    global streaming_thread, stop_streaming

    try:
        file_path = data.get("file_path")
        config = data.get("config", {})
        base_url = config.get(
            "baseUrl", "api.deepgram.com"
        )  # Get baseUrl from config object

        if not file_path:
            error_msg = "No file path provided"
            logger.error(error_msg)
            if callback:
                callback({"error": error_msg})
            return

        # Clear accumulated responses for new session
        streaming_responses["file_streaming"] = []
        # Clear session info for new session
        current_session_info["file_streaming"] = {"parameters": {}, "request_id": None}

        # Store parameters for raw response display
        current_session_info["file_streaming"]["parameters"] = config.copy()

        logger.info(f"Starting file streaming for: {file_path}")
        logger.info(f"Config: {config}")
        logger.info(f"Using base URL: {base_url}")

        # Reset stop flag
        stop_streaming = False

        # Start streaming in a separate thread
        streaming_thread = threading.Thread(
            target=stream_audio_file_from_path, args=(file_path, config)
        )
        streaming_thread.daemon = True
        streaming_thread.start()

        logger.info("File streaming thread started")

        # Get audio duration for progress tracking
        try:
            audio_info = AudioSegment.from_file(file_path)
            duration_seconds = len(audio_info) / 1000.0
        except Exception as e:
            logger.warning(f"Could not get audio duration: {e}")
            duration_seconds = 0

        # Emit stream_started event with duration
        socketio.emit(
            "stream_started",
            {
                "message": "File streaming started",
                "file_path": file_path,
                "duration": duration_seconds,
            },
        )

        if callback:
            callback({"success": True, "message": "Streaming started"})

    except Exception as e:
        error_msg = f"Error starting file streaming: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())

        socketio.emit("stream_error", {"error": error_msg})

        if callback:
            callback({"error": error_msg})


@socketio.on("restart_deepgram")
def restart_deepgram():
    logger.info("Restarting Deepgram connection")
    initialize_deepgram_connection()


@socketio.on("detect_audio_settings")
def handle_detect_audio_settings():
    logger.info("Detecting audio settings")
    settings = detect_audio_settings(socketio)
    return settings


@socketio.on("upload_file")
def handle_file_upload(data, callback=None):
    if "file" not in data:
        logger.warning("Error: No file provided in data")
        error_response = {"error": "No file provided"}
        if callback:
            callback(error_response)
        return

    file = data["file"]
    if not file:
        logger.warning("Error: Empty file object")
        error_response = {"error": "No file selected"}
        if callback:
            callback(error_response)
        return

    logger.info(f"Received file: {file['name']}")
    logger.info(f"File data length: {len(file['data'])}")

    # Save file temporarily
    temp_dir = "temp"
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, file["name"])
    logger.info(f"Will save to: {file_path}")

    # Save the file
    try:
        file_data = base64.b64decode(file["data"].split(",")[1])
        logger.info(f"Decoded file size: {len(file_data)} bytes")
        with open(file_path, "wb") as f:
            f.write(file_data)
        logger.info(f"File saved successfully to {file_path}")
    except Exception as e:
        logger.warning(f"Error saving file: {str(e)}")
        error_response = {"error": f"Error saving file: {str(e)}"}
        if callback:
            callback(error_response)
        return

    try:
        # Use the config parameters from the client
        params = data.get("config", {})
        base_url = data.get("baseUrl", "api.deepgram.com")

        # Construct the API URL for batch processing
        deepgram_api_url = f"https://{base_url}/v1/listen"

        logger.info("Processing audio with HTTPS")
        logger.info(f"File path: {file_path}")
        logger.info(f"File size: {os.path.getsize(file_path)} bytes")
        logger.info(f"Parameters: {params}")
        logger.info(f"Using Deepgram API URL: {deepgram_api_url}")

        # Store parameters for raw response display
        current_session_info["file_upload"]["parameters"] = params.copy()
        logger.info(
            f"Stored parameters for file upload: {current_session_info['file_upload']['parameters']}"
        )

        result = process_audio(
            file_path, params, deepgram_api_url=deepgram_api_url, verbose=False
        )
        logger.info(f"Processing completed successfully")
        logger.info(
            f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}"
        )

        # Extract request_id from result if available
        request_id = None
        logger.info(f"Result structure: {type(result)}")
        if isinstance(result, dict):
            logger.info(f"Result keys: {list(result.keys())}")
            if "metadata" in result:
                logger.info(f"Metadata keys: {list(result['metadata'].keys())}")
                request_id = result["metadata"].get("request_id")
                logger.info(f"Extracted request_id from metadata: {request_id}")
            else:
                logger.info("No metadata in result")
        current_session_info["file_upload"]["request_id"] = request_id
        logger.info(
            f"Stored request_id: {current_session_info['file_upload']['request_id']}"
        )

        # Emit raw response data to frontend for debugging
        if result and not result.get("error"):
            raw_response_data = {
                "type": "file_upload",
                "data": result,
                "request_id": current_session_info["file_upload"]["request_id"],
                "parameters": current_session_info["file_upload"]["parameters"],
            }
            logger.info(
                f"About to emit raw response: request_id={raw_response_data['request_id']}, parameters_keys={list(raw_response_data['parameters'].keys()) if raw_response_data['parameters'] else 'None'}"
            )
            socketio.emit("raw_response", raw_response_data)
            logger.info(f"Emitted raw response data for file upload")

        # Clean up
        os.remove(file_path)
        logger.info(f"Temporary file removed: {file_path}")

        # Call the callback with the result
        if callback:
            callback(result)

    except Exception as e:
        logger.warning(f"Error processing audio: {str(e)}")
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Temporary file removed after error: {file_path}")
        error_response = {"error": str(e)}
        if callback:
            callback(error_response)


@socketio.on("stream_file")
def handle_stream_file(data, callback=None):
    """Handle streaming of prerecorded files"""
    logger.info("=== STREAM_FILE EVENT RECEIVED ===")
    logger.info(f"Data type: {type(data)}")
    logger.info(
        f"Data keys: {list(data.keys()) if data and isinstance(data, dict) else 'Not a dict or None'}"
    )
    logger.info(f"Callback provided: {callback is not None}")

    if data:
        logger.info(f"Data length: {len(str(data))}")
        if isinstance(data, dict):
            for key, value in data.items():
                if key == "file" and isinstance(value, dict):
                    logger.info(
                        f"File data - name: {value.get('name', 'Unknown')}, data length: {len(value.get('data', ''))}"
                    )
                elif key == "config":
                    logger.info(f"Config: {value}")
                else:
                    logger.info(f"{key}: {type(value)}")

    if "file" not in data:
        error_msg = "Error: No file provided in stream_file data"
        logger.warning(error_msg)
        socketio.emit("stream_error", {"error": error_msg})
        if callback:
            callback({"error": error_msg})
        return

    file = data["file"]
    if not file:
        error_msg = "Error: Empty file object in stream_file"
        logger.warning(error_msg)
        socketio.emit("stream_error", {"error": error_msg})
        if callback:
            callback({"error": error_msg})
        return

    logger.info(
        f"Starting file stream: {file['name']}, data length: {len(file.get('data', ''))}"
    )
    logger.info(f"Config: {data.get('config', {})}")

    # Save file temporarily
    temp_dir = "temp"
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, file["name"])

    try:
        # Save the file
        logger.info(f"Decoding file data...")
        file_data = base64.b64decode(file["data"].split(",")[1])
        logger.info(f"Decoded file size: {len(file_data)} bytes")

        with open(file_path, "wb") as f:
            f.write(file_data)
        logger.info(f"File saved for streaming: {file_path}")

        # Get configuration
        config = data.get("config", {})
        logger.info(f"Using config for streaming: {config}")

        # Reset stop flag and start streaming in a separate thread
        global streaming_thread, stop_streaming
        stop_streaming = False
        logger.info("Starting streaming thread...")

        streaming_thread = threading.Thread(
            target=stream_audio_file, args=(file_path, config)
        )
        streaming_thread.daemon = True
        streaming_thread.start()

        logger.info("Streaming thread started successfully")

        # Emit success event
        socketio.emit(
            "stream_started",
            {"message": "File streaming started", "filename": file["name"]},
        )

        if callback:
            callback({"success": True, "message": "Streaming started"})

    except Exception as e:
        error_msg = f"Error setting up file stream: {str(e)}"
        logger.error(error_msg)
        socketio.emit("stream_error", {"error": error_msg})
        if os.path.exists(file_path):
            os.remove(file_path)


def stream_audio_file_from_path(file_path, config, base_url="api.deepgram.com"):
    """Stream audio file from a file path"""
    global stop_streaming

    try:
        # Get API key
        deepgram_api_key = API_KEY
        if not deepgram_api_key:
            raise ValueError("Deepgram API key not found")

        # Create a new Deepgram client for this streaming session
        # Use WS for local/custom instances, WSS for official Deepgram API
        if base_url == "api.deepgram.com":
            websocket_url = f"wss://{base_url}/v1/listen"
        else:
            # Custom instances (like AWS) likely use WS instead of WSS
            websocket_url = f"ws://{base_url}/v1/listen"
        logger.info(f"Using Deepgram WebSocket URL for file streaming: {websocket_url}")

        client_options = DeepgramClientOptions(
            verbose=logging.INFO, options={"keepalive": "true"}, url=websocket_url
        )

        deepgram = DeepgramClient(deepgram_api_key, client_options)
        logger.info("Created new Deepgram client for file streaming")

        # Get audio info
        is_mulaw = file_path.lower().endswith(".mulaw")

        # Check if encoding is specified as mulaw in config
        if config.get("encoding") == "mulaw":
            is_mulaw = True

        if is_mulaw:
            # Use format hints for mulaw files
            sample_rate = int(config.get("sample_rate", 8000))
            logger.info(f"Processing mulaw file with sample rate: {sample_rate}")
            audio = AudioSegment.from_file(
                file_path,
                format="raw",
                sample_width=1,  # 8-bit
                channels=1,  # mono
                frame_rate=sample_rate,
            )
            # Create minimal info dict since mediainfo will fail
            info = {
                "sample_rate": str(sample_rate),
                "channels": "1",
                "codec_name": "pcm_mulaw",
                "codec_type": "audio",
            }
        else:
            # Standard processing for other formats
            audio = AudioSegment.from_file(file_path)
            info = mediainfo(file_path)

        # Calculate streaming parameters
        chunk_duration = 0.02  # 20ms chunks
        try:
            bit_rate = int(info["bit_rate"])
        except (ValueError, KeyError):
            bit_rate = 64000 * audio.channels

        byte_rate = bit_rate / 8
        chunk_size = int(byte_rate * chunk_duration)
        # Ensure chunk size is aligned to frame boundaries
        bytes_per_sample = audio.sample_width * audio.channels
        chunk_size = (chunk_size // bytes_per_sample) * bytes_per_sample

        logger.info(f"Starting audio stream: {file_path}, chunk_size: {chunk_size}")

        # Create WebSocket connection
        dg_connection = deepgram.listen.websocket.v("1")

        # Set up event handlers
        def on_message(self, result, **kwargs):
            # Store the full result for raw response display - ALWAYS store regardless of structure
            try:
                if hasattr(result, "to_dict"):
                    result_dict = result.to_dict()
                elif hasattr(result, "__dict__"):
                    result_dict = result.__dict__
                else:
                    result_dict = str(result)
                streaming_responses["file_streaming"].append(result_dict)
                logger.debug(f"Stored file streaming response: {type(result).__name__}")
                
                # Emit real-time streaming response to UI
                socketio.emit("streaming_response", {
                    "type": "file_streaming",
                    "response": result_dict,
                    "timestamp": time.time(),
                    "response_count": len(streaming_responses["file_streaming"])
                })
                
            except Exception as e:
                logger.error(f"Error storing file streaming response: {e}")
                # Fallback: store as string representation
                try:
                    fallback_response = str(result)
                    streaming_responses["file_streaming"].append(fallback_response)
                    # Emit fallback response to UI
                    socketio.emit("streaming_response", {
                        "type": "file_streaming",
                        "response": fallback_response,
                        "timestamp": time.time(),
                        "response_count": len(streaming_responses["file_streaming"]),
                        "error": str(e)
                    })
                except:
                    error_response = f"Error storing result: {str(e)}"
                    streaming_responses["file_streaming"].append(error_response)
                    socketio.emit("streaming_response", {
                        "type": "file_streaming", 
                        "response": error_response,
                        "timestamp": time.time(),
                        "response_count": len(streaming_responses["file_streaming"]),
                        "error": "Double error in storage"
                    })

            # Safely extract transcript with fallback handling
            transcript = ""
            try:
                if hasattr(result, 'channel') and result.channel:
                    if hasattr(result.channel, 'alternatives') and result.channel.alternatives:
                        if len(result.channel.alternatives) > 0:
                            transcript = result.channel.alternatives[0].transcript or ""
            except Exception as e:
                logger.error(f"Error extracting transcript from file streaming result: {e}")
                # Continue processing even if transcript extraction fails
                
            if not transcript:  # Skip empty transcripts
                return

            # Extract request_id from metadata if available
            request_id = None
            if hasattr(result, "metadata") and result.metadata:
                if hasattr(result.metadata, "request_id"):
                    request_id = result.metadata.request_id
                    # Store request_id for raw response display
                    if (
                        request_id
                        and not current_session_info["file_streaming"]["request_id"]
                    ):
                        current_session_info["file_streaming"][
                            "request_id"
                        ] = request_id

            if result.is_final:
                # Emit final transcript to client
                socketio.emit(
                    "transcript",
                    {
                        "transcript": transcript,
                        "is_final": True,
                        "speech_final": (
                            result.speech_final
                            if hasattr(result, "speech_final")
                            else False
                        ),
                        "channel": (
                            result.channel_index[0] if result.channel_index else 0
                        ),
                        "request_id": request_id,
                    },
                )
            else:
                # Emit interim transcript to client
                socketio.emit(
                    "transcript",
                    {
                        "transcript": transcript,
                        "is_final": False,
                        "speech_final": (
                            result.speech_final
                            if hasattr(result, "speech_final")
                            else False
                        ),
                        "channel": (
                            result.channel_index[0] if result.channel_index else 0
                        ),
                        "request_id": request_id,
                    },
                )

            # Emit request_id separately when available (for first message)
            if request_id:
                socketio.emit("request_id_update", {"request_id": request_id})

        def on_error(self, error, **kwargs):
            logger.error(f"Deepgram error: {error}")
            socketio.emit("stream_error", {"error": str(error)})

        def on_close(self, close, **kwargs):
            logger.info(f"Deepgram connection closed: {close}")

            # Emit accumulated streaming responses for debugging
            if streaming_responses["file_streaming"]:
                try:
                    socketio.emit(
                        "raw_response",
                        {
                            "type": "file_streaming",
                            "data": {
                                "close_event": str(close),
                                "streaming_responses": streaming_responses[
                                    "file_streaming"
                                ],
                                "total_responses": len(
                                    streaming_responses["file_streaming"]
                                ),
                            },
                            "request_id": current_session_info["file_streaming"][
                                "request_id"
                            ],
                            "parameters": current_session_info["file_streaming"][
                                "parameters"
                            ],
                        },
                    )
                    logger.info(
                        f"[FILE STREAMING] Emitted {len(streaming_responses['file_streaming'])} accumulated streaming responses"
                    )

                    # Clear the accumulated responses
                    streaming_responses["file_streaming"] = []
                except Exception as e:
                    logger.error(f"[FILE STREAMING] Error processing raw response: {e}")
                    socketio.emit(
                        "raw_response",
                        {
                            "type": "file_streaming",
                            "data": {"error": str(e), "raw": str(close)},
                        },
                    )

        def on_open(self, open, **kwargs):
            logger.info(f"Deepgram connection opened: {open}")

        # Register event handlers
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        dg_connection.on(LiveTranscriptionEvents.Open, on_open)

        # Prepare LiveOptions with config (filter out non-LiveOptions parameters)
        live_config = {k: v for k, v in config.items() if k != "baseUrl"}
        live_options = LiveOptions(**live_config)
        logger.info(f"Starting connection with options: {live_options}")

        # Start the connection
        if not dg_connection.start(live_options):
            raise Exception("Failed to start Deepgram connection")

        logger.info("Deepgram connection started, beginning audio streaming...")

        # Stream the audio
        with open(file_path, "rb") as f:
            while True:
                # Check if streaming should be stopped
                if stop_streaming:
                    logger.info("Streaming stopped by user")
                    break

                data = f.read(chunk_size)
                if not data:
                    break

                dg_connection.send(data)
                time.sleep(chunk_duration)

        # Finish the connection
        logger.info("Finishing Deepgram connection...")
        dg_connection.finish()

        logger.info("Finished streaming audio file")

        # Save streaming responses to file
        try:
            if streaming_responses["file_streaming"]:
                timestamp = int(time.time())
                responses_filename = f"streaming_responses_{timestamp}.json"
                responses_path = os.path.join("outputs", responses_filename)
                
                # Create outputs directory if it doesn't exist
                os.makedirs("outputs", exist_ok=True)
                
                # Prepare data to save
                save_data = {
                    "session_info": {
                        "timestamp": timestamp,
                        "parameters": current_session_info["file_streaming"]["parameters"],
                        "request_id": current_session_info["file_streaming"]["request_id"],
                        "total_responses": len(streaming_responses["file_streaming"])
                    },
                    "streaming_responses": streaming_responses["file_streaming"]
                }
                
                with open(responses_path, 'w', encoding='utf-8') as f:
                    json.dump(save_data, f, indent=2, default=str)
                
                logger.info(f"Saved {len(streaming_responses['file_streaming'])} streaming responses to {responses_path}")
                
                # Emit file saved notification to UI
                socketio.emit("responses_saved", {
                    "message": f"Streaming responses saved to {responses_filename}",
                    "filename": responses_filename,
                    "path": responses_path,
                    "total_responses": len(streaming_responses["file_streaming"])
                })
            else:
                logger.info("No streaming responses to save")
                
        except Exception as e:
            logger.error(f"Error saving streaming responses: {e}")
            socketio.emit("responses_save_error", {
                "message": f"Failed to save streaming responses: {str(e)}"
            })

        # Emit stream finished event
        socketio.emit("stream_finished", {"message": "File streaming completed"})

        # Clean up
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Temporary file removed: {file_path}")

    except Exception as e:
        error_msg = f"Error streaming audio file: {str(e)}"
        logger.error(error_msg)
        logger.exception("Full exception details:")

        # Emit error event
        socketio.emit("stream_error", {"error": error_msg})

        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temporary file after error: {file_path}")


@socketio.on("stop_stream_file")
def handle_stop_stream_file():
    """Handle stopping of file streaming"""
    global stop_streaming, streaming_thread

    logger.info("Received stop_stream_file request")

    # Set the stop flag
    stop_streaming = True

    # Wait for the streaming thread to finish (with timeout)
    if streaming_thread and streaming_thread.is_alive():
        streaming_thread.join(timeout=2.0)
        if streaming_thread.is_alive():
            logger.warning("Streaming thread did not stop within timeout")

    logger.info("File streaming stopped")


if __name__ == "__main__":
    logging.info("Starting combined Flask-SocketIO server")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True, port=8001)
