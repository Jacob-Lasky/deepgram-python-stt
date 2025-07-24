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

// Global variables for progress tracking
let streamingStartTime = 0;
let streamingTimer = null;
let estimatedDuration = 0;
let audioPlayer = null;
let isAudioMuted = false;

// Track if request ID has been shown for this session
let requestIdShown = false;

// Define parameter compatibility
const PARAMETER_COMPATIBILITY = {
    // Streaming-only parameters
    streaming_only: ['interim_results', 'vad_events', 'endpointing', 'utterance_end'],
    // Batch-only parameters  
    batch_only: ['paragraphs']
};

// Function to show parameter notification
function showParameterNotification(message, type = 'info') {
    // Remove any existing notification
    const existingNotification = document.querySelector('.parameter-notification');
    if (existingNotification) {
        existingNotification.remove();
    }
    
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `parameter-notification ${type}`;
    notification.innerHTML = `
        <i class="fas fa-info-circle"></i>
        <span>${message}</span>
        <button class="close-notification" onclick="this.parentElement.remove()">
            <i class="fas fa-times"></i>
        </button>
    `;
    
    // Insert at the top of the config panel
    const configPanel = document.querySelector('.config-panel');
    if (configPanel) {
        configPanel.insertBefore(notification, configPanel.firstChild);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 5000);
    }
}

// Function to disable incompatible parameters and notify user
function handleParameterCompatibility(isStreaming) {
    const disabledParams = [];
    const incompatibleParams = isStreaming ? PARAMETER_COMPATIBILITY.batch_only : PARAMETER_COMPATIBILITY.streaming_only;
    
    incompatibleParams.forEach(paramId => {
        const element = document.getElementById(paramId);
        if (element) {
            // If parameter was enabled, disable it and track for notification
            if (element.type === 'checkbox' && element.checked) {
                element.checked = false;
                disabledParams.push(paramId);
            } else if (element.type !== 'checkbox' && element.value.trim() !== '') {
                element.value = '';
                disabledParams.push(paramId);
            }
            
            // Disable the element
            element.disabled = true;
            
            // Update tooltip
            const mode = isStreaming ? 'streaming' : 'batch';
            const oppositeMode = isStreaming ? 'batch' : 'streaming';
            element.title = `${paramId} parameter is only available for ${oppositeMode} transcription, not ${mode}`;
        }
    });
    
    // Show notification if any parameters were disabled
    if (disabledParams.length > 0) {
        const mode = isStreaming ? 'streaming' : 'batch';
        const paramList = disabledParams.map(p => `'${p}'`).join(', ');
        const message = `Disabled ${paramList} parameter${disabledParams.length > 1 ? 's' : ''} - not supported in ${mode} mode`;
        showParameterNotification(message, 'warning');
        
        // Update URL to reflect parameter changes
        updateRequestUrl(getConfig());
    }
}

// Function to enable compatible parameters
function enableCompatibleParameters(isStreaming) {
    const compatibleParams = isStreaming ? PARAMETER_COMPATIBILITY.streaming_only : PARAMETER_COMPATIBILITY.batch_only;
    
    compatibleParams.forEach(paramId => {
        const element = document.getElementById(paramId);
        if (element) {
            element.disabled = false;
            
            // Restore normal tooltip
            switch(paramId) {
                case 'paragraphs':
                    element.title = 'Enable automatic paragraph formatting';
                    break;
                case 'interim_results':
                    element.title = 'Enable continuous transcription updates';
                    break;
                case 'vad_events':
                    element.title = 'Enable voice activity detection events';
                    break;
                case 'endpointing':
                    element.title = 'Speech finalization timing in milliseconds';
                    break;
                case 'utterance_end':
                    element.title = 'Utterance end detection timing in milliseconds';
                    break;
                default:
                    element.title = '';
            }
        }
    });
}

