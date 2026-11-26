document.addEventListener('alpine:init', () => {
  Alpine.data('logForm', (cfg) => {
    const UNITS_KEY = 'mp_unit_suggestions_v1';
    const LISTENING_MSG = 'Listening... Speak now.';

    function readUnitsFromDom() {
      const el = document.getElementById('units-data');
      if (!el) return [];
      try {
        const v = JSON.parse(el.textContent);
        return Array.isArray(v) ? v : [];
      } catch (e) { return []; }
    }

    return {
      cfg,
      form: {
        id: null,
        date: '',
        unit_sub_department: '',
        activity_summary: '',
        skills_acquired: '',
        challenges_faced: '',
        solutions_applied: '',
      },
      listening: null,
      interimText: '',
      speechSupported: !!(window.SpeechRecognition || window.webkitSpeechRecognition),
      saving: false,
      message: '',
      messageType: 'info',
      unitSuggestions: [],
      _recognition: null,
      _recField: null,

      init() {
        const now = new Date();
        const localToday = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
        this.form.date = cfg.existing ? cfg.existing.date : localToday;

        const ex = cfg.existing;
        if (ex) {
          this.form.id = ex.id || null;
          this.form.unit_sub_department = ex.unit_sub_department || '';
          ['activity_summary', 'skills_acquired', 'challenges_faced', 'solutions_applied']
            .forEach((f) => { this.form[f] = ex[f] || ''; });
        }

        this._loadUnitSuggestions();
      },

      get weekNumber() {
        if (!this.form.date || !cfg.start_date) return '';
        const d = new Date(this.form.date + 'T00:00:00');
        const s = new Date(cfg.start_date + 'T00:00:00');
        if (isNaN(d) || isNaN(s)) return '';
        const days = Math.round((d - s) / 86400000);
        return Math.floor(days / 7) + 1;
      },

      // ---- Unit autosuggest ----
      _loadUnitSuggestions() {
        let stored = [];
        try { stored = JSON.parse(localStorage.getItem(UNITS_KEY) || '[]'); } catch (e) { stored = []; }
        if (!Array.isArray(stored)) stored = [];
        const merged = readUnitsFromDom().concat(stored);
        const seen = {};
        const out = [];
        merged.forEach((u) => {
          const s = String(u == null ? '' : u).trim();
          if (!s) return;
          const k = s.toLowerCase();
          if (seen[k]) return;
          seen[k] = true;
          out.push(s);
        });
        this.unitSuggestions = out;
      },

      rememberUnit() {
        const value = (this.form.unit_sub_department || '').trim();
        if (!value) return;
        const lower = value.toLowerCase();
        const exists = this.unitSuggestions.some((u) => u.toLowerCase() === lower);
        if (exists) return;
        this.unitSuggestions = this.unitSuggestions.concat([value]);
        try { localStorage.setItem(UNITS_KEY, JSON.stringify(this.unitSuggestions)); } catch (e) {}
      },

      _clearListeningMessage() {
        if (this.message === LISTENING_MSG) {
          this.message = '';
          this.messageType = 'info';
        }
      },

      // ---- Recording (fresh instance, synchronous reset, stale-event guards) ----
      dictate(field) {
        if (!this.speechSupported) {
          this.setMessage('Voice dictation is not supported in this browser. Use Chrome or Edge.', 'error');
          return;
        }
        if (this.listening === field) {
          this.stopDictation();
          return;
        }
        if (this._recognition) this.stopDictation();
        this.startDictation(field);
      },

      startDictation(field) {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        const rec = new SR();
        rec.lang = 'en-US';
        rec.continuous = true;
        rec.interimResults = true;

        this._recognition = rec;
        this._recField = field;
        this.listening = field;     // flip UI synchronously — don't wait for onstart
        this.interimText = '';

        rec.onstart = () => {
          if (this._recognition !== rec) return;
          this.listening = field;
          this.setMessage(LISTENING_MSG, 'success');
        };

        rec.onresult = (e) => {
          if (this._recognition !== rec) return;
          let interim = '';
          let final = '';
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const t = e.results[i][0].transcript;
            if (e.results[i].isFinal) final += t + ' ';
            else interim += t;
          }
          if (final) {
            const clean = final.trim();
            if (clean) {
              const cur = this.form[field] || '';
              this.form[field] = cur ? (cur.replace(/\s+$/, '') + ' ' + clean) : clean;
            }
          }
          this.interimText = interim;
        };

        rec.onend = () => {
          if (this._recognition !== rec) return; // stale instance → ignore
          this._recognition = null;
          this._recField = null;
          this.listening = null;
          this.interimText = '';
          this._clearListeningMessage();
        };

        rec.onerror = (e) => {
          if (this._recognition !== rec) return;
          if (e.error === 'aborted' || e.error === 'no-speech') return;
          this._recognition = null;
          this._recField = null;
          this.listening = null;
          this.interimText = '';
          if (e.error === 'not-allowed') {
            this.setMessage('Microphone blocked by browser or Windows. Please check permissions.', 'error');
          } else if (e.error === 'network') {
            this.setMessage('Dictation needs an internet connection.', 'error');
          } else {
            this.setMessage('Mic error: ' + e.error, 'error');
          }
        };

        try {
          rec.start();
        } catch (err) {
          this._recognition = null;
          this._recField = null;
          this.listening = null;
          this.setMessage('Could not start mic. Wait a second and try again.', 'error');
        }
      },

      stopDictation() {
        const rec = this._recognition;
        // Synchronous reset — button flips instantly, next click can start fresh
        this._recognition = null;
        this._recField = null;
        this.listening = null;
        this.interimText = '';
        this._clearListeningMessage();
        if (rec) {
          try { rec.onstart = null; rec.onresult = null; rec.onend = null; rec.onerror = null; } catch (e) {}
          try { rec.stop(); } catch (e) { try { rec.abort(); } catch (e2) {} }
        }
      },

      validate() {
        if (!this.form.date) return 'Pick a date.';
        if (!this.form.unit_sub_department.trim()) return 'Choose or type the unit you worked in.';
        if (!this.form.activity_summary.trim()) return 'Describe what you did today.';
        return '';
      },

      buildPayload() {
        return {
          id: this.form.id,
          attachment_id: cfg.attachment_id,
          date: this.form.date,
          unit_sub_department: this.form.unit_sub_department.trim(),
          activity_summary: this.form.activity_summary.trim(),
          skills_acquired: this.form.skills_acquired.trim(),
          challenges_faced: this.form.challenges_faced.trim(),
          solutions_applied: this.form.solutions_applied.trim(),
          tags: [],
          numeric_tallies: {},
          department_details: {},
        };
      },

      async queueOffline(payload, authProblem) {
        if (!window.MPOffline || typeof window.MPOffline.queueLog !== 'function') {
          this.setMessage('Offline sync not available. Please reload the page and try again.', 'error');
          return;
        }
        await window.MPOffline.queueLog(payload);
        this.setMessage(
          authProblem ? 'Session expired. Saved on this device; log in to sync.' : 'Saved offline. Will sync when online.',
          'success'
        );
      },

      async submit() {
        const problem = this.validate();
        if (problem) { this.setMessage(problem, 'error'); return; }

        if (!window.MPOffline || typeof window.MPOffline.sendLogs !== 'function') {
          this.setMessage('Sync module not loaded. Reload the page and try again.', 'error');
          return;
        }

        const payload = this.buildPayload();
        this.saving = true;
        try {
          if (!navigator.onLine) { await this.queueOffline(payload, false); return; }

          let result;
          try {
            result = await window.MPOffline.sendLogs([payload]);
          } catch (err) {
            await this.queueOffline(payload, err.code === 'auth');
            return;
          }

          if (result.errors && result.errors.length) {
            this.setMessage(result.errors[0].error, 'error');
            return;
          }
          this.setMessage('Saved ✓', 'success');
          setTimeout(() => { window.location.href = cfg.dashboard_url; }, 700);
        } catch (e) {
          this.setMessage('Could not save: ' + (e && e.message ? e.message : e), 'error');
        } finally {
          this.saving = false;
        }
      },

      setMessage(text, type) {
        this.message = text;
        this.messageType = type;
      },
    };
  });
});