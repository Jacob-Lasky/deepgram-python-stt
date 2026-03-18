// Alpine.js component for Deepgram STT Explorer

function appData() {
  return {

    // ---- State ----
    mode: 'mic',         // 'mic' | 'file' | 'batch'
    rightTab: 'transcript',
    connected: false,
    socket: null,

    // Recording
    recording: false,
    mediaRecorder: null,
    micStream: null,

    // File streaming
    uploadedFile: null,      // { name, serverName, size }
    fileStreamState: 'idle', // 'idle' | 'streaming' | 'done' | 'error'
    _fileAudio: null,        // Audio element for file playback

    // Batch
    batchSource: '',
    batchLoading: false,
    batchResult: null,

    // TTS Test
    ttsText: '',
    ttsProvider: 'deepgram',  // 'deepgram' | 'elevenlabs'
    ttsModel: 'aura-2-asteria-en',
    ttsLang: 'en',
    ttsMode: 'batch',   // 'batch' | 'streaming' | 'both'
    ttsLoading: false,
    ttsResult: null,
    ttsLastText: '',
    ttsLastTranscript: '',
    ttsLastStreamTranscript: '',
    // ElevenLabs voices cache
    elevenVoices: [],
    elevenVoicesLoading: false,

    // Deepgram Aura-2 voices grouped by language
    dgVoiceLangs: [
      { code: 'en', label: 'English' },
      { code: 'es', label: 'Spanish' },
      { code: 'de', label: 'German' },
      { code: 'fr', label: 'French' },
      { code: 'it', label: 'Italian' },
      { code: 'nl', label: 'Dutch' },
      { code: 'ja', label: 'Japanese' },
    ],
    dgVoices: [
      // English — American
      { id: 'aura-2-asteria-en', name: 'Asteria', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-andromeda-en', name: 'Andromeda', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-apollo-en', name: 'Apollo', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-arcas-en', name: 'Arcas', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-aries-en', name: 'Aries', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-athena-en', name: 'Athena', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-atlas-en', name: 'Atlas', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-aurora-en', name: 'Aurora', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-callista-en', name: 'Callista', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-cora-en', name: 'Cora', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-cordelia-en', name: 'Cordelia', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-delia-en', name: 'Delia', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-electra-en', name: 'Electra', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-harmonia-en', name: 'Harmonia', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-helena-en', name: 'Helena', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-hera-en', name: 'Hera', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-hermes-en', name: 'Hermes', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-iris-en', name: 'Iris', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-juno-en', name: 'Juno', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-jupiter-en', name: 'Jupiter', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-luna-en', name: 'Luna', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-mars-en', name: 'Mars', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-minerva-en', name: 'Minerva', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-neptune-en', name: 'Neptune', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-odysseus-en', name: 'Odysseus', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-ophelia-en', name: 'Ophelia', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-orion-en', name: 'Orion', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-orpheus-en', name: 'Orpheus', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-phoebe-en', name: 'Phoebe', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-pluto-en', name: 'Pluto', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-saturn-en', name: 'Saturn', gender: 'M', accent: 'American', lang: 'en' },
      { id: 'aura-2-selene-en', name: 'Selene', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-thalia-en', name: 'Thalia', gender: 'F', accent: 'American', lang: 'en' },
      { id: 'aura-2-zeus-en', name: 'Zeus', gender: 'M', accent: 'American', lang: 'en' },
      // English — Southern US
      { id: 'aura-2-janus-en', name: 'Janus', gender: 'F', accent: 'Southern US', lang: 'en' },
      // English — Filipino
      { id: 'aura-2-amalthea-en', name: 'Amalthea', gender: 'F', accent: 'Filipino', lang: 'en' },
      // English — British
      { id: 'aura-2-draco-en', name: 'Draco', gender: 'M', accent: 'British', lang: 'en' },
      { id: 'aura-2-pandora-en', name: 'Pandora', gender: 'F', accent: 'British', lang: 'en' },
      // English — Australian
      { id: 'aura-2-hyperion-en', name: 'Hyperion', gender: 'M', accent: 'Australian', lang: 'en' },
      { id: 'aura-2-theia-en', name: 'Theia', gender: 'F', accent: 'Australian', lang: 'en' },
      // Spanish
      { id: 'aura-2-estrella-es', name: 'Estrella', gender: 'F', accent: 'Mexican', lang: 'es' },
      { id: 'aura-2-sirio-es', name: 'Sirio', gender: 'M', accent: 'Mexican', lang: 'es' },
      { id: 'aura-2-javier-es', name: 'Javier', gender: 'M', accent: 'Mexican', lang: 'es' },
      { id: 'aura-2-luciano-es', name: 'Luciano', gender: 'M', accent: 'Mexican', lang: 'es' },
      { id: 'aura-2-olivia-es', name: 'Olivia', gender: 'F', accent: 'Mexican', lang: 'es' },
      { id: 'aura-2-valerio-es', name: 'Valerio', gender: 'M', accent: 'Mexican', lang: 'es' },
      { id: 'aura-2-nestor-es', name: 'Nestor', gender: 'M', accent: 'Peninsular', lang: 'es' },
      { id: 'aura-2-carina-es', name: 'Carina', gender: 'F', accent: 'Peninsular', lang: 'es' },
      { id: 'aura-2-alvaro-es', name: 'Alvaro', gender: 'M', accent: 'Peninsular', lang: 'es' },
      { id: 'aura-2-diana-es', name: 'Diana', gender: 'F', accent: 'Peninsular', lang: 'es' },
      { id: 'aura-2-agustina-es', name: 'Agustina', gender: 'F', accent: 'Peninsular', lang: 'es' },
      { id: 'aura-2-silvia-es', name: 'Silvia', gender: 'F', accent: 'Peninsular', lang: 'es' },
      { id: 'aura-2-celeste-es', name: 'Celeste', gender: 'F', accent: 'Colombian', lang: 'es' },
      { id: 'aura-2-gloria-es', name: 'Gloria', gender: 'F', accent: 'Colombian', lang: 'es' },
      { id: 'aura-2-antonia-es', name: 'Antonia', gender: 'F', accent: 'Argentine', lang: 'es' },
      { id: 'aura-2-aquila-es', name: 'Aquila', gender: 'M', accent: 'Latin American', lang: 'es' },
      { id: 'aura-2-selena-es', name: 'Selena', gender: 'F', accent: 'Latin American', lang: 'es' },
      // German
      { id: 'aura-2-julius-de', name: 'Julius', gender: 'M', accent: 'German', lang: 'de' },
      { id: 'aura-2-viktoria-de', name: 'Viktoria', gender: 'F', accent: 'German', lang: 'de' },
      { id: 'aura-2-elara-de', name: 'Elara', gender: 'F', accent: 'German', lang: 'de' },
      { id: 'aura-2-aurelia-de', name: 'Aurelia', gender: 'F', accent: 'German', lang: 'de' },
      { id: 'aura-2-lara-de', name: 'Lara', gender: 'F', accent: 'German', lang: 'de' },
      { id: 'aura-2-fabian-de', name: 'Fabian', gender: 'M', accent: 'German', lang: 'de' },
      { id: 'aura-2-kara-de', name: 'Kara', gender: 'F', accent: 'German', lang: 'de' },
      // French
      { id: 'aura-2-agathe-fr', name: 'Agathe', gender: 'F', accent: 'French', lang: 'fr' },
      { id: 'aura-2-hector-fr', name: 'Hector', gender: 'M', accent: 'French', lang: 'fr' },
      // Italian
      { id: 'aura-2-livia-it', name: 'Livia', gender: 'F', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-dionisio-it', name: 'Dionisio', gender: 'M', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-melia-it', name: 'Melia', gender: 'F', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-elio-it', name: 'Elio', gender: 'M', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-flavio-it', name: 'Flavio', gender: 'M', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-maia-it', name: 'Maia', gender: 'F', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-cinzia-it', name: 'Cinzia', gender: 'F', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-cesare-it', name: 'Cesare', gender: 'M', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-perseo-it', name: 'Perseo', gender: 'M', accent: 'Italian', lang: 'it' },
      { id: 'aura-2-demetra-it', name: 'Demetra', gender: 'F', accent: 'Italian', lang: 'it' },
      // Dutch
      { id: 'aura-2-rhea-nl', name: 'Rhea', gender: 'F', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-sander-nl', name: 'Sander', gender: 'M', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-beatrix-nl', name: 'Beatrix', gender: 'F', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-daphne-nl', name: 'Daphne', gender: 'F', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-cornelia-nl', name: 'Cornelia', gender: 'F', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-hestia-nl', name: 'Hestia', gender: 'F', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-lars-nl', name: 'Lars', gender: 'M', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-roman-nl', name: 'Roman', gender: 'M', accent: 'Dutch', lang: 'nl' },
      { id: 'aura-2-leda-nl', name: 'Leda', gender: 'F', accent: 'Dutch', lang: 'nl' },
      // Japanese
      { id: 'aura-2-fujin-ja', name: 'Fujin', gender: 'M', accent: 'Japanese', lang: 'ja' },
      { id: 'aura-2-izanami-ja', name: 'Izanami', gender: 'F', accent: 'Japanese', lang: 'ja' },
      { id: 'aura-2-uzume-ja', name: 'Uzume', gender: 'F', accent: 'Japanese', lang: 'ja' },
      { id: 'aura-2-ebisu-ja', name: 'Ebisu', gender: 'M', accent: 'Japanese', lang: 'ja' },
      { id: 'aura-2-ama-ja', name: 'Ama', gender: 'F', accent: 'Japanese', lang: 'ja' },
    ],

    // Transcript
    finalTranscript: '',
    interimTranscript: '',
    interimLog: [],
    debugPanelOpen: false,

    // Responses
    responses: [],

    // Stream status
    streamUrl: '',

    // URL bar
    urlDisplay: '',
    urlFocused: false,
    urlCopied: false,

    // Import/Export
    importExportOpen: false,
    importExportText: '',

    // Toast
    toast: { visible: false, message: '', type: 'success' },
    _toastTimer: null,

    // Section open states
    sections: {
      core: true,
      audio: false,
      formatting: false,
      features: false,
      redaction: false,
      prompting: false,
      intelligence: false,
      streaming: false,
      advanced: false,
    },

    // Redact options
    redactOptions: [
      { value: 'pci', label: 'PCI' },
      { value: 'ssn', label: 'SSN' },
      { value: 'credit_card', label: 'Credit Card' },
      { value: 'account_number', label: 'Account #' },
      { value: 'routing_number', label: 'Routing #' },
      { value: 'passport_number', label: 'Passport' },
      { value: 'driver_license', label: 'Driver License' },
      { value: 'numerical_pii', label: 'Numerical PII' },
      { value: 'numbers', label: 'Numbers' },
      { value: 'aggressive_numbers', label: 'Aggressive Nums' },
      { value: 'phi', label: 'PHI' },
      { value: 'name', label: 'Name' },
      { value: 'dob', label: 'Date of Birth' },
      { value: 'username', label: 'Username' },
    ],

    // ---- Params ----
    params: {
      model: 'nova-3',
      language: 'en',
      version: '',
      base_url: 'api.deepgram.com',
      encoding: '',
      sample_rate: 0,
      channels: 0,
      endpointing: 10,
      utterance_end_ms: 1000,
      smart_format: true,
      punctuate: false,
      numerals: false,
      filler_words: false,
      dictation: false,
      profanity_filter: false,
      diarize: false,
      diarize_version: '',
      detect_entities: false,
      multichannel: false,
      utterances: false,
      paragraphs: false,
      redact: [],
      keyterms: [],
      entity_prompt: '',
      keywords: '',
      search: '',
      replace: '',
      topics: false,
      intents: false,
      sentiment: false,
      interim_results: true,
      vad_events: true,
      no_delay: false,
      callback: '',
      tags: '',
      mip_opt_out: false,
      alternatives: 0,
      word_confidence: false,
      extra_json: '',
    },

    // ---- Init ----
    init() {
      this.setupSocket();

      // Watch params and update URL (debounced)
      this._urlUpdateTimer = null;
      this.$watch('params', () => {
        if (!this.urlFocused) {
          clearTimeout(this._urlUpdateTimer);
          this._urlUpdateTimer = setTimeout(() => this.refreshUrl(), 80);
        }
      }, { deep: true });

      this.$watch('mode', () => {
        clearTimeout(this._urlUpdateTimer);
        this._urlUpdateTimer = setTimeout(() => this.refreshUrl(), 80);
      });

      this.$watch('ttsModel', () => {
        if (this.mode === 'tts' && !this.urlFocused) {
          clearTimeout(this._urlUpdateTimer);
          this._urlUpdateTimer = setTimeout(() => this.refreshUrl(), 80);
        }
      });

      this.refreshUrl();
    },

    // ---- Transcript helpers ----
    _applyTranscriptUpdate(data) {
      const text = data.transcript || '';
      const prefix = (data.speaker != null) ? `<span class="speaker-label">[Speaker ${data.speaker}]</span> ` : '';
      if (data.is_final) {
        this.interimTranscript = '';
        if (text.trim()) {
          this.finalTranscript += prefix + this.escapeHtml(text) + '\n';
          this.$nextTick(() => {
            const el = this.$refs.transcriptFinal;
            if (el) el.scrollTop = el.scrollHeight;
          });
          this._logDebug('final', (data.speaker != null ? `[Speaker ${data.speaker}] ` : '') + text);
        }
        this.addResponse('final', data);
      } else {
        this.interimTranscript = (data.speaker != null ? `[Speaker ${data.speaker}] ` : '') + text;
        if (text.trim()) {
          this._logDebug('interim', (data.speaker != null ? `[Speaker ${data.speaker}] ` : '') + text);
        }
        this.addResponse('interim', data);
      }
    },

    // ---- SocketIO ----
    setupSocket() {
      this.socket = io(window.location.origin, { transports: ['websocket', 'polling'] });

      this.socket.on('connect', () => {
        this.connected = true;
      });

      this.socket.on('disconnect', () => {
        this.connected = false;
        this.recording = false;
        if (this.fileStreamState === 'streaming') this.fileStreamState = 'idle';
      });

      this.socket.on('transcription_update', (data) => {
        this._applyTranscriptUpdate(data);
      });

      this.socket.on('stream_started', (data) => {
        this.streamUrl = data.url || '';
        if (this.fileStreamState === 'idle') this.fileStreamState = 'streaming';
        // Flush any audio buffered before the connection was ready
        if (this._pendingAudio && this._pendingAudio.length > 0) {
          this._pendingAudio.forEach(buf => this.socket.emit('audio_stream', buf));
          this._pendingAudio = [];
        }
        this._streamReady = true;
      });

      this.socket.on('stream_finished', () => {
        this.streamUrl = '';
        this.recording = false;
        if (this.fileStreamState === 'streaming') this.fileStreamState = 'done';
        if (this._fileAudio) {
          this._fileAudio.pause();
          this._fileAudio = null;
        }
      });

      this.socket.on('stream_error', (data) => {
        console.error('[DG] stream_error:', data.message);
        // Stop MediaRecorder and release mic so next Start works cleanly
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop();
        if (this.micStream) { this.micStream.getTracks().forEach(t => t.stop()); this.micStream = null; }
        this._streamReady = false;
        this._pendingAudio = [];
        this.streamUrl = '';
        this.fileStreamState = 'error';
        this.recording = false;
        this.showToast(data.message || 'Stream error', 'error');
        const msg = data.message || 'Stream error';
        this.responses.push({ type: 'error', data: { message: msg }, timestamp: new Date().toLocaleTimeString(), preview: msg, open: true });
        this.rightTab = 'responses';
        this.$nextTick(() => { const el = this.$refs.responsesList; if (el) el.scrollTop = el.scrollHeight; });
      });

      this.socket.on('audio_settings', (data) => {
        if (data.sample_rate) this.params.sample_rate = data.sample_rate;
        if (data.channels) this.params.channels = data.channels;
        this.showToast(`Detected: ${data.sample_rate}Hz, ${data.channels}ch`, 'success');
      });
    },

    // ---- Mode switch ----
    setMode(m) {
      // Stop any active file stream before switching tabs
      if (m !== 'file' && this.fileStreamState === 'streaming') {
        this.stopFileStream();
      }
      this.mode = m;
      if (m === 'batch') {
        this.rightTab = 'batch';
      } else if (m === 'tts') {
        this.rightTab = 'tts';
      } else {
        this.rightTab = 'transcript';
      }
    },

    // ---- Microphone ----
    async startMic() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.micStream = stream;
        const options = {};
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
          options.mimeType = 'audio/webm;codecs=opus';
        } else if (MediaRecorder.isTypeSupported('audio/webm')) {
          options.mimeType = 'audio/webm';
        }
        this.params.encoding = '';  // Deepgram auto-detects WebM container

        this._pendingAudio = [];
        this._streamReady = false;

        this.mediaRecorder = new MediaRecorder(stream, options);
        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0 && this.socket) {
            e.data.arrayBuffer().then(buf => {
              if (this._streamReady) {
                this.socket.emit('audio_stream', buf);
              } else {
                this._pendingAudio.push(buf);
              }
            });
          }
        };
        this.mediaRecorder.start(250);

        const cleanParams = this.getCleanParams('streaming');
        this.socket.emit('toggle_transcription', {
          params: cleanParams,
          action: 'start',
        });
        this.recording = true;
        this.rightTab = 'transcript';
      } catch (err) {
        console.error('[DG] startMic error:', err);
        this.showToast('Microphone access denied: ' + err.message, 'error');
      }
    },

    stopMic() {
      this.recording = false;
      if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
        this.mediaRecorder.stop();
      }
      if (this.micStream) {
        this.micStream.getTracks().forEach(t => t.stop());
        this.micStream = null;
      }
      this.socket.emit('toggle_transcription', { params: {}, action: 'stop' });
      this.streamUrl = '';
    },

    detectAudioSettings() {
      this.socket.emit('detect_audio_settings', {});
    },

    // ---- File streaming ----
    handleFileDrop(event) {
      event.currentTarget.classList.remove('drag-over');
      const file = event.dataTransfer.files[0];
      if (file) this.uploadFile(file);
    },

    handleFileSelect(event) {
      const file = event.target.files[0];
      if (file) this.uploadFile(file);
      event.target.value = '';
    },

    async uploadFile(file) {
      const formData = new FormData();
      formData.append('file', file);
      try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        this.uploadedFile = { name: file.name, serverName: data.filename, size: data.size };
        this.fileStreamState = 'idle';
        this.showToast(`Uploaded: ${file.name}`, 'success');
      } catch (err) {
        this.showToast('Upload failed: ' + err.message, 'error');
      }
    },

    startFileStream() {
      if (!this.uploadedFile) return;
      this.fileStreamState = 'streaming';
      this.rightTab = 'transcript';

      // Play file through speakers — server streams to Deepgram at the same
      // real-time rate, so transcripts arrive in sync with playback naturally.
      this._fileAudio = new Audio(`/files/${encodeURIComponent(this.uploadedFile.serverName)}`);
      this._fileAudio.play().catch(e => console.warn('[DG] audio playback failed:', e));

      this.socket.emit('start_file_streaming', {
        params: this.getCleanParams('streaming'),
        filename: this.uploadedFile.serverName,
      });
    },

    stopFileStream() {
      if (this._fileAudio) {
        this._fileAudio.pause();
        this._fileAudio = null;
      }
      this.socket.emit('stop_file_streaming', {});
      this.fileStreamState = 'idle';
      this.streamUrl = '';
    },

    // ---- Batch ----
    handleBatchFileSelect(event) {
      const file = event.target.files[0];
      if (file) {
        this.uploadFile(file).then(() => {
          if (this.uploadedFile) {
            this.batchSource = this.uploadedFile.serverName;
          }
        });
      }
      event.target.value = '';
    },

    async runBatch() {
      if (!this.batchSource) return;
      this.batchLoading = true;
      this.batchResult = null;
      this.rightTab = 'batch';

      const isUrl = this.batchSource.startsWith('http');
      const body = {
        params: this.getCleanParams('batch'),
      };
      if (isUrl) {
        body.url = this.batchSource;
      } else {
        body.filename = this.batchSource;
      }

      try {
        const res = await fetch('/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        this.batchResult = data;

        // Also extract transcript and show in responses
        const transcript = this.extractBatchTranscript(data);
        if (transcript) {
          this.finalTranscript += this.escapeHtml(transcript) + '\n';
        }
        this.addResponse('final', data);
        this.showToast('Batch transcription complete', 'success');
      } catch (err) {
        this.showToast('Batch error: ' + err.message, 'error');
        this.batchResult = { error: err.message };
      } finally {
        this.batchLoading = false;
      }
    },

    filteredDgVoices() {
      return this.dgVoices.filter(v => v.lang === this.ttsLang);
    },

    switchTtsLang(lang) {
      this.ttsLang = lang;
      const voices = this.dgVoices.filter(v => v.lang === lang);
      if (voices.length && !voices.find(v => v.id === this.ttsModel)) {
        this.ttsModel = voices[0].id;
      }
    },

    async loadElevenVoices() {
      if (this.elevenVoices.length > 0) return;
      this.elevenVoicesLoading = true;
      try {
        const res = await fetch('/api/tts-voices?provider=elevenlabs');
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        this.elevenVoices = data.voices || [];
      } catch (err) {
        this.showToast('Failed to load ElevenLabs voices: ' + err.message, 'error');
      } finally {
        this.elevenVoicesLoading = false;
      }
    },

    switchTtsProvider(provider) {
      this.ttsProvider = provider;
      if (provider === 'elevenlabs') {
        this.loadElevenVoices();
        if (!this.ttsModel || this.ttsModel.startsWith('aura-')) {
          this.ttsModel = '';
        }
      } else {
        if (!this.ttsModel || !this.ttsModel.startsWith('aura-')) {
          this.ttsModel = 'aura-2-asteria-en';
        }
      }
    },

    async runTts() {
      if (!this.ttsText.trim()) return;
      this.ttsLoading = true;
      this.ttsResult = null;
      this.rightTab = 'tts';

      try {
        const res = await fetch('/api/tts-transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: this.ttsText,
            tts_model: this.ttsModel,
            tts_provider: this.ttsProvider,
            mode: this.ttsMode,
            stt_params: this.getCleanParams('batch'),
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        this.ttsResult = data;

        // Extract transcripts based on mode
        const batchSrc  = this.ttsMode === 'both' ? data.batch   : (this.ttsMode === 'batch'     ? data : null);
        const streamSrc = this.ttsMode === 'both' ? data.streaming : (this.ttsMode === 'streaming' ? data : null);

        const batchTranscript  = batchSrc  ? this.extractBatchTranscript(batchSrc)  : '';
        const streamTranscript = streamSrc ? (streamSrc.transcript || '')            : '';

        this.ttsLastText             = this.ttsText;
        this.ttsLastTranscript       = batchTranscript || streamTranscript;
        this.ttsLastStreamTranscript = streamTranscript;

        const display = batchTranscript || streamTranscript;
        if (display) {
          this.finalTranscript += this.escapeHtml(display) + '\n';
        }
        this.addResponse('final', data);
        this.rightTab = 'transcript';
        this.showToast('TTS transcription complete', 'success');
      } catch (err) {
        this.showToast('TTS error: ' + err.message, 'error');
        this.ttsResult = { error: err.message };
      } finally {
        this.ttsLoading = false;
      }
    },

    extractBatchTranscript(data) {
      try {
        return data?.results?.channels?.[0]?.alternatives?.[0]?.transcript || '';
      } catch {
        return '';
      }
    },

    // Word-level LCS diff between two strings.
    // Returns array of ops: {type:'equal'|'delete'|'insert'|'replace', ...}
    _wordDiff(original, transcribed) {
      const norm = s => s.toLowerCase().replace(/^['"]+|[.,!?;:'"]+$/g, '');
      const a = original.trim().split(/\s+/).filter(Boolean);
      const b = transcribed.trim().split(/\s+/).filter(Boolean);
      const m = a.length, n = b.length;

      // Build LCS table
      const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
      for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
          dp[i][j] = norm(a[i-1]) === norm(b[j-1])
            ? dp[i-1][j-1] + 1
            : Math.max(dp[i-1][j], dp[i][j-1]);
        }
      }

      // Backtrack
      const ops = [];
      let i = m, j = n;
      while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && norm(a[i-1]) === norm(b[j-1])) {
          ops.unshift({ type: 'equal', word: a[i-1] });
          i--; j--;
        } else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) {
          ops.unshift({ type: 'insert', word: b[j-1] });
          j--;
        } else {
          ops.unshift({ type: 'delete', word: a[i-1] });
          i--;
        }
      }

      // Merge adjacent delete+insert pairs into replace
      const merged = [];
      for (let k = 0; k < ops.length; k++) {
        if (k + 1 < ops.length && ops[k].type === 'delete' && ops[k+1].type === 'insert') {
          merged.push({ type: 'replace', old: ops[k].word, new: ops[k+1].word });
          k++;
        } else {
          merged.push(ops[k]);
        }
      }
      return merged;
    },

    renderTtsDiff(original, transcribed) {
      if (!original && !transcribed) return '';
      const e = s => this.escapeHtml(s);
      const ops = this._wordDiff(original, transcribed);
      const parts = ops.map(op => {
        if (op.type === 'equal')   return `<span class="diff-equal">${e(op.word)}</span>`;
        if (op.type === 'delete')  return `<span class="diff-delete">${e(op.word)}</span>`;
        if (op.type === 'insert')  return `<span class="diff-insert">${e(op.word)}</span>`;
        if (op.type === 'replace') return `<span class="diff-delete">${e(op.old)}</span><span class="diff-arrow">→</span><span class="diff-insert">${e(op.new)}</span>`;
      });
      return parts.join(' ');
    },

    // ---- URL computation ----
    refreshUrl() {
      const base = this.params.base_url || 'api.deepgram.com';

      if (this.mode === 'tts') {
        if (this.ttsProvider === 'elevenlabs') {
          const voiceId = this.ttsModel || '(select voice)';
          this.urlDisplay = `api.elevenlabs.io/v1/text-to-speech/${voiceId}`;
          return `https://${this.urlDisplay}`;
        }
        const ttsModel = this.ttsModel || 'aura-2-asteria-en';
        this.urlDisplay = `${base}/v1/speak?model=${ttsModel}&encoding=mp3`;
        return `https://${this.urlDisplay}`;
      }

      const isStreaming = this.mode !== 'batch';
      const scheme = isStreaming ? 'wss' : 'https';

      const qp = this.buildQueryParams(isStreaming);
      const query = qp.length ? '?' + qp : '';
      const full = `${scheme}://${base}/v1/listen${query}`;

      // Store without scheme prefix since scheme is shown separately in URL bar
      this.urlDisplay = `${base}/v1/listen${query}`;
      return full;
    },

    buildQueryParams(isStreaming) {
      const p = this.params;

      // Streaming-only: excluded from batch
      const streamingOnly = ['interim_results', 'vad_events', 'endpointing', 'utterance_end_ms', 'no_delay'];
      // Batch-only: excluded from streaming
      const batchOnly = ['paragraphs', 'topics', 'intents', 'sentiment', 'utterances'];

      const parts = [];

      const skip = ['base_url', 'extra_json', 'redact', 'keyterms'];
      if (isStreaming) skip.push(...batchOnly);
      else skip.push(...streamingOnly);

      for (const [key, val] of Object.entries(p)) {
        if (skip.includes(key)) continue;
        if (key === 'redact' || key === 'keyterms') continue;
        if (val === '' || val === false || val === 0 || val === null || val === undefined) continue;
        if (key === 'alternatives' && val === 0) continue;
        parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(val)}`);
      }

      // Array params: redact
      for (const v of (p.redact || [])) {
        parts.push(`redact=${encodeURIComponent(v)}`);
      }

      // Keyterms
      for (const term of (p.keyterms || [])) {
        if (term.trim()) parts.push(`keyterm=${encodeURIComponent(term.trim())}`);
      }

      // extra_json merge
      if (p.extra_json && p.extra_json.trim()) {
        try {
          const extra = JSON.parse(p.extra_json);
          for (const [k, v] of Object.entries(extra)) {
            if (Array.isArray(v)) {
              for (const item of v) parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(item)}`);
            } else {
              parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
            }
          }
        } catch {
          // Invalid JSON, skip
        }
      }

      return parts.join('&');
    },

    // ---- URL parsing (two-way binding) ----
    parseUrlInput() {
      const raw = this.urlDisplay.trim();
      if (!raw) return;

      // Reconstruct full URL for parsing
      const withScheme = raw.startsWith('ws') || raw.startsWith('http') ? raw : 'wss://' + raw;

      try {
        const url = new URL(withScheme);
        this.params.base_url = url.hostname;

        const known = Object.keys(this.params);
        const arrayParams = ['redact', 'keyterm'];
        const unknownPairs = {};

        // Reset array params before re-parse
        this.params.redact = [];
        const newKeyterms = [];

        for (const [k, v] of url.searchParams.entries()) {
          if (k === 'redact') {
            this.params.redact.push(v);
          } else if (k === 'keyterm') {
            newKeyterms.push(v);
          } else if (known.includes(k)) {
            // Coerce type
            const existing = this.params[k];
            if (typeof existing === 'boolean') {
              this.params[k] = v === 'true' || v === '1';
            } else if (typeof existing === 'number') {
              const n = parseFloat(v);
              this.params[k] = isNaN(n) ? existing : n;
            } else {
              this.params[k] = v;
            }
          } else {
            if (!unknownPairs[k]) unknownPairs[k] = [];
            unknownPairs[k].push(v);
          }
        }

        if (newKeyterms.length) this.params.keyterms = newKeyterms;

        // Put unknown params into extra_json
        if (Object.keys(unknownPairs).length) {
          const flat = {};
          for (const [k, vals] of Object.entries(unknownPairs)) {
            flat[k] = vals.length === 1 ? vals[0] : vals;
          }
          this.params.extra_json = JSON.stringify(flat, null, 2);
        }
      } catch {
        // Invalid URL, ignore
      }
    },

    copyUrl() {
      const isStreaming = this.mode !== 'batch';
      const scheme = isStreaming ? 'wss' : 'https';
      const full = scheme + '://' + this.urlDisplay;
      navigator.clipboard.writeText(full).then(() => {
        this.urlCopied = true;
        setTimeout(() => { this.urlCopied = false; }, 2000);
      });
    },

    // ---- Import/Export ----
    importConfig() {
      const raw = this.importExportText.trim();
      if (!raw) return;

      if (raw.startsWith('ws') || raw.startsWith('http')) {
        // Parse as URL
        this.urlDisplay = raw.replace(/^wss?:\/\/|^https?:\/\//, '');
        this.parseUrlInput();
        this.showToast('Imported from URL', 'success');
      } else if (raw.startsWith('{')) {
        // Parse as JSON
        try {
          const obj = JSON.parse(raw);
          for (const [k, v] of Object.entries(obj)) {
            if (k in this.params) {
              this.params[k] = v;
            }
          }
          this.showToast('Imported from JSON', 'success');
        } catch {
          this.showToast('Invalid JSON', 'error');
        }
      } else {
        this.showToast('Unrecognized format. Paste a URL or JSON object.', 'error');
      }
    },

    exportConfig() {
      const clean = this.getCleanParams(this.mode === 'batch' ? 'batch' : 'streaming');
      const json = JSON.stringify(clean, null, 2);
      this.importExportText = json;
      navigator.clipboard.writeText(json).then(() => {
        this.showToast('Params copied to clipboard', 'success');
      });
    },

    resetParams() {
      this.params = {
        model: 'nova-3',
        language: 'en',
        version: '',
        base_url: 'api.deepgram.com',
        encoding: '',
        sample_rate: 0,
        channels: 0,
        endpointing: 10,
        utterance_end_ms: 1000,
        smart_format: true,
        punctuate: false,
        numerals: false,
        filler_words: false,
        dictation: false,
        profanity_filter: false,
        diarize: false,
        diarize_version: '',
        detect_entities: false,
        multichannel: false,
        utterances: false,
        paragraphs: false,
        redact: [],
        keyterms: [],
        entity_prompt: '',
        keywords: '',
        search: '',
        replace: '',
        topics: false,
        intents: false,
        sentiment: false,
        interim_results: true,
        vad_events: true,
        no_delay: false,
        callback: '',
        tags: '',
        mip_opt_out: false,
        alternatives: 0,
        word_confidence: false,
        extra_json: '',
      };
      this.showToast('Params reset to defaults', 'success');
    },

    // ---- getCleanParams ----
    getCleanParams(mode) {
      const p = this.params;
      const isStreaming = mode === 'streaming';

      const streamingOnly = ['interim_results', 'vad_events', 'endpointing', 'utterance_end_ms', 'no_delay'];
      const batchOnly = ['paragraphs', 'topics', 'intents', 'sentiment', 'utterances'];

      const out = {};

      for (const [key, val] of Object.entries(p)) {
        if (key === 'base_url' || key === 'extra_json') continue;
        if (isStreaming && batchOnly.includes(key)) continue;
        if (!isStreaming && streamingOnly.includes(key)) continue;

        if (val === '' || val === false || val === null || val === undefined) continue;
        if (typeof val === 'number' && val === 0) continue;

        if (key === 'redact' && Array.isArray(val) && val.length === 0) continue;
        if (key === 'keyterms' && Array.isArray(val) && val.length === 0) continue;

        out[key] = val;
      }

      // Merge extra_json
      if (p.extra_json && p.extra_json.trim()) {
        try {
          const extra = JSON.parse(p.extra_json);
          Object.assign(out, extra);
        } catch {
          // Invalid JSON, skip
        }
      }

      // Clean keyterms: remove empty strings
      if (out.keyterms) {
        out.keyterms = out.keyterms.filter(t => t.trim());
        if (out.keyterms.length === 0) delete out.keyterms;
      }

      return out;
    },

    // ---- Redact helpers ----
    toggleRedact(value, checked) {
      if (checked) {
        if (!this.params.redact.includes(value)) {
          this.params.redact = [...this.params.redact, value];
        }
      } else {
        this.params.redact = this.params.redact.filter(v => v !== value);
      }
    },

    // ---- Transcript ----
    _logDebug(type, text) {
      const now = new Date();
      const time = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
        '.' + String(now.getMilliseconds()).padStart(3, '0');
      this.interimLog.push({ time, type, text });
      if (this.debugPanelOpen) {
        this.$nextTick(() => {
          const el = this.$refs.debugLog;
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    },

    clearTranscript() {
      this.finalTranscript = '';
      this.interimTranscript = '';
      this.interimLog = [];
    },

    // ---- Response explorer ----
    addResponse(type, data) {
      const now = new Date();
      const timestamp = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
        '.' + String(now.getMilliseconds()).padStart(3, '0');

      let preview = '';
      try {
        if (data.transcript !== undefined) {
          preview = data.transcript;
        } else {
          const alt = data?.results?.channels?.[0]?.alternatives?.[0];
          preview = alt?.transcript || JSON.stringify(data).slice(0, 80);
        }
      } catch {
        preview = '';
      }

      // Keep last 200 responses
      if (this.responses.length >= 200) {
        this.responses.splice(0, 1);
      }

      this.responses.push({ type, data, timestamp, preview, open: false });

      this.$nextTick(() => {
        const el = this.$refs.responsesList;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    // ---- JSON tree renderer ----
    renderJsonTree(data, depth) {
      if (depth === undefined) depth = 0;
      const indent = '  '.repeat(depth);

      if (data === null) return `<span class="jt-null">null</span>`;
      if (typeof data === 'boolean') return `<span class="jt-bool">${data}</span>`;
      if (typeof data === 'number') return `<span class="jt-num">${data}</span>`;
      if (typeof data === 'string') return `<span class="jt-str">"${this.escapeHtml(data)}"</span>`;

      if (Array.isArray(data)) {
        if (data.length === 0) return `<span class="jt-brace">[]</span>`;
        const id = 'jt_' + Math.random().toString(36).slice(2);
        const items = data.map((item, i) => {
          return `<div class="jt-line">` +
            `<span class="jt-indent"></span>` +
            this.renderJsonTree(item, depth + 1) +
            (i < data.length - 1 ? '<span class="jt-brace">,</span>' : '') +
            `</div>`;
        }).join('');
        return `<span class="jt-brace">[</span>` +
          `<span class="jt-count">${data.length} items</span>` +
          `<span class="jt-toggle" onclick="(function(el){var b=el.closest('.jt-line').nextElementSibling;if(b){b.classList.toggle('collapsed');el.textContent=b.classList.contains('collapsed')?'▶':'▼'}})(this)">▼</span>` +
          `<div class="jt-block">${items}</div>` +
          `<span class="jt-brace">]</span>`;
      }

      if (typeof data === 'object') {
        const keys = Object.keys(data);
        if (keys.length === 0) return `<span class="jt-brace">{}</span>`;
        const items = keys.map((k, i) => {
          return `<div class="jt-line">` +
            `<span class="jt-indent"></span>` +
            `<span class="jt-key">"${this.escapeHtml(String(k))}"</span>` +
            `<span class="jt-colon">:</span> ` +
            this.renderJsonTree(data[k], depth + 1) +
            (i < keys.length - 1 ? '<span class="jt-brace">,</span>' : '') +
            `</div>`;
        }).join('');
        return `<span class="jt-brace">{</span>` +
          `<span class="jt-count">${keys.length} keys</span>` +
          `<span class="jt-toggle" onclick="(function(el){var b=el.closest('.jt-line, div').nextElementSibling;if(!b){b=el.parentElement.querySelector('.jt-block')}if(b){b.classList.toggle('collapsed');el.textContent=b.classList.contains('collapsed')?'▶':'▼'}})(this)">▼</span>` +
          `<div class="jt-block">${items}</div>` +
          `<span class="jt-brace">}</span>`;
      }

      return `<span>${this.escapeHtml(String(data))}</span>`;
    },

    // ---- Toast ----
    showToast(message, type) {
      if (type === undefined) type = 'success';
      this.toast = { visible: true, message, type };
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => {
        this.toast.visible = false;
      }, 3000);
    },

    // ---- Utilities ----
    escapeHtml(str) {
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },

  };
}
