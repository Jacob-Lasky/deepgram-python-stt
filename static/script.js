let isRecording = false;
let isStreamingFile = false;
let socket;
let microphone;
let audioContext;
let processor;
// Track which parameters have been changed during this session
let changedParams = new Set();
// Track if we're in a post-import state
let isImported = false;

// Function to stop file streaming
function stopFileStreaming() {
    if (isStreamingFile) {
        console.log('Stopping file streaming...');
        socket.emit('stop_stream_file');
        isStreamingFile = false;
        document.body.classList.remove('recording');
        
        // Reset the record button
        const recordButton = document.getElementById('record');
        recordButton.checked = false;
        
        // Reset interim_results to the checkbox state
        const config = getConfig();
        updateRequestUrl(config);
        
        console.log('File streaming stopped');
    }
}

let DEFAULT_CONFIG = {
    "base_url": "api.deepgram.com",
    "model": "nova-3",
    "language": "en",
    "utterance_end_ms": "1000",
    "endpointing": "10",
    "smart_format": false,
    "interim_results": true,
    "no_delay": false,
    "dictation": false,
    "numerals": false,
    "profanity_filter": false,
    "redact": false,
    "punctuate": false,
    "encoding": "",
    "channels": 1,
    "sample_rate": 16000,
    "vad_events": true,
    "extra": {}
};

const socket_port = 8001;
socket = io(
  "http://" + window.location.hostname + ":" + socket_port.toString()
);

// Add Socket.IO connection event logging
socket.on('connect', () => {
    console.log('Socket.IO connected successfully');
    console.log('Socket ID:', socket.id);
});

socket.on('disconnect', (reason) => {
    console.log('Socket.IO disconnected:', reason);
});

socket.on('connect_error', (error) => {
    console.error('Socket.IO connection error:', error);
});

socket.on('reconnect', (attemptNumber) => {
    console.log('Socket.IO reconnected after', attemptNumber, 'attempts');
});

socket.on('reconnect_error', (error) => {
    console.error('Socket.IO reconnection error:', error);
});

// Listen for streaming events
socket.on('stream_started', (data) => {
    console.log('Stream started:', data);
});

socket.on('stream_error', (error) => {
    console.error('Stream error:', error);
    if (isStreamingFile) {
        stopFileStreaming();
    }
});

socket.on('stream_finished', (data) => {
    console.log('Stream finished:', data);
    if (isStreamingFile) {
        stopFileStreaming();
    }
});

// Listen for transcript events from file streaming
socket.on('transcript', (data) => {
    console.log('Received transcript:', data);
    
    const interimCaptions = document.getElementById("captions");
    const finalCaptions = document.getElementById("finalCaptions");
    
    // Create interim message div with formatting similar to microphone streaming
    const interimDiv = document.createElement("div");
    const type = data.is_final ? "[Is Final]" : "[Interim Result]";
    interimDiv.textContent = `${type} ${data.transcript}`;
    interimDiv.className = data.is_final ? "final" : "interim";
    
    // Add to interim container
    interimCaptions.appendChild(interimDiv);
    interimDiv.scrollIntoView({ behavior: "smooth" });
    
    // Update final container
    if (data.is_final) {
        // Remove any existing interim span
        const existingInterim = finalCaptions.querySelector('.interim-final');
        if (existingInterim) {
            existingInterim.remove();
        }
        // For final results, append as a new span
        const finalDiv = document.createElement("span");
        finalDiv.textContent = data.transcript + " ";
        finalDiv.className = "final";
        finalCaptions.appendChild(finalDiv);
        finalDiv.scrollIntoView({ behavior: "smooth" });
    } else {
        // For interim results, update or create the interim span
        let interimSpan = finalCaptions.querySelector('.interim-final');
        if (!interimSpan) {
            interimSpan = document.createElement("span");
            interimSpan.className = "interim-final";
            finalCaptions.appendChild(interimSpan);
        }
        interimSpan.textContent = data.transcript;
        interimSpan.scrollIntoView({ behavior: "smooth" });
    }
});

// Fetch default configuration
fetch('../config/defaults.json')
  .then(response => response.json())
  .then(config => {
    DEFAULT_CONFIG = config;
    // Initialize URL with current config
    updateRequestUrl(getConfig());
  })
  .catch(error => {
    console.error('Error loading default configuration:', error);
    // Initialize URL with current config
    updateRequestUrl(getConfig());
  });