// Function to enable all parameters (neutral state)
function enableAllParameters() {
    const allParams = [...PARAMETER_COMPATIBILITY.streaming_only, ...PARAMETER_COMPATIBILITY.batch_only];
    
    allParams.forEach(paramId => {
        const element = document.getElementById(paramId);
        if (element) {
            element.disabled = false;
            
            // Restore normal tooltip
            switch(paramId) {
                case 'paragraphs':
                    element.title = 'Enable automatic paragraph formatting';
                    break;
                case 'interim_results':
                    element.title = 'Enable continuous transcription updates';
                    break;
                case 'vad_events':
                    element.title = 'Enable voice activity detection events';
                    break;
                case 'endpointing':
                    element.title = 'Speech finalization timing in milliseconds';
                    break;
                case 'utterance_end':
                    element.title = 'Utterance end detection timing in milliseconds';
                    break;
                default:
                    element.title = '';
            }
        }
    });
}

// Function to disable paragraphs checkbox during streaming
function disableParagraphsForStreaming() {
    handleParameterCompatibility(true);
}

// Function to enable paragraphs checkbox when not streaming
function enableParagraphsForPrerecorded() {
    enableAllParameters(); // Enable all parameters in neutral state
}

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
        
        // Hide progress bar and stop audio
        hideStreamingProgress();
        stopAudioPlayback();
        
        // Re-enable paragraphs checkbox when not streaming
        enableParagraphsForPrerecorded();
        
        // Reset interim_results to the checkbox state
        const config = getConfig();
        updateRequestUrl(config);
        
        console.log('File streaming stopped');
    }
}

// Function to show streaming progress
function showStreamingProgress(filename, duration = 0) {
    const progressContainer = document.getElementById('streamingProgress');
    const statusElement = document.getElementById('streamingStatus');
    const detailsElement = document.getElementById('streamingDetails');
    
    estimatedDuration = duration;
    streamingStartTime = Date.now();
    
    statusElement.textContent = 'Streaming Audio';
    detailsElement.textContent = `File: ${filename}`;
    
    progressContainer.style.display = 'block';
    
    // Start timer
    startStreamingTimer();
}

// Function to hide streaming progress
function hideStreamingProgress() {
    const progressContainer = document.getElementById('streamingProgress');
    progressContainer.style.display = 'none';
    
    // Clear timer
    if (streamingTimer) {
        clearInterval(streamingTimer);
        streamingTimer = null;
    }
}

// Function to update streaming progress
function updateStreamingProgress(status, details = '') {
    const statusElement = document.getElementById('streamingStatus');
    const detailsElement = document.getElementById('streamingDetails');
    
    if (status) statusElement.textContent = status;
    if (details) detailsElement.textContent = details;
}

// Function to start streaming timer
function startStreamingTimer() {
    if (streamingTimer) clearInterval(streamingTimer);
    
    streamingTimer = setInterval(() => {
        const elapsed = (Date.now() - streamingStartTime) / 1000;
        const minutes = Math.floor(elapsed / 60);
        const seconds = Math.floor(elapsed % 60);
        
        const timeElement = document.getElementById('streamingTime');
        timeElement.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
        
        // Update progress bar if we have estimated duration
        if (estimatedDuration > 0) {
            const progress = Math.min((elapsed / estimatedDuration) * 100, 100);
            const progressFill = document.getElementById('progressFill');
            progressFill.style.width = `${progress}%`;
        }
    }, 1000);
}

// Function to set progress bar to specific percentage
function setStreamingProgress(percentage) {
    const progressFill = document.getElementById('progressFill');
    progressFill.style.width = `${Math.min(percentage, 100)}%`;
}

// Function to setup audio player for streaming
function setupAudioPlayer(audioBlob, filename) {
    const audioPlayerContainer = document.getElementById('audioPlayerContainer');
    const streamingAudio = document.getElementById('streamingAudio');
    const toggleAudioBtn = document.getElementById('toggleAudioBtn');
    
    // Create object URL for the audio blob
    const audioUrl = URL.createObjectURL(audioBlob);
    streamingAudio.src = audioUrl;
    
    // Store reference to audio player
    audioPlayer = streamingAudio;
    
    // Show audio player
    audioPlayerContainer.style.display = 'block';
    
    // Setup toggle button
    toggleAudioBtn.onclick = toggleAudioMute;
    
    // Set initial volume
    streamingAudio.volume = isAudioMuted ? 0 : 0.7;
    
    console.log('Audio player setup complete for:', filename);
}

// Function to start synchronized audio playback
function startAudioPlayback() {
    if (audioPlayer) {
        audioPlayer.currentTime = 0;
        audioPlayer.play().catch(error => {
            console.log('Audio autoplay prevented:', error);
            // Show a message to user that they need to click play
            updateStreamingProgress('Ready to Play', 'Click the play button to hear audio while streaming');
        });
    }
}

// Function to stop audio playback
function stopAudioPlayback() {
    if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
    }
    
    // Hide audio player
    const audioPlayerContainer = document.getElementById('audioPlayerContainer');
    audioPlayerContainer.style.display = 'none';
    
    // Clean up object URL
    if (audioPlayer && audioPlayer.src) {
        URL.revokeObjectURL(audioPlayer.src);
    }
    
    audioPlayer = null;
}

// Function to toggle audio mute
function toggleAudioMute() {
    const toggleBtn = document.getElementById('toggleAudioBtn');
    const icon = toggleBtn.querySelector('i');
    
    isAudioMuted = !isAudioMuted;
    
    if (audioPlayer) {
        audioPlayer.volume = isAudioMuted ? 0 : 0.7;
    }
    
    // Update button appearance
    if (isAudioMuted) {
        icon.className = 'fas fa-volume-mute';
        toggleBtn.classList.add('muted');
    } else {
        icon.className = 'fas fa-volume-up';
        toggleBtn.classList.remove('muted');
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
    "punctuate": false,
    "multichannel": false,
    "mip_opt_out": false,
    "encoding": "",
    "channels": 1,
    "sample_rate": 16000,
    "callback": "",
    "keywords": "",
    "replace": "",
    "search": "",
    "tags": "",
    "version": "",
    "redact": [],
    "vad_events": true,
    "diarize": false,
    "filler_words": false,
    "paragraphs": false,
    "utterances": false,
    "detect_entities": false,
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
    const filename = data.file_path ? data.file_path.split('/').pop() : 'Unknown file';
    showStreamingProgress(filename, data.duration || 0);
    updateStreamingProgress('Streaming Audio', `Processing: ${filename}`);
    
    // Start audio playback if audio player is set up
    if (audioPlayer) {
        startAudioPlayback();
    }
});

socket.on('stream_error', (error) => {
    console.error('Stream error:', error);
    updateStreamingProgress('Error', `Streaming failed: ${error.error || 'Unknown error'}`);
    
    // Hide progress after showing error
    setTimeout(() => {
        if (isStreamingFile) {
            stopFileStreaming();
        }
    }, 3000);
});

socket.on('stream_finished', (data) => {
    console.log('Stream finished:', data);
    updateStreamingProgress('Completed', 'File streaming finished successfully');
    setStreamingProgress(100);
    
    // Hide progress after a delay
    setTimeout(() => {
        if (isStreamingFile) {
            stopFileStreaming();
        }
    }, 2000);
});

