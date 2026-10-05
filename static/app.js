// Alpine.js component for Deepgram STT Explorer

function appData() {
  return {

    // ---- State ----
    // API access token. The UI shell is public; every call that spends the
    // Deepgram key carries this. Read from ?token= so a shared link works in
    // one click, then kept in sessionStorage so it survives navigation without
    // persisting to disk.
    apiToken: '',
    // Access tier, reported by the server on connect. Anonymous visitors get a
    // working app under limits; a token lifts them. Rendered as a banner so a
    // visitor who hits a limit sees why instead of a silently broken page.
    tier: { known: false, privileged: false, maxStreamSeconds: null, maxTtsChars: null, passwordEnabled: false },
    limitNotice: '',       // set when a limit is actually hit
    // Unlock box: a password (or the token) typed into the banner instead of
    // pasted into a ?token= URL. authError is the server's verdict on the last
    // credential tried, shown beside the box.
    unlockInput: '',
    authError: '',
    // True from stream_started until stream_finished/stream_error/disconnect.
    // Unlock reconnects the socket, and the server cancels a stream on
    // disconnect, so a stream that is stopping but not finished still counts.
    streamActive: false,

    mode: 'mic',         // 'mic' | 'file' | 'batch' | 'tts'
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
    // ElevenLabs voices cache (keyed by language code)
    elevenVoicesCache: {},
    elevenVoices: [],
    elevenVoicesLoading: false,
    // Map Deepgram language codes to ElevenLabs language label prefixes
    elevenLangMap: {
      en: 'English',
      es: 'Spanish',
      fr: 'French',
      de: 'German',
      it: 'Italian',
      nl: 'Dutch',
      ja: 'Japanese',
    },

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
    // Parameter gating rules, fetched from /api/param-gating so stt/options.py
    // stays the only place they are written down. These defaults are EMPTY on
    // purpose: they are not a second copy waiting to drift, they are the
    // fail-open state for the moment before the fetch lands. The server gates
    // authoritatively either way, so the worst case is the URL preview briefly
    // showing a param the server will strip.
    gatingLoaded: false,
    gating: {
      streaming_only: [],
      batch_only: [],
      internal: [],
      denied: [],
      aliases: {},
      repeatable: [],
      zero_means_unset: [],
      flux: {
        params: [],
        multilingual_only: [],
        multilingual_model: 'flux-general-multi',
        ranges: {},
        redact_values: [],
      },
    },

    sections: {
      core: true,
      flux: false,
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
      // Groups
      { value: 'pii', label: 'PII (group)', group: 'Groups' },
      { value: 'phi', label: 'PHI (group)', group: 'Groups' },
      { value: 'pci', label: 'PCI (group)', group: 'Groups' },
      { value: 'numbers', label: 'Numbers (3+ digits)', group: 'Groups' },
      { value: 'aggressive_numbers', label: 'Aggressive Numbers', group: 'Groups' },
      { value: 'pin', label: 'PIN', group: 'Groups' },
      // PII — Identity
      { value: 'name', label: 'Name', group: 'PII' },
      { value: 'name_given', label: 'Given Name', group: 'PII' },
      { value: 'name_family', label: 'Family Name', group: 'PII' },
      { value: 'name_medical_professional', label: 'Medical Professional Name', group: 'PII' },
      { value: 'dob', label: 'Date of Birth', group: 'PII' },
      { value: 'age', label: 'Age', group: 'PII' },
      { value: 'gender_sexuality', label: 'Gender/Sexuality', group: 'PII' },
      { value: 'origin', label: 'Origin', group: 'PII' },
      { value: 'occupation', label: 'Occupation', group: 'PII' },
      { value: 'physical_attribute', label: 'Physical Attribute', group: 'PII' },
      { value: 'username', label: 'Username', group: 'PII' },
      { value: 'password', label: 'Password', group: 'PII' },
      // PII — Financial
      { value: 'credit_card', label: 'Credit Card', group: 'PII' },
      { value: 'credit_card_expiration', label: 'CC Expiration', group: 'PII' },
      { value: 'cvv', label: 'CVV', group: 'PII' },
      { value: 'account_number', label: 'Account Number', group: 'PII' },
      { value: 'bank_account', label: 'Bank Account', group: 'PII' },
      { value: 'routing_number', label: 'Routing Number', group: 'PII' },
      { value: 'money', label: 'Money', group: 'PII' },
      // PII — Government IDs
      { value: 'ssn', label: 'SSN', group: 'PII' },
      { value: 'driver_license', label: 'Driver License', group: 'PII' },
      { value: 'passport_number', label: 'Passport Number', group: 'PII' },
      { value: 'healthcare_number', label: 'Healthcare Number', group: 'PII' },
      { value: 'vehicle_id', label: 'Vehicle ID', group: 'PII' },
      // PII — Contact & Location
      { value: 'email_address', label: 'Email Address', group: 'PII' },
      { value: 'phone_number', label: 'Phone Number', group: 'PII' },
      { value: 'ip_address', label: 'IP Address', group: 'PII' },
      { value: 'url', label: 'URL', group: 'PII' },
      { value: 'location', label: 'Location', group: 'PII' },
      { value: 'location_address', label: 'Address', group: 'PII' },
      { value: 'location_city', label: 'City', group: 'PII' },
      { value: 'location_state', label: 'State', group: 'PII' },
      { value: 'location_country', label: 'Country', group: 'PII' },
      { value: 'location_zip', label: 'ZIP Code', group: 'PII' },
      { value: 'location_coordinate', label: 'Coordinate', group: 'PII' },
      // PII — Numbers & Dates
      { value: 'numerical_pii', label: 'Numerical PII', group: 'PII' },
      { value: 'cardinal', label: 'Cardinal Number', group: 'PII' },
      { value: 'ordinal', label: 'Ordinal Number', group: 'PII' },
      { value: 'percent', label: 'Percent', group: 'PII' },
      { value: 'date', label: 'Date', group: 'PII' },
      { value: 'date_interval', label: 'Date Interval', group: 'PII' },
      { value: 'time', label: 'Time', group: 'PII' },
      // PII — Other
      { value: 'event', label: 'Event', group: 'PII' },
      { value: 'filename', label: 'Filename', group: 'PII' },
      { value: 'organization', label: 'Organization', group: 'Other' },
      { value: 'language', label: 'Language', group: 'Other' },
      { value: 'marital_status', label: 'Marital Status', group: 'Other' },
      { value: 'political_affiliation', label: 'Political Affiliation', group: 'Other' },
      { value: 'religion', label: 'Religion', group: 'Other' },
      { value: 'zodiac_sign', label: 'Zodiac Sign', group: 'Other' },
      // PHI
      { value: 'condition', label: 'Condition', group: 'PHI' },
      { value: 'drug', label: 'Drug', group: 'PHI' },
      { value: 'injury', label: 'Injury', group: 'PHI' },
      { value: 'blood_type', label: 'Blood Type', group: 'PHI' },
      { value: 'medical_process', label: 'Medical Process', group: 'PHI' },
      { value: 'statistics', label: 'Statistics', group: 'PHI' },
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
      diarize_model: '',
      detect_entities: false,
      multichannel: false,
      utterances: false,
      paragraphs: false,
      redact: [],
      keyterms: [],
      entity_prompt: '',
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
      // Flux (/v2/listen) end-of-turn detection. Strings, not numbers, because
      // '' is the only way to express "leave it at Deepgram's default" — 0 is
      // out of range for all three and would be sent as a real value.
      eot_threshold: '',
      eager_eot_threshold: '',
      eot_timeout_ms: '',
      language_hint: '',
      extra_json: '',
    },

    // ---- Init ----
    init() {
      // MUST run before setupSocket(): the token goes in the SocketIO
      // handshake, and a socket opened without it is refused at connect.
      this._loadToken();
      this.setupSocket();
      this._loadGating();

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

    // ---- Parameter gating ----
    async _loadGating() {
      try {
        const resp = await fetch('/api/param-gating');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        this.gating = await resp.json();
        this.gatingLoaded = true;
        this.refreshUrl();
      } catch (err) {
        // Fail open rather than fall back to a hardcoded copy: the server
        // applies the real gate, and a stale duplicate here is the bug this
        // endpoint exists to remove.
        console.warn('[DG] param gating unavailable, URL preview may show params the server strips:', err);
      }
    },

    // Should the panel offer this control at all? False when the server would
    // strip it anyway — wrong mode, wrong endpoint, or on the deny list.
    //
    // A control that cannot affect the request is worse than a missing one: it
    // reads as evidence that the feature was tested. `callback` was the clearest
    // case, a field the server has always refused to forward because it is a
    // data-exfil primitive, sitting in the panel looking functional.
    showParam(key) {
      return !this._isDropped(key, this.mode === 'batch' ? 'batch' : 'streaming');
    },

    // Which gated params live in which accordion. Layout, not gating rules —
    // the rules themselves come from /api/param-gating and are not duplicated
    // here. This exists so a section whose every control is hidden collapses
    // instead of rendering a header over nothing, which is what "Features"
    // did on Flux.
    sectionParams: {
      core: ['model'],
      audio: ['sample_rate', 'channels', 'encoding', 'multichannel'],
      formatting: ['smart_format', 'punctuate', 'numerals', 'filler_words',
                   'dictation', 'profanity_filter', 'word_confidence'],
      features: ['diarize', 'diarize_model', 'detect_entities', 'utterances',
                 'paragraphs'],
      redaction: ['redact'],
      prompting: ['keyterms', 'entity_prompt', 'search', 'replace'],
      intelligence: ['topics', 'intents', 'sentiment'],
      streaming: ['interim_results', 'vad_events', 'endpointing',
                  'utterance_end_ms', 'no_delay'],
      advanced: ['version', 'alternatives', 'tags', 'mip_opt_out'],
    },

    // Redaction groups that still have at least one selectable option. On Flux
    // only the two number types survive, so PII/PHI/Other would otherwise render
    // as bare headings over nothing.
    redactGroups() {
      return ['Groups', 'PII', 'PHI', 'Other'].filter(
        g => this.redactOptions.some(o => o.group === g && this.redactAllowed(o.value))
      );
    },

    sectionVisible(name) {
      const keys = this.sectionParams[name];
      if (!keys) return true;
      return keys.some(k => this.showParam(k));
    },

    // Flux redacts numbers and nothing else — `redact=pci` is valid on v1 and a
    // 400 at the v2 handshake. Offering the other thirty types on Flux is
    // offering a broken stream.
    redactAllowed(value) {
      if (!this.isFlux) return true;
      const allowed = this.gating.flux.redact_values || [];
      return !allowed.length || allowed.includes(value);
    },

    // Flux is a different endpoint (/v2/listen), not another model on v1, so
    // this drives the URL, the params panel, and what gets sent.
    get isFlux() {
      return (this.params.model || '').startsWith('flux-');
    },

    get isFluxMultilingual() {
      return this.params.model === this.gating.flux.multilingual_model;
    },

    // Mirrors stt/options.py flux_validation_error: Deepgram rejects all of
    // these with "Unexpected error when initializing websocket connection",
    // which names nothing, so the panel says which knob is wrong before the
    // request is ever made.
    get fluxWarning() {
      if (!this.isFlux) return '';
      const p = this.params;
      for (const [name, [low, high]] of Object.entries(this.gating.flux.ranges || {})) {
        const raw = p[name];
        if (raw === '' || raw === null || raw === undefined) continue;
        const value = Number(raw);
        if (Number.isNaN(value)) return `${name} must be a number between ${low} and ${high}.`;
        if (value < low || value > high) return `${name} must be between ${low} and ${high} (got ${raw}).`;
      }
      if (p.eager_eot_threshold !== '' && p.eot_threshold !== '' &&
          Number(p.eager_eot_threshold) > Number(p.eot_threshold)) {
        return `eager_eot_threshold (${p.eager_eot_threshold}) must be less than or equal to eot_threshold (${p.eot_threshold}).`;
      }
      const allowed = this.gating.flux.redact_values || [];
      const badRedact = (p.redact || []).filter(v => allowed.length && !allowed.includes(v));
      if (badRedact.length) {
        return `Flux only redacts numbers. Unset ${badRedact.join(', ')}.`;
      }
      if (!this.isFluxMultilingual) {
        for (const name of (this.gating.flux.multilingual_only || [])) {
          if (p[name]) return `${name} requires model=${this.gating.flux.multilingual_model}.`;
        }
      }
      return '';
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
    _loadToken() {
      const fromUrl = new URLSearchParams(window.location.search).get('token');
      if (fromUrl) {
        this._setToken(fromUrl);
        // Drop the token out of the visible URL so it does not end up in a
        // screenshot or a copied link by accident. sessionStorage still has it.
        const url = new URL(window.location.href);
        url.searchParams.delete('token');
        window.history.replaceState({}, '', url);
      } else {
        this.apiToken = (sessionStorage.getItem('sttApiToken') || '').trim();
      }
    },

    // The ONE writer of the credential. socket.auth is updated here too: it is
    // what socket.io resends on every automatic reconnect, so a copy left there
    // would replay a rejected password and burn failure budget on its own.
    _setToken(value) {
      this.apiToken = (value || '').trim();
      if (this.apiToken) sessionStorage.setItem('sttApiToken', this.apiToken);
      else sessionStorage.removeItem('sttApiToken');
      if (this.socket) this.socket.auth = { token: this.apiToken };
    },

    // A credential the server rejected (wrong, or password checks paused) is
    // forgotten and the page drops back to the anonymous tier and its unlock box.
    // fromHttp: the verdict came on an HTTP response, so this.tier still holds
    // the old tier's limits. The anonymous ones only arrive in access_tier, so
    // re-handshake to fetch them, unless that would cancel a live stream; until
    // then the banner hides the limits sentence rather than print stale ones.
    _rejectCredential(reason, fromHttp) {
      this.authError = reason;
      this._setToken('');
      if (!fromHttp) return;
      this.tier = { ...this.tier, privileged: false, maxStreamSeconds: null, maxTtsChars: null };
      if (this.socket && !this.streamBusy) {
        this.socket.disconnect();
        this.socket.connect();
      }
    },

    get streamBusy() {
      return this.recording || this.streamActive || this.fileStreamState === 'streaming';
    },

    // Re-handshake with the typed credential. The tier is resolved at connect,
    // so a reconnect is the only way the socket picks it up; access_tier then
    // reports success or why not.
    unlock() {
      const value = this.unlockInput.trim();
      if (!value || this.streamBusy) return;
      this._setToken(value);
      this.unlockInput = '';
      this.authError = '';
      this.socket.disconnect();
      this.socket.connect();
    },

    _authHeaders(extra) {
      const headers = Object.assign({}, extra || {});
      if (this.apiToken) headers['X-App-Token'] = this.apiToken;
      return headers;
    },

    // Append the token to a URL for contexts that cannot send headers, i.e. an
    // <audio src>. Header-based auth is preferred everywhere else.
    _authUrl(path) {
      if (!this.apiToken) return path;
      const sep = path.includes('?') ? '&' : '?';
      return `${path}${sep}token=${encodeURIComponent(this.apiToken)}`;
    },

    async _authedFetch(path, options) {
      const opts = Object.assign({}, options || {});
      opts.headers = this._authHeaders(opts.headers);
      const res = await fetch(path, opts);
      const authError = res.headers.get('X-Auth-Error');
      if (authError && this.apiToken) this._rejectCredential(authError, true);
      if (res.status === 401 || res.status === 429) {
        let detail = '';
        try { detail = (await res.clone().json()).detail || ''; } catch (e) { /* non-JSON body */ }
        this.limitNotice = detail || 'This instance requires an access token.';
        this.showToast(this.limitNotice, 'error');
      }
      return res;
    },

    setupSocket() {
      this.socket = io(window.location.origin, {
        transports: ['websocket', 'polling'],
        auth: { token: this.apiToken },
      });

      this.socket.on('connect_error', (err) => {
        this.connected = false;
        const msg = String((err && err.message) || '');
        // A refused connect never emits access_tier. Mark the tier known and
        // anonymous anyway, or with ANON_ACCESS off the unlock box (the only
        // way in) would never render.
        this.tier.known = true;
        this.tier.privileged = false;
        // The server refuses a connect for a reason worth showing verbatim:
        // token required, or the demo's concurrent-stream cap.
        if (msg) {
          this.limitNotice = msg;
          this.showToast(msg, 'error');
        }
      });

      this.socket.on('access_tier', (data) => {
        this.tier = {
          known: true,
          privileged: !!data.privileged,
          maxStreamSeconds: data.max_stream_seconds,
          maxTtsChars: data.max_tts_chars,
          passwordEnabled: !!data.password_enabled,
        };
        // An anonymous verdict with no error keeps the last message: it is
        // the re-handshake that follows a rejection, and erasing "wrong
        // password" there would hide why the page relocked.
        if (this.tier.privileged) {
          this.limitNotice = '';
          this.authError = '';
        } else if (data.auth_error) {
          // Every HTTP call carries the credential, and each one would count
          // as another wrong guess toward the lockout.
          this._rejectCredential(data.auth_error);
        }
      });

      this.socket.on('stream_limit_reached', (data) => {
        this.limitNotice = data.reason;
        this.showToast(data.reason, 'error');
        this.recording = false;
      });

      this.socket.on('connect', () => {
        this.connected = true;
      });

      this.socket.on('disconnect', () => {
        this.connected = false;
        this.recording = false;
        this.streamActive = false;
        if (this.fileStreamState === 'streaming') this.fileStreamState = 'idle';
      });

      this.socket.on('transcription_update', (data) => {
        this._applyTranscriptUpdate(data);
      });

      this.socket.on('stream_started', (data) => {
        this.streamActive = true;
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
        this.streamActive = false;
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
        this.streamActive = false;
        this.fileStreamState = 'error';
        this.recording = false;
        this.showToast(data.message || 'Stream error', 'error');
        const msg = data.message || 'Stream error';
        this.responses.push({ type: 'error', data: { message: msg }, timestamp: new Date().toLocaleTimeString(), preview: msg, open: true });
        this.rightTab = 'responses';
        this.$nextTick(() => { const el = this.$refs.responsesList; if (el) el.scrollTop = el.scrollHeight; });
      });

      // Not an error: the request succeeded, but something about the result
      // needs explaining. Today that is Flux ending mid-turn, where the
      // transcript legitimately stops short of the audio.
      this.socket.on('stream_notice', (data) => {
        this.showToast(data.summary || data.message, 'warning');
        this.responses.push({
          type: 'notice',
          data,
          timestamp: new Date().toLocaleTimeString(),
          preview: data.message,
          open: true,
        });
        if (data.unfinalized_transcript) {
          this._logDebug('unfinalized', data.unfinalized_transcript);
        }
        this.$nextTick(() => {
          const el = this.$refs.responsesList;
          if (el) el.scrollTop = el.scrollHeight;
        });
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
        const res = await this._authedFetch('/upload', { method: 'POST', body: formData });
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
      this._fileAudio = new Audio(this._authUrl(`/files/${encodeURIComponent(this.uploadedFile.serverName)}`));
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
        const res = await this._authedFetch('/transcribe', {
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

    filteredElevenVoices() {
      // Voices are already fetched per-language from the API.
      // Client-side filter catches user's own voices that may not match.
      const label = this.elevenLangMap[this.ttsLang] || '';
      if (!label) return this.elevenVoices;
      // Keep shared voices (already language-matched) + user voices matching language
      return this.elevenVoices.filter(v =>
        v.source === 'shared' ||
        !v.language ||
        v.language.toLowerCase().startsWith(label.toLowerCase())
      );
    },

    switchTtsLang(lang) {
      this.ttsLang = lang;
      // Keep STT language in sync so we don't transcribe with the wrong language
      this.params.language = lang;
      if (this.ttsProvider === 'elevenlabs') {
        this.loadElevenVoices(lang).then(() => {
          const voices = this.filteredElevenVoices();
          if (voices.length && !voices.find(v => v.voice_id === this.ttsModel)) {
            this.ttsModel = voices[0].voice_id;
          }
        });
      } else {
        const voices = this.dgVoices.filter(v => v.lang === lang);
        if (voices.length && !voices.find(v => v.id === this.ttsModel)) {
          this.ttsModel = voices[0].id;
        }
      }
    },

    async loadElevenVoices(lang) {
      lang = lang || this.ttsLang;
      if (this.elevenVoicesCache[lang]) {
        this.elevenVoices = this.elevenVoicesCache[lang];
        return;
      }
      this.elevenVoicesLoading = true;
      try {
        const langCode = this.elevenLangMap[lang] ? lang : '';
        const url = `/api/tts-voices?provider=elevenlabs${langCode ? '&language=' + langCode : ''}`;
        const res = await this._authedFetch(url);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        this.elevenVoices = data.voices || [];
        this.elevenVoicesCache[lang] = this.elevenVoices;
      } catch (err) {
        this.showToast('Failed to load ElevenLabs voices: ' + err.message, 'error');
      } finally {
        this.elevenVoicesLoading = false;
      }
    },

    switchTtsProvider(provider) {
      this.ttsProvider = provider;
      if (provider === 'elevenlabs') {
        this.loadElevenVoices(this.ttsLang).then(() => {
          const filtered = this.filteredElevenVoices();
          this.ttsModel = filtered.length ? filtered[0].voice_id : '';
        });
      } else {
        const voices = this.dgVoices.filter(v => v.lang === this.ttsLang);
        this.ttsModel = voices.length ? voices[0].id : 'aura-2-asteria-en';
      }
    },

    async runTts() {
      if (!this.ttsText.trim()) return;
      this.ttsLoading = true;
      this.ttsResult = null;
      this.rightTab = 'tts';

      try {
        const res = await this._authedFetch('/api/tts-transcribe', {
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
      // Flux lives on /v2/listen. Showing v1 here would hand someone a URL that
      // 400s with V2_MODEL_ON_V1_LISTEN_ENDPOINT the moment they curl it.
      const path = this.isFlux ? '/v2/listen' : '/v1/listen';

      const qp = this.buildQueryParams(isStreaming);
      const query = qp.length ? '?' + qp : '';

      // Store without scheme prefix since scheme is shown separately in URL bar
      this.urlDisplay = `${base}${path}${query}`;
      return `${scheme}://${this.urlDisplay}`;
    },

    buildQueryParams(isStreaming) {
      const p = this.params;
      const g = this.gating;
      const mode = isStreaming ? 'streaming' : 'batch';
      const parts = [];

      // The URL bar is meant to be copied into curl, so it has to spell the
      // params the way the wire does: the UI's plural `keyterms` and `tags` are
      // internal names, and Deepgram's are the singular ones.
      const push = (key, val) => {
        const wire = g.aliases[key] || key;
        parts.push(`${encodeURIComponent(wire)}=${encodeURIComponent(val)}`);
      };

      const isEmpty = (key, val) => {
        if (val === '' || val === false || val === null || val === undefined) return true;
        // 0 is a real value for most numeric params (endpointing=0 disables
        // endpointing); it means "unset" only for the few the server says so.
        return val === 0 && g.zero_means_unset.includes(g.aliases[key] || key);
      };

      for (const [key, val] of Object.entries(p)) {
        if (key === 'base_url' || key === 'extra_json') continue;
        if (Array.isArray(val)) continue;  // repeated below, one part per value
        if (this._isDropped(key, mode)) continue;
        if (isEmpty(key, val)) continue;
        push(key, val);
      }

      for (const key of ['redact', 'keyterms']) {
        if (this._isDropped(key, mode)) continue;
        for (const v of (p[key] || [])) {
          const term = String(v).trim();
          if (term) push(key, term);
        }
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
        diarize_model: '',
        detect_entities: false,
        multichannel: false,
        utterances: false,
        paragraphs: false,
        redact: [],
        keyterms: [],
        entity_prompt: '',
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
    // True when a param would be dropped before it reaches Deepgram, by the
    // same rules stt/options.py applies. Used by both getCleanParams and the
    // URL preview so the two cannot disagree about what is being sent.
    _isDropped(key, mode) {
      // Before the fetch lands every list is empty, and an empty Flux allowlist
      // would mean "drop everything" — the panel would render with no controls
      // and the URL bar with no params. Not gating until the rules are known is
      // the only safe reading of "we do not know yet"; the server gates for real
      // regardless.
      if (!this.gatingLoaded) return false;
      const g = this.gating;
      const wire = g.aliases[key] || key;
      if (g.internal.includes(wire) || g.denied.includes(wire)) return true;
      if (this.isFlux) return !g.flux.params.includes(wire);
      if (mode === 'streaming') return g.batch_only.includes(wire);
      return g.streaming_only.includes(wire);
    },

    getCleanParams(mode) {
      const p = this.params;

      const out = {};

      for (const [key, val] of Object.entries(p)) {
        if (key === 'base_url' || key === 'extra_json') continue;
        if (this._isDropped(key, mode)) continue;

        if (val === '' || val === false || val === null || val === undefined) continue;
        // Same rule as the URL preview: 0 only means "unset" for the params the
        // server nominates. endpointing=0 is a real setting, not a blank field.
        if (val === 0 && this.gating.zero_means_unset.includes(this.gating.aliases[key] || key)) continue;

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