function setDefaultValues() {
    if (!DEFAULT_CONFIG) return;
    
    // Set text input defaults
    ['baseUrl', 'model', 'language', 'utterance_end_ms', 'endpointing', 'encoding'].forEach(id => {
        const element = document.getElementById(id);
        if (element && DEFAULT_CONFIG[id]) {
            element.value = DEFAULT_CONFIG[id];
        }
    });

    // Set numeric input defaults
    ['channels', 'sample_rate'].forEach(id => {
        const element = document.getElementById(id);
        if (element && DEFAULT_CONFIG[id] !== undefined) {
            element.value = DEFAULT_CONFIG[id];
        }
    });

    // Set checkbox defaults
    ['smart_format', 'interim_results', 'no_delay', 'dictation', 
     'numerals', 'profanity_filter', 'redact', 'punctuate', 'vad_events'].forEach(id => {
        const element = document.getElementById(id);
        if (element && DEFAULT_CONFIG[id] !== undefined) {
            element.checked = DEFAULT_CONFIG[id];
        }
    });

    // Set extra params default
    document.getElementById('extraParams').value = JSON.stringify(DEFAULT_CONFIG.extra || {}, null, 2);
}

function resetConfig() {
    if (!DEFAULT_CONFIG) return;
    // Clear changed parameters tracking and import state
    changedParams.clear();
    isImported = false;
    setDefaultValues();
    updateRequestUrl(getConfig());
}

function importConfig(input) {
    if (!DEFAULT_CONFIG) return;
    
    // Reset all options to defaults first
    setDefaultValues();
    
    let config;
    
    try {
        config = JSON.parse(input);
    } catch (e) {
        config = parseUrlParams(input);
    }
    
    if (!config) {
        throw new Error('Invalid configuration format. Please provide a valid JSON object or URL.');
    }

    // Set import state
    isImported = true;

    // Clear all form fields first
    ['baseUrl', 'model', 'language', 'utterance_end_ms', 'endpointing'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.value = '';
        }
    });

    ['smart_format', 'interim_results', 'no_delay', 'dictation', 
     'numerals', 'profanity_filter', 'redact'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.checked = false;
        }
    });

    // Only set values that are explicitly in the config
    Object.entries(config).forEach(([key, value]) => {
        const element = document.getElementById(key);
        if (element) {
            if (element.type === 'checkbox') {
                element.checked = value === 'true' || value === true;
            } else {
                element.value = value;
            }
            changedParams.add(key);
        } else {
            // If the key doesn't correspond to a form element, it's an extra param
            const extraParams = document.getElementById('extraParams');
            const currentExtra = JSON.parse(extraParams.value || '{}');
            currentExtra[key] = value;
            extraParams.value = JSON.stringify(currentExtra, null, 2);
            changedParams.add('extraParams');
        }
    });

    // Set baseUrl if not in config
    if (!config.baseUrl) {
        document.getElementById('baseUrl').value = 'api.deepgram.com';
    }

    // Update the URL display
    updateRequestUrl();
}

socket.on("transcription_update", (data) => {
  const interimCaptions = document.getElementById("captions");
  const finalCaptions = document.getElementById("finalCaptions");
  
  let timeString = "";
  if (data.timing) {
    const start = data.timing.start.toFixed(2);
    const end = data.timing.end.toFixed(2);
    timeString = `${start}-${end}`;
  }
  
  // Create interim message div
  const interimDiv = document.createElement("div");
  const type = data.is_final ? "[Is Final]" : data.utterance_end ? "[Utterance End]" : "[Interim Result]";
  interimDiv.textContent = data.utterance_end ? 
    `${type} ${data.transcription}` :
    `${timeString}   ${type} ${data.transcription}`;
  interimDiv.className = data.is_final ? "final" : "interim";
  
  // Add to interim container
  interimCaptions.appendChild(interimDiv);
  interimDiv.scrollIntoView({ behavior: "smooth" });
  
  // Update final container
  if (data.is_final) {
    // Remove any existing interim span
    const existingInterim = finalCaptions.querySelector('.interim-final');
    if (existingInterim) {
      existingInterim.remove();
    }
    // For final results, append as a new span
    const finalDiv = document.createElement("span");
    finalDiv.textContent = data.transcription + " ";
    finalDiv.className = "final";
    finalCaptions.appendChild(finalDiv);
    finalDiv.scrollIntoView({ behavior: "smooth" });
  } else if (!data.utterance_end) {
    // For interim results, update or create the interim span
    let interimSpan = finalCaptions.querySelector('.interim-final');
    if (!interimSpan) {
      interimSpan = document.createElement("span");
      interimSpan.className = "interim-final";
      finalCaptions.appendChild(interimSpan);
    }
    interimSpan.textContent = data.transcription + " ";
    interimSpan.scrollIntoView({ behavior: "smooth" });
  }
});