// Listen for transcript events from file streaming
socket.on('transcript', (data) => {
    console.log('Received transcript:', data);
    
    const interimCaptions = document.getElementById("captions");
    const finalCaptions = document.getElementById("finalCaptions");
    
    // Create interim message div with formatting similar to microphone streaming
    const interimDiv = document.createElement("div");
    
    // Build type indicator based on multiple flags
    const indicators = [];
    if (data.is_final) {
        indicators.push("Is Final");
    }
    if (data.speech_final) {
        indicators.push("Speech Final");
    }
    
    // If no special indicators, it's an interim result
    if (indicators.length === 0) {
        indicators.push("Interim Result");
    }
    
    const type = `[${indicators.join(", ")}]`;
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
        
        // Add line break if speech_final is true
        if (data.speech_final) {
            const lineBreak = document.createElement("br");
            finalCaptions.appendChild(lineBreak);
        }
        
        finalDiv.scrollIntoView({ behavior: "smooth" });
    } else if (!data.speech_final) {
        // For interim results, update or create the interim span (but not if speech_final)
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
    ['baseUrl', 'model', 'language', 'utterance_end_ms', 'endpointing', 'encoding', 
     'callback', 'keywords', 'replace', 'search', 'tags', 'version'].forEach(id => {
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
     'numerals', 'profanity_filter', 'punctuate', 'multichannel', 'mip_opt_out',
     'vad_events', 'diarize', 'filler_words', 'paragraphs', 'utterances', 'detect_entities'].forEach(id => {
        const element = document.getElementById(id);
        if (element && DEFAULT_CONFIG[id] !== undefined) {
            element.checked = DEFAULT_CONFIG[id];
        }
    });
    
    // Set redact multi-select default
    const redactElement = document.getElementById('redact');
    if (redactElement && DEFAULT_CONFIG.redact) {
        Array.from(redactElement.options).forEach(option => {
            option.selected = DEFAULT_CONFIG.redact.includes(option.value);
        });
    }

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
    ['baseUrl', 'model', 'language', 'utterance_end_ms', 'endpointing', 'encoding',
     'callback', 'keywords', 'replace', 'search', 'tags', 'version'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.value = '';
        }
    });

    ['smart_format', 'interim_results', 'no_delay', 'dictation', 
     'numerals', 'profanity_filter', 'punctuate', 'multichannel', 'mip_opt_out',
     'vad_events', 'diarize', 'filler_words', 'paragraphs', 'utterances', 'detect_entities'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.checked = false;
        }
    });
    
    // Clear redact multi-select
    const redactElement = document.getElementById('redact');
    if (redactElement) {
        Array.from(redactElement.options).forEach(option => {
            option.selected = false;
        });
    }

    // Only set values that are explicitly in the config
    Object.entries(config).forEach(([key, value]) => {
        const element = document.getElementById(key);
        if (element) {
            if (element.type === 'checkbox') {
                element.checked = value === 'true' || value === true;
            } else if (key === 'redact' && element.multiple) {
                // Handle redact multi-select
                const values = Array.isArray(value) ? value : [value];
                Array.from(element.options).forEach(option => {
                    option.selected = values.includes(option.value);
                });
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
  let type;
  let showTimestamp = true;
  
  // Build type indicator based on multiple flags
  const indicators = [];
  if (data.is_final) {
    indicators.push("Is Final");
  }
  if (data.speech_final) {
    indicators.push("Speech Final");
  }
  if (data.utterance_end_ms) {
    indicators.push("Utterance End");
    showTimestamp = false; // Don't show timestamp for utterance end
  }
  
  // If no special indicators, it's an interim result
  if (indicators.length === 0) {
    indicators.push("Interim Result");
  }
  
  type = `[${indicators.join(", ")}]`;
  
  interimDiv.textContent = showTimestamp ? 
    `${timeString}   ${type} ${data.transcription}` :
    `${type} ${data.transcription}`;
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
    
    // Add line break if speech_final is true
    if (data.speech_final) {
      const lineBreak = document.createElement("br");
      finalCaptions.appendChild(lineBreak);
    }
    
    finalDiv.scrollIntoView({ behavior: "smooth" });
  } else if (!data.utterance_end_ms && !data.speech_final) {
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

// Listen for request ID updates
socket.on("request_id_update", (data) => {
  console.log('Received request ID:', data.request_id);
  
  // Only show the request ID once per session
  if (data.request_id && !requestIdShown) {
    // Display the request ID in the interim results container
    const interimCaptions = document.getElementById("captions");
    const requestIdDiv = document.createElement("div");
    requestIdDiv.className = "url-info";
    requestIdDiv.textContent = `Request ID: ${data.request_id}`;
    interimCaptions.appendChild(requestIdDiv);
    requestIdDiv.scrollIntoView({ behavior: "smooth" });
    
    // Mark that we've shown the request ID for this session
    requestIdShown = true;
  }
});

// Listen for raw response data
// Create collapsible JSON tree
function createJsonTree(data, key = null, level = 0) {
  const container = document.createElement('div');
  container.className = 'json-node';
  
  if (data === null) {
    container.innerHTML = `${key ? `<span class="json-key">${key}:</span> ` : ''}<span class="json-null">null</span>`;
    return container;
  }
  
  if (typeof data === 'string') {
    container.innerHTML = `${key ? `<span class="json-key">${key}:</span> ` : ''}<span class="json-string">"${escapeHtml(data)}"</span>`;
    return container;
  }
  
  if (typeof data === 'number') {
    container.innerHTML = `${key ? `<span class="json-key">${key}:</span> ` : ''}<span class="json-number">${data}</span>`;
    return container;
  }
  
  if (typeof data === 'boolean') {
    container.innerHTML = `${key ? `<span class="json-key">${key}:</span> ` : ''}<span class="json-boolean">${data}</span>`;
    return container;
  }
  
  if (Array.isArray(data)) {
    const header = document.createElement('div');
    header.className = 'json-header';
    header.innerHTML = `
      <span class="json-toggle">▶</span>
      ${key ? `<span class="json-key">${key}:</span>` : ''}
      <span class="json-bracket">[</span>
      <span class="json-count">${data.length} items</span>
      <span class="json-bracket">]</span>
    `;
    
    const content = document.createElement('div');
    content.className = 'json-content collapsed';
    content.style.marginLeft = '20px';
    
    data.forEach((item, index) => {
      const itemNode = createJsonTree(item, index, level + 1);
      content.appendChild(itemNode);
    });
    
    header.addEventListener('click', () => {
      const toggle = header.querySelector('.json-toggle');
      const isCollapsed = content.classList.contains('collapsed');
      
      if (isCollapsed) {
        content.classList.remove('collapsed');
        toggle.textContent = '▼';
      } else {
        content.classList.add('collapsed');
        toggle.textContent = '▶';
      }
    });
    
    container.appendChild(header);
    container.appendChild(content);
    return container;
  }
  
  if (typeof data === 'object') {
    const keys = Object.keys(data);
    const header = document.createElement('div');
    header.className = 'json-header';
    header.innerHTML = `
      <span class="json-toggle">▶</span>
      ${key ? `<span class="json-key">${key}:</span>` : ''}
      <span class="json-bracket">{</span>
      <span class="json-count">${keys.length} keys</span>
      <span class="json-bracket">}</span>
    `;
    
    const content = document.createElement('div');
    content.className = 'json-content collapsed';
    content.style.marginLeft = '20px';
    
    keys.forEach(objKey => {
      const itemNode = createJsonTree(data[objKey], objKey, level + 1);
      content.appendChild(itemNode);
    });
    
    header.addEventListener('click', () => {
      const toggle = header.querySelector('.json-toggle');
      const isCollapsed = content.classList.contains('collapsed');
      
      if (isCollapsed) {
        content.classList.remove('collapsed');
        toggle.textContent = '▼';
      } else {
        content.classList.add('collapsed');
        toggle.textContent = '▶';
      }
    });
    
    container.appendChild(header);
    container.appendChild(content);
    return container;
  }
  
  // Fallback for unknown types
  container.innerHTML = `${key ? `<span class="json-key">${key}:</span> ` : ''}<span class="json-unknown">${String(data)}</span>`;
  return container;
}

// Helper function to escape HTML
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Format transcript with speaker labels for diarization
function formatTranscriptWithSpeakers(data) {
  // Check if this is a multichannel response
  if (data.results && data.results.channels && data.results.channels.length > 1) {
    // Handle multichannel audio
    let formattedTranscript = '';
    data.results.channels.forEach((channel, channelIndex) => {
      if (channel.alternatives && channel.alternatives[0]) {
        const alt = channel.alternatives[0];
        formattedTranscript += `\n[Channel ${channelIndex + 1}]\n`;
        
        if (alt.words && alt.words.length > 0) {
          // Group words by speaker within this channel
          const speakerGroups = groupWordsBySpeaker(alt.words);
          speakerGroups.forEach(group => {
            formattedTranscript += `Speaker ${group.speaker}: ${group.text}\n`;
          });
        } else {
          formattedTranscript += alt.transcript + '\n';
        }
      }
    });
    return formattedTranscript.trim();
  }
  
  // Handle single channel with diarization
  if (data.results && data.results.channels && data.results.channels[0]) {
    const channel = data.results.channels[0];
    if (channel.alternatives && channel.alternatives[0]) {
      const alt = channel.alternatives[0];
      
      // Check if words have speaker information
      if (alt.words && alt.words.length > 0 && alt.words[0].hasOwnProperty('speaker')) {
        const speakerGroups = groupWordsBySpeaker(alt.words);
        let formattedTranscript = '';
        speakerGroups.forEach(group => {
          formattedTranscript += `Speaker ${group.speaker}: ${group.text}\n`;
        });
        return formattedTranscript.trim();
      } else {
        // No speaker information, return plain transcript
        return alt.transcript;
      }
    }
  }
  
  // Fallback to plain transcript
  return data.results?.channels[0]?.alternatives[0]?.transcript || '';
}

// Group words by speaker for diarization
function groupWordsBySpeaker(words) {
  const groups = [];
  let currentGroup = null;
  
  words.forEach(word => {
    const speaker = word.speaker !== undefined ? word.speaker : 0;
    
    if (!currentGroup || currentGroup.speaker !== speaker) {
      // Start new speaker group
      currentGroup = {
        speaker: speaker,
        words: [word],
        text: word.punctuated_word || word.word
      };
      groups.push(currentGroup);
    } else {
      // Add to current speaker group
      currentGroup.words.push(word);
      currentGroup.text += ' ' + (word.punctuated_word || word.word);
    }
  });
  
  return groups;
}

socket.on("raw_response", (data) => {
  console.log('Received raw response:', data);
  console.log('Request ID:', data.request_id);
  console.log('Parameters:', data.parameters);
  console.log('Parameters keys:', data.parameters ? Object.keys(data.parameters) : 'No parameters');
  
  // Display the raw response in the interim results container
  const interimCaptions = document.getElementById("captions");
  
  // Create a collapsible raw response section
  const rawResponseDiv = document.createElement("div");
  rawResponseDiv.className = "raw-response-container";
  
  // Create header with toggle functionality
  const headerDiv = document.createElement("div");
  headerDiv.className = "raw-response-header";
  
  // Build header text with request_id if available
  let headerText = `🔍 Raw Response (${data.type})`;
  if (data.request_id) {
    headerText += ` - Request ID: ${data.request_id}`;
  }
  
  headerDiv.innerHTML = `
    <span class="raw-response-title">${headerText}</span>
    <span class="raw-response-toggle">▼</span>
  `;
  
  // Create content area (initially collapsed)
  const contentDiv = document.createElement("div");
  contentDiv.className = "raw-response-content collapsed";
  
  // Add parameters section if available
  console.log('Checking parameters section:', {
    hasParameters: !!data.parameters,
    parametersType: typeof data.parameters,
    parametersKeys: data.parameters ? Object.keys(data.parameters) : 'N/A',
    parametersLength: data.parameters ? Object.keys(data.parameters).length : 0
  });
  
  if (data.parameters && Object.keys(data.parameters).length > 0) {
    console.log('Creating parameters section...');
    const parametersDiv = document.createElement("div");
    parametersDiv.className = "raw-response-section";
    
    const parametersHeader = document.createElement("h4");
    parametersHeader.textContent = "Parameters Used:";
    parametersHeader.style.color = "#47aca9";
    parametersHeader.style.marginBottom = "8px";
    
    const parametersContainer = document.createElement("div");
    parametersContainer.className = "json-tree-container";
    
    try {
      const parametersTree = createJsonTree(data.parameters);
      parametersContainer.appendChild(parametersTree);
    } catch (e) {
      const preElement = document.createElement("pre");
      preElement.className = "raw-response-data";
      preElement.textContent = JSON.stringify(data.parameters, null, 2);
      parametersContainer.appendChild(preElement);
    }
    
    parametersDiv.appendChild(parametersHeader);
    parametersDiv.appendChild(parametersContainer);
    contentDiv.appendChild(parametersDiv);
  }
  
  // Add response data section
  const responseDiv = document.createElement("div");
  responseDiv.className = "raw-response-section";
  
  const responseHeader = document.createElement("h4");
  responseHeader.textContent = "Response Data:";
  responseHeader.style.color = "#47aca9";
  responseHeader.style.marginBottom = "8px";
  responseHeader.style.marginTop = "16px";
  
  // Create collapsible JSON tree
  const jsonContainer = document.createElement("div");
  jsonContainer.className = "json-tree-container";
  
  try {
    const jsonTree = createJsonTree(data.data);
    jsonContainer.appendChild(jsonTree);
  } catch (e) {
    // Fallback to plain text if JSON parsing fails
    const preElement = document.createElement("pre");
    preElement.className = "raw-response-data";
    preElement.textContent = String(data.data);
    jsonContainer.appendChild(preElement);
  }
  
  responseDiv.appendChild(responseHeader);
  responseDiv.appendChild(jsonContainer);
  contentDiv.appendChild(responseDiv);
  
  // Add toggle functionality
  headerDiv.addEventListener('click', () => {
    const isCollapsed = contentDiv.classList.contains('collapsed');
    if (isCollapsed) {
      contentDiv.classList.remove('collapsed');
      headerDiv.querySelector('.raw-response-toggle').textContent = '▲';
    } else {
      contentDiv.classList.add('collapsed');
      headerDiv.querySelector('.raw-response-toggle').textContent = '▼';
    }
  });
  
  rawResponseDiv.appendChild(headerDiv);
  rawResponseDiv.appendChild(contentDiv);
  
  interimCaptions.appendChild(rawResponseDiv);
  rawResponseDiv.scrollIntoView({ behavior: "smooth" });
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
  // Reset request ID flag for new session
  requestIdShown = false;
  // Disable paragraphs checkbox during streaming
  disableParagraphsForStreaming();
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
    
    // Re-enable paragraphs checkbox when not streaming
    enableParagraphsForPrerecorded();
    
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
    addIfSet('punctuate');
    addIfSet('multichannel');
    addIfSet('mip_opt_out');
    addIfSet('encoding');
    addIfSet('channels');
    addIfSet('sample_rate');
    addIfSet('callback');
    addIfSet('keywords');
    addIfSet('replace');
    addIfSet('search');
    addIfSet('tags');
    addIfSet('version');
    addIfSet('vad_events');
    addIfSet('diarize');
    addIfSet('filler_words');
    addIfSet('paragraphs');
    addIfSet('utterances');
    addIfSet('detect_entities');
    
    // Handle redact multi-select specially
    const redactElement = document.getElementById('redact');
    if (redactElement) {
        const selectedValues = Array.from(redactElement.selectedOptions).map(option => option.value);
        if (selectedValues.length > 0) {
            config['redact'] = selectedValues;
        }
    }

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
    
    const utteranceEnd = document.getElementById('utterance_end_ms').value;
    if (utteranceEnd) params.append('utterance_end_ms', utteranceEnd);
    
    const endpointing = document.getElementById('endpointing').value;
    if (endpointing) params.append('endpointing', endpointing);
    
    const encoding = document.getElementById('encoding').value;
    if (encoding) params.append('encoding', encoding);
    
    const channels = document.getElementById('channels').value;
    if (channels) params.append('channels', channels);
    
    const sampleRate = document.getElementById('sample_rate').value;
    if (sampleRate) params.append('sample_rate', sampleRate);
    
    const callback = document.getElementById('callback').value;
    if (callback) params.append('callback', callback);
    
    const keywords = document.getElementById('keywords').value;
    if (keywords) params.append('keywords', keywords);
    
    const replace = document.getElementById('replace').value;
    if (replace) params.append('replace', replace);
    
    const search = document.getElementById('search').value;
    if (search) params.append('search', search);
    
    const tags = document.getElementById('tags').value;
    if (tags) params.append('tags', tags);
    
    const version = document.getElementById('version').value;
    if (version) params.append('version', version);
    
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
    
    const multichannel = document.getElementById('multichannel').checked;
    if (multichannel) params.append('multichannel', 'true');
    
    const mipOptOut = document.getElementById('mip_opt_out').checked;
    if (mipOptOut) params.append('mip_opt_out', 'true');
    
    const vadEvents = document.getElementById('vad_events').checked;
    if (vadEvents) params.append('vad_events', 'true');
    
    // Handle redact multi-select
    const redactElement = document.getElementById('redact');
    if (redactElement) {
        const selectedValues = Array.from(redactElement.selectedOptions).map(option => option.value);
        selectedValues.forEach(value => {
            if (value) params.append('redact', value);
        });
    }
    
    const diarize = document.getElementById('diarize').checked;
    if (diarize) params.append('diarize', 'true');
    
    const fillerWords = document.getElementById('filler_words').checked;
    if (fillerWords) params.append('filler_words', 'true');
    
    const paragraphs = document.getElementById('paragraphs').checked;
    if (paragraphs) params.append('paragraphs', 'true');
    
    const utterances = document.getElementById('utterances').checked;
    if (utterances) params.append('utterances', 'true');
    
    const detectEntities = document.getElementById('detect_entities').checked;
    if (detectEntities) params.append('detect_entities', 'true');
    
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
        // Handle parameter compatibility for batch processing
        handleParameterCompatibility(false); // false = batch mode
        
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
            
            // Display transcription with speaker formatting
            const formattedTranscript = formatTranscriptWithSpeakers(result);
            if (formattedTranscript) {
              const finalCaptions = document.getElementById('finalCaptions');
              const finalDiv = document.createElement('div');
              
              // Check if transcript has speaker labels (contains "Speaker ")
              if (formattedTranscript.includes('Speaker ') || formattedTranscript.includes('[Channel ')) {
                // Use pre-formatted text to preserve line breaks
                finalDiv.style.whiteSpace = 'pre-line';
                finalDiv.textContent = formattedTranscript;
              } else {
                // Plain transcript, use span for inline display
                finalDiv.textContent = formattedTranscript + ' ';
              }
              
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
        
        // Reset request ID flag for new session
        requestIdShown = false;
        // Disable paragraphs checkbox during streaming
        disableParagraphsForStreaming();
        
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
            
            // Show initial progress
            showStreamingProgress(file.name);
            updateStreamingProgress('Uploading', `Preparing ${file.name} for streaming...`);
            
            // Setup audio player with the file for synchronized playback
            const audioBlob = new Blob([new Uint8Array(atob(e.target.result.split(',')[1]).split('').map(c => c.charCodeAt(0)))], {type: file.type});
            setupAudioPlayer(audioBlob, file.name);
            
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
                updateStreamingProgress('Connecting', 'Establishing connection to Deepgram...');
                
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
    
    // Initialize all parameters as enabled (neutral state by default)
    enableAllParameters();
});