async function getMicrophone() {
  try {
    // Get the selected device ID from the dropdown if it exists
    const microphoneSelect = document.getElementById('microphone-select');
    const constraints = { audio: true };
    
    // If a specific microphone is selected, use that device ID
    if (microphoneSelect && microphoneSelect.value) {
      constraints.audio = { deviceId: { exact: microphoneSelect.value } };
    }
    
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    return new MediaRecorder(stream, { mimeType: "audio/webm" });
  } catch (error) {
    console.error("Error accessing microphone:", error);
    throw error;
  }
}

async function openMicrophone(microphone, socket) {
  return new Promise((resolve) => {
    microphone.onstart = () => {
      console.log("Client: Microphone opened");
      document.body.classList.add("recording");
      resolve();
    };
    microphone.ondataavailable = async (event) => {
      console.log("client: microphone data received");
      if (event.data.size > 0) {
        socket.emit("audio_stream", event.data);
      }
    };
    microphone.start(1000);
  });
}

async function startRecording() {
  isRecording = true;
  microphone = await getMicrophone();
  console.log("Client: Waiting to open microphone");
  
  // Send configuration before starting microphone
  const config = getConfig();
  // Force interim_results to true for live recording
  config.interim_results = true;
  
  // Update the UI to show interim_results is true
  document.getElementById('interim_results').checked = true;
  
  // Update the URL display to show interim_results=true
  updateRequestUrl(config);
  
  socket.emit("toggle_transcription", { action: "start", config: config });
  
  // Display the URL in the interim results container
  const interimCaptions = document.getElementById("captions");
  const urlDiv = document.createElement("div");
  urlDiv.className = "url-info";
  const url = document.getElementById('requestUrl').textContent
    .replace(/\s+/g, '') // Remove all whitespace including newlines
    .replace(/&amp;/g, '&'); // Fix any HTML-encoded ampersands
  urlDiv.textContent = `Using URL: ${url}`;
  interimCaptions.appendChild(urlDiv);
  urlDiv.scrollIntoView({ behavior: "smooth" });
  
  await openMicrophone(microphone, socket);
}

async function stopRecording() {
  if (isRecording === true) {
    microphone.stop();
    microphone.stream.getTracks().forEach((track) => track.stop()); // Stop all tracks
    socket.emit("toggle_transcription", { action: "stop" });
    microphone = null;
    isRecording = false;
    console.log("Client: Microphone closed");
    document.body.classList.remove("recording");
    
    // Reset interim_results to the checkbox state
    const config = getConfig();
    updateRequestUrl(config);
  }
}

function getConfig() {
    const config = {};
    
    const addIfSet = (id) => {
        const element = document.getElementById(id);
        const value = element.type === 'checkbox' ? element.checked : element.value;
        if (value !== '' && value !== false) {
            config[id] = value;
        }
    };

    addIfSet('baseUrl');
    addIfSet('language');
    addIfSet('model');
    addIfSet('utterance_end_ms');
    addIfSet('endpointing');
    addIfSet('smart_format');
    addIfSet('interim_results');
    addIfSet('no_delay');
    addIfSet('dictation');
    addIfSet('numerals');
    addIfSet('profanity_filter');
    addIfSet('redact');
    addIfSet('punctuate');
    addIfSet('encoding');
    addIfSet('channels');
    addIfSet('sample_rate');
    addIfSet('vad_events');

    // Add extra parameters
    const extraParams = document.getElementById('extraParams');
    if (extraParams && extraParams.value) {
        try {
            const extra = JSON.parse(extraParams.value);
            Object.entries(extra).forEach(([key, value]) => {
                if (value !== undefined && value !== '') {
                    config[key] = value;
                }
            });
        } catch (e) {
            console.error('Error parsing extra parameters:', e);
        }
    }

    return config;
}

function toggleConfig() {
    const header = document.querySelector('.config-header');
    const content = document.getElementById('configContent');
    header.classList.toggle('collapsed');
    content.classList.toggle('collapsed');
}

function updateRequestUrl() {
    const urlElement = document.getElementById('requestUrl');

    const baseUrl = document.getElementById('baseUrl').value;
    const params = new URLSearchParams();
    
    // Only add parameters that are explicitly set
    const language = document.getElementById('language').value;
    if (language) params.append('language', language);
    
    const model = document.getElementById('model').value;
    if (model) params.append('model', model);
    
    const utteranceEndMs = document.getElementById('utterance_end_ms').value;
    if (utteranceEndMs) params.append('utterance_end_ms', utteranceEndMs);
    
    const endpointing = document.getElementById('endpointing').value;
    if (endpointing) params.append('endpointing', endpointing);
    
    const encoding = document.getElementById('encoding').value;
    if (encoding) params.append('encoding', encoding);
    
    const channels = document.getElementById('channels').value;
    if (channels) params.append('channels', channels);
    
    const sampleRate = document.getElementById('sample_rate').value;
    if (sampleRate) params.append('sample_rate', sampleRate);
    
    const smartFormat = document.getElementById('smart_format').checked;
    if (smartFormat) params.append('smart_format', 'true');
    
    const interimResults = document.getElementById('interim_results').checked;
    if (interimResults) params.append('interim_results', 'true');
    
    const noDelay = document.getElementById('no_delay').checked;
    if (noDelay) params.append('no_delay', 'true');
    
    const dictation = document.getElementById('dictation').checked;
    if (dictation) params.append('dictation', 'true');
    
    const numerals = document.getElementById('numerals').checked;
    if (numerals) params.append('numerals', 'true');
    
    const profanityFilter = document.getElementById('profanity_filter').checked;
    if (profanityFilter) params.append('profanity_filter', 'true');
    
    const redact = document.getElementById('redact').checked;
    if (redact) params.append('redact', 'true');
    
    const punctuate = document.getElementById('punctuate').checked;
    if (punctuate) params.append('punctuate', 'true');
    
    const vadEvents = document.getElementById('vad_events').checked;
    if (vadEvents) params.append('vad_events', 'true');
    
    // Add extra parameters if any
    const extraParams = document.getElementById('extraParams');
    if (extraParams && extraParams.value) {
        try {
            const extra = JSON.parse(extraParams.value);
            Object.entries(extra).forEach(([key, value]) => {
                if (value !== undefined && value !== '') {
                    if (Array.isArray(value)) {
                        value.forEach(v => params.append(key, v));
                    } else {
                        params.append(key, value);
                    }
                }
            });
        } catch (e) {
            console.error('Invalid extra parameters JSON:', e);
        }
    }
    
    // Calculate maxLineLength for new parameters
    const containerWidth = urlElement.parentElement.getBoundingClientRect().width;
    const avgCharWidth = 8.5;
    const safetyMargin = 40;
    const maxLineLength = Math.floor((containerWidth - safetyMargin) / avgCharWidth);
    
    // Format URL with line breaks
    const baseUrlDisplay = isRecording ? `ws://${baseUrl}/v1/listen?` : `http://${baseUrl}/v1/listen?`;
    const pairs = params.toString().split('&');
    let currentLine = baseUrlDisplay;
    const outputLines = [];
    
    pairs.forEach((pair, index) => {
        const shouldBreakLine = currentLine !== baseUrlDisplay && 
            (currentLine.length + pair.length + 1 > maxLineLength);
        
        if (shouldBreakLine) {
            outputLines.push(currentLine + '&amp;');
            currentLine = pair;
        } else {
            currentLine += (currentLine === baseUrlDisplay ? '' : '&amp;') + pair;
        }
        
        if (index === pairs.length - 1) {
            outputLines.push(currentLine);
        }
    });
    
    urlElement.innerHTML = outputLines.join('\n');
    return outputLines.join('').replace(/&amp;/g, '&');
}

function toggleExtraParams() {
    const header = document.querySelector('.extra-params-header');
    const content = document.getElementById('extraParamsContent');
    header.classList.toggle('collapsed');
    content.classList.toggle('collapsed');
}

function parseUrlParams(url) {
    try {
        // Handle ws:// and wss:// protocols by temporarily replacing them
        let modifiedUrl = url;
        if (url.startsWith('ws://') || url.startsWith('wss://')) {
            modifiedUrl = url.replace(/^ws:\/\//, 'http://').replace(/^wss:\/\//, 'https://');
        }
        
        // If URL starts with a path, prepend the default base URL
        if (url.startsWith('/')) {
            modifiedUrl = 'http://api.deepgram.com' + url;
        }
        
        const urlObj = new URL(modifiedUrl);
        const params = {};

        // Extract the hostname as baseUrl, removing /v1/listen if present
        params.baseUrl = urlObj.hostname;
        
        // Handle duplicate parameters as arrays
        const paramMap = new Map();
        urlObj.searchParams.forEach((value, key) => {
            const cleanKey = key.trim();
            const cleanValue = value.trim();
            if (cleanKey && cleanValue) {
                if (paramMap.has(cleanKey)) {
                    const existingValue = paramMap.get(cleanKey);
                    paramMap.set(cleanKey, Array.isArray(existingValue) ? [...existingValue, cleanValue] : [existingValue, cleanValue]);
                } else {
                    paramMap.set(cleanKey, cleanValue);
                }
            }
        });
        
        // Convert Map to object
        paramMap.forEach((value, key) => {
            params[key] = value;
        });
        
        return params;
    } catch (e) {
        console.error('Invalid URL:', e);
        return null;
    }
}

function simplifyUrl() {
    // Clear import state and changed params
    isImported = false;
    changedParams.clear();
    // Update URL to show only non-default params
    updateRequestUrl(getConfig());
}

document.addEventListener("DOMContentLoaded", () => {
    const recordButton = document.getElementById("record");
    const configPanel = document.querySelector('.config-panel');
    const copyButton = document.getElementById('copyUrl');
    const resetButton = document.getElementById('resetButton');
    const simplifyButton = document.getElementById('simplifyButton');
    const clearButton = document.getElementById('clearButton');
    
    // Make URL editable
    const urlElement = document.getElementById('requestUrl');
    urlElement.contentEditable = true;
    urlElement.style.cursor = 'text';
    
    // Clear button functionality
    if (clearButton) {
        clearButton.addEventListener('click', () => {
            document.getElementById('captions').innerHTML = '';
            document.getElementById('finalCaptions').innerHTML = '';
        });
    }
    
    // Reset button functionality
    if (resetButton) {
        resetButton.addEventListener('click', resetConfig);
    }
    
    // Simplify button functionality
    if (simplifyButton) {
        simplifyButton.addEventListener('click', simplifyUrl);
    }
    
    // Copy URL functionality
    copyButton.addEventListener('click', () => {
        const url = document.getElementById('requestUrl').textContent
            .replace(/\s+/g, '') // Remove all whitespace including newlines
            .replace(/&amp;/g, '&'); // Fix any HTML-encoded ampersands
        navigator.clipboard.writeText(url).then(() => {
            copyButton.classList.add('copied');
            setTimeout(() => copyButton.classList.remove('copied'), 1000);
        });
    });

    // Add event listeners to all config inputs with change tracking
    const configInputs = document.querySelectorAll('#configForm input');
    configInputs.forEach(input => {
        input.addEventListener('change', () => {
            changedParams.add(input.id);
            updateRequestUrl(getConfig());
        });
        if (input.type === 'text') {
            input.addEventListener('input', () => {
                changedParams.add(input.id);
                updateRequestUrl(getConfig());
            });
        }
    });
    
    // Add event listener for extra params
    document.getElementById('extraParams').addEventListener('blur', () => {
        try {
            const extraParams = document.getElementById('extraParams');
            const rawJson = extraParams.value || '{}';
            // Parse the raw JSON string to handle duplicate keys
            const processedExtra = {}; ul
            const lines = rawJson.split('\n');
            lines.forEach(line => {
                const match = line.match(/"([^"]+)":\s*"([^"]+)"/);
                if (match) {
                    const [, key, value] = match;
                    if (processedExtra[key]) {
                        if (Array.isArray(processedExtra[key])) {
                            processedExtra[key].push(value);
                        } else {
                            processedExtra[key] = [processedExtra[key], value];
                        }
                    } else {
                        processedExtra[key] = value;
                    }
                }
            });
            // Update the textarea with the processed JSON
            extraParams.value = JSON.stringify(processedExtra, null, 2);
            // Mark extra params as changed if they're not empty
            if (Object.keys(processedExtra).length > 0) {
                changedParams.add('extraParams');
            } else {
                changedParams.delete('extraParams');
            }
            updateRequestUrl();
        } catch (e) {
            console.warn('Invalid JSON in extra params');
        }
    });

    // Add resize listener to update URL formatting when window size changes
    window.addEventListener('resize', () => {
        updateRequestUrl(getConfig());
    });

    // Initialize URL with current config instead of defaults
    updateRequestUrl(getConfig());

    // Function to populate the microphone dropdown with available devices
    async function populateMicrophoneList() {
      try {
        // First get permission to access media devices
        await navigator.mediaDevices.getUserMedia({ audio: true });
        
        // Then enumerate devices
        const devices = await navigator.mediaDevices.enumerateDevices();
        const microphoneSelect = document.getElementById('microphone-select');
        
        // Store current selection if any
        const currentSelection = microphoneSelect.value;
        
        // Clear all options except the default
        while (microphoneSelect.options.length > 1) {
          microphoneSelect.remove(1);
        }
        
        // Add all audio input devices
        const audioInputDevices = devices.filter(device => device.kind === 'audioinput');
        audioInputDevices.forEach(device => {
          const option = document.createElement('option');
          option.value = device.deviceId;
          option.text = device.label || `Microphone ${microphoneSelect.options.length}`;
          microphoneSelect.appendChild(option);
          
          // If this was the previously selected device, select it again
          if (device.deviceId === currentSelection) {
            microphoneSelect.value = device.deviceId;
          }
        });
        
        console.log(`Found ${audioInputDevices.length} audio input devices`);
      } catch (error) {
        console.error('Error accessing media devices:', error);
      }
    }

    // Populate the microphone list on page load
    populateMicrophoneList();
    
    // Add event listener for the refresh button
    const refreshButton = document.getElementById('refreshMicrophoneList');
    if (refreshButton) {
      refreshButton.addEventListener('click', populateMicrophoneList);
    }

    recordButton.addEventListener("change", async () => {
        if (recordButton.checked) {
            // Only start microphone recording if not already streaming a file
            if (!isStreamingFile) {
                try {
                    await startRecording();
                } catch (error) {
                    console.error("Failed to start recording:", error);
                    recordButton.checked = false;
                }
            }
        } else {
            // Stop both microphone recording and file streaming
            if (isRecording) {
                await stopRecording();
            }
            if (isStreamingFile) {
                stopFileStreaming();
            }
        }
    });

    // Initialize extra params as collapsed
    const extraParamsHeader = document.querySelector('.extra-params-header');
    extraParamsHeader.classList.add('collapsed');

    // Add import button handler
    document.getElementById('importButton').addEventListener('click', () => {
        const importInput = document.getElementById('importInput');
        const input = importInput.value.trim();
        if (!input) {
            alert('Please enter a configuration to import.');
            return;
        }
        
        try {
            importConfig(input);
            // Only clear input if import was successful
            importInput.value = '';
        } catch (e) {
            alert('Invalid configuration format. Please provide a valid JSON object or URL.');
        }
    });

    // Add keyboard shortcut (Enter key) for import input
    document.getElementById('importInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            document.getElementById('importButton').click();
        }
    });

    // Add event listener for URL editing
    document.getElementById('requestUrl').addEventListener('input', function(e) {
        // Store cursor position
        const selection = window.getSelection();
        const range = selection.getRangeAt(0);
        const cursorOffset = range.startOffset;
        
        const url = this.textContent.replace(/\s+/g, '').replace(/&amp;/g, '&');
        const config = parseUrlParams(url);
        if (config) {
            // Update form fields based on URL
            Object.entries(config).forEach(([key, value]) => {
                const element = document.getElementById(key);
                if (element) {
                    if (element.type === 'checkbox') {
                        element.checked = value === 'true' || value === true;
                    } else {
                        element.value = value;
                    }
                    changedParams.add(key);
                }
            });
            
            // Update extra parameters
            const extraParams = {};
            Object.entries(config).forEach(([key, value]) => {
                if (!document.getElementById(key)) {
                    extraParams[key] = value;
                }
            });
            document.getElementById('extraParams').value = JSON.stringify(extraParams, null, 2);
            
            // Update URL display with proper wrapping and escaping
            updateRequestUrl();
            
            // Restore cursor position
            try {
                const urlElement = document.getElementById('requestUrl');
                const newRange = document.createRange();
                newRange.setStart(urlElement.firstChild || urlElement, Math.min(cursorOffset, (urlElement.firstChild || urlElement).length));
                newRange.collapse(true);
                selection.removeAllRanges();
                selection.addRange(newRange);
            } catch (e) {
                console.warn('Could not restore cursor position:', e);
            }
        }
    });

    // File upload handling
    const uploadButton = document.getElementById('uploadButton');
    const audioFile = document.getElementById('audioFile');
    const dropZone = document.getElementById('dropZone');
    
    // Debug: Log when elements are found
    console.log('Upload button found:', !!uploadButton);
    console.log('Audio file input found:', !!audioFile);
    console.log('Drop zone found:', !!dropZone);
    
    uploadButton.addEventListener('click', () => {
        console.log('Upload button clicked');
        audioFile.click();
    });
    
    // Drag and drop handling
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });
    
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });
    
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        
        if (e.dataTransfer.files.length === 0) {
            console.log('No files dropped');
            return;
        }
        
        const file = e.dataTransfer.files[0];
        console.log(`Dropped file: ${file.name}, type: ${file.type}, size: ${file.size} bytes`);
        
        processFile(file);
    });
    
    // Click on drop zone to trigger file input
    dropZone.addEventListener('click', () => {
        audioFile.click();
    });
    
    // File input change handler
    audioFile.addEventListener('change', (e) => {
        if (e.target.files.length === 0) {
            console.log('No file selected');
            return;
        }
        
        const file = e.target.files[0];
        console.log(`Selected file: ${file.name}, type: ${file.type}, size: ${file.size} bytes`);
        
        processFile(file);
    });
    
    // Function to process a file
    function processFile(file) {
        const streamCheckbox = document.getElementById('streamPrerecorded');
        const isStreamMode = streamCheckbox.checked;
        
        if (isStreamMode) {
            streamPrerecordedFile(file);
        } else {
            uploadFile(file);
        }
    }
    
    // Function to upload file for batch processing
    function uploadFile(file) {
        const reader = new FileReader();
        
        reader.onload = function(e) {
          console.log(`File loaded, data length: ${e.target.result.length}`);
          const fileData = {
            name: file.name,
            data: e.target.result
          };
          
          // Get parameters from URL
          const urlElement = document.getElementById('requestUrl');
          const urlText = urlElement.textContent;
          const params = {};
          
          // Parse URL parameters
          const url = new URL(urlText.replace('ws://', 'http://'));
          // Only include parameters that are explicitly in the URL
          for (const [key, value] of url.searchParams) {
            params[key] = value;
          }
          
          console.log(`Sending file upload request with params:`, params);
          
          // Use regular HTTP POST instead of Socket.IO
          fetch('/upload', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              file: fileData,
              config: params
            })
          })
          .then(response => response.json())
          .then(result => {
            console.log(`Received response:`, result);
            if (result.error) {
              console.error('Upload error:', result.error);
              return;
            }
            
            // Display transcription
            const transcript = result.results?.channels[0]?.alternatives[0]?.transcript;
            if (transcript) {
              const finalCaptions = document.getElementById('finalCaptions');
              const finalDiv = document.createElement('span');
              finalDiv.textContent = transcript + ' ';
              finalDiv.className = 'final';
              finalCaptions.appendChild(finalDiv);
              finalDiv.scrollIntoView({ behavior: 'smooth' });
            }
          })
          .catch(error => {
            console.error('Upload error:', error);
          });
        };
        
        reader.onerror = function(e) {
          console.error('Error reading file:', e);
        };
        
        reader.onprogress = function(e) {
          if (e.lengthComputable) {
            console.log(`Reading file: ${Math.round((e.loaded / e.total) * 100)}%`);
          }
        };
        
        console.log('Starting to read file...');
        reader.readAsDataURL(file);
    }
    
    // Function to stream a prerecorded file
    function streamPrerecordedFile(file) {
        console.log(`Starting file streaming for: ${file.name}`);
        
        // Get current configuration
        const config = getConfig();
        // Force interim_results to true for file streaming
        config.interim_results = true;
        
        // Update the UI to show interim_results is true
        document.getElementById('interim_results').checked = true;
        
        // Update the URL display to show interim_results=true
        updateRequestUrl(config);
        
        // Set streaming state and update UI
        isStreamingFile = true;
        const recordButton = document.getElementById('record');
        recordButton.checked = true;
        document.body.classList.add('recording');
        
        // Display the URL in the interim results container
        const interimCaptions = document.getElementById('captions');
        const urlDiv = document.createElement('div');
        urlDiv.className = 'url-info';
        const url = document.getElementById('requestUrl').textContent
            .replace(/\s+/g, '') // Remove all whitespace including newlines
            .replace(/&amp;/g, '&'); // Fix any HTML-encoded ampersands
        urlDiv.textContent = `Streaming file: ${file.name} | Using URL: ${url}`;
        interimCaptions.appendChild(urlDiv);
        urlDiv.scrollIntoView({ behavior: 'smooth' });
        
        console.log(`File streaming config:`, config);
        
        // Step 1: Upload file via HTTP (to avoid Socket.IO size limits)
        const reader = new FileReader();
        reader.onload = function(e) {
            console.log(`File loaded for upload, data length: ${e.target.result.length}`);
            const fileData = {
                name: file.name,
                data: e.target.result
            };
            
            console.log('Uploading file for streaming...');
            
            // Upload file via HTTP POST
            fetch('/upload_for_streaming', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    file: fileData,
                    config: config
                })
            })
            .then(response => response.json())
            .then(result => {
                console.log('File upload response:', result);
                
                if (result.error) {
                    console.error('File upload error:', result.error);
                    stopFileStreaming();
                    return;
                }
                
                // Step 2: Start streaming via Socket.IO with just the file path
                console.log('Starting streaming session...');
                socket.emit('start_file_streaming', {
                    file_path: result.file_path,
                    config: config
                }, (response) => {
                    console.log('Start streaming response:', response);
                    
                    if (response && response.error) {
                        console.error('Start streaming error:', response.error);
                        stopFileStreaming();
                    }
                });
            })
            .catch(error => {
                console.error('File upload error:', error);
                stopFileStreaming();
            });
        };
        
        reader.onerror = function(e) {
            console.error('Error reading file for streaming:', e);
            stopFileStreaming();
        };
        
        console.log('Starting to read file for upload...');
        reader.readAsDataURL(file);
    }

    // Add event listener for interim_results checkbox
    const interimResultsCheckbox = document.getElementById('interim_results');
    if (interimResultsCheckbox) {
        interimResultsCheckbox.addEventListener('change', function() {
            // Only update URL if not recording or streaming
            if (!isRecording && !isStreamingFile) {
                updateRequestUrl(getConfig());
            }
        });
    }
    
    // Handle detect audio settings button
    const detectSettingsButton = document.getElementById('detectSettingsButton');
    const audioSettingsDisplay = document.getElementById('audioSettingsDisplay');
    const audioSettingsContent = document.getElementById('audioSettingsContent');
    
    if (detectSettingsButton && audioSettingsDisplay && audioSettingsContent) {
        // Listen for audio settings from server
        socket.on('audio_settings', function(settings) {
            console.log('Received audio settings:', settings);
            
            // Show the settings display
            audioSettingsDisplay.style.display = 'block';
            
            // Clear previous content
            audioSettingsContent.innerHTML = '';
            
            if (settings.error) {
                audioSettingsContent.innerHTML = `<div class="error">${settings.error}</div>`;
                return;
            }
            
            // Create HTML for settings
            const settingsHTML = `
                <div class="settings-item">
                    <strong>Device:</strong> ${settings.device_name || 'Unknown'}
                </div>
                <div class="settings-item">
                    <strong>Sample Rate:</strong> ${settings.sample_rate ? settings.sample_rate.toFixed(0) + ' Hz' : 'Unknown'}
                </div>
                <div class="settings-item">
                    <strong>Encoding:</strong> ${settings.dtype || 'Unknown'}
                </div>
                <div class="settings-item">
                    <strong>Bit Depth:</strong> ${settings.bit_depth ? settings.bit_depth + ' bits' : 'Unknown'}
                </div>
                <div class="settings-item">
                    <strong>Bitrate:</strong> ${settings.bitrate ? (settings.bitrate / 1000).toFixed(1) + ' kbps' : 'Unknown'}
                </div>
                <div class="settings-item">
                    <strong>Channels:</strong> ${settings.max_input_channels || 'Unknown'}
                </div>
            `;
            
            audioSettingsContent.innerHTML = settingsHTML;
            
            // Auto-populate form fields if they exist
            if (settings.sample_rate) {
                const sampleRateInput = document.getElementById('sample_rate');
                if (sampleRateInput) {
                    sampleRateInput.value = Math.round(settings.sample_rate);
                }
            }
            
            if (settings.dtype) {
                const encodingInput = document.getElementById('encoding');
                if (encodingInput) {
                    // Map numpy dtype to encoding format
                    let encoding = '';
                    if (settings.dtype.includes('float')) {
                        encoding = 'LINEAR32F';
                    } else if (settings.dtype.includes('int16')) {
                        encoding = 'LINEAR16';
                    } else if (settings.dtype.includes('int32')) {
                        encoding = 'LINEAR32';
                    }
                    encodingInput.value = encoding;
                }
            }
        });
        
        // Handle detect settings button click
        detectSettingsButton.addEventListener('click', function() {
            console.log('Detecting audio settings...');
            socket.emit('detect_audio_settings');
            
            // Show loading state
            audioSettingsDisplay.style.display = 'block';
            audioSettingsContent.innerHTML = '<div class="loading">Detecting audio settings...</div>';
        });
    }
    
    // Handle stream checkbox change
    const streamCheckbox = document.getElementById('streamPrerecorded');
    
    if (streamCheckbox && uploadButton && dropZone) {
        streamCheckbox.addEventListener('change', function() {
            if (this.checked) {
                uploadButton.innerHTML = '<i class="fas fa-play"></i> Stream Audio';
                dropZone.innerHTML = '<i class="fas fa-play"></i><p>Drag & drop audio files to stream</p>';
            } else {
                uploadButton.innerHTML = '<i class="fas fa-upload"></i> Upload Audio';
                dropZone.innerHTML = '<i class="fas fa-cloud-upload-alt"></i><p>Drag & drop audio files here</p>';
            }
        });
    }
});
