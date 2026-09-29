document.addEventListener('alpine:init', () => {
  Alpine.data('logForm', (cfg) => {
    let recognition = null; // kept outside Alpine so the browser object isn't proxied

    return {
      cfg,
      form: {
        date: '',
        unit_sub_department: '',
        activity_summary: '',
        skills_acquired: '',
        challenges_faced: '',
        solutions_applied: '',
        tags: [],
        tallies: {},
        details: {},
      },
      tagInput: '',
      listening: null,
      speechSupported: !!(window.SpeechRecognition || window.webkitSpeechRecognition),
      saving: false,
      message: '',
      messageType: 'info',

      init() {
        cfg.tallies.forEach((t) => { this.form.tallies[t.key] = ''; });
        cfg.details.forEach((d) => { this.form.details[d.key] = ''; });

        const now = new Date();
        const localToday = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
        let date = cfg.existing ? cfg.existing.date : localToday;
        if (date < cfg.start_date) date = cfg.start_date;
        if (date > cfg.end_date) date = cfg.end_date;
        this.form.date = date;

        const ex = cfg.existing;
        if (ex) {
          this.form.unit_sub_department = ex.unit_sub_department || '';
          ['activity_summary', 'skills_acquired', 'challenges_faced', 'solutions_applied']
            .forEach((f) => { this.form[f] = ex[f] || ''; });
          this.form.tags = [...(ex.tags || [])];
          Object.entries(ex.tallies || {}).forEach(([k, v]) => { this.form.tallies[k] = v; });
          Object.entries(ex.details || {}).forEach(([k, v]) => { this.form.details[k] = v; });
        }
      },

      get weekNumber() {
        if (!this.form.date) return '';
        const d = new Date(this.form.date + 'T00:00:00');
        const s = new Date(cfg.start_date + 'T00:00:00');
        const days = Math.round((d - s) / 86400000);
        return Math.floor(days / 7) + 1;
      },

      // ----- Tags -----
      addTag(value) {
        let tag = (value !== undefined ? value : this.tagInput).trim();
        if (!tag) return;
        if (!tag.startsWith('#')) tag = '#' + tag;
        if (!this.form.tags.includes(tag) && this.form.tags.length < 20) this.form.tags.push(tag);
        this.tagInput = '';
      },
      removeTag(tag) {
        this.form.tags = this.form.tags.filter((t) => t !== tag);
      },

      // ----- Voice dictation -----
      dictate(field) {
        if (!this.speechSupported) return;
        if (this.listening === field && recognition) {
          recognition.stop();
          return;
        }
        if (recognition) recognition.stop();

        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        const rec = new SR();
        rec.lang = 'en-US'; // try 'en-GH' if it recognises your accent better
        rec.continuous = true;
        rec.interimResults = false;

        rec.onstart = () => { this.listening = field; };
        rec.onresult = (e) => {
          for (let i = e.resultIndex; i < e.results.length; i++) {
            if (e.results[i].isFinal) {
              this.form[field] = (this.form[field] + ' ' + e.results[i][0].transcript).trim();
            }
          }
        };
        rec.onend = () => { this.listening = null; };
        rec.onerror = (e) => {
          this.listening = null;
          if (e.error === 'not-allowed') this.setMessage('Microphone permission was blocked.', 'error');
          else if (e.error === 'network') this.setMessage('Dictation needs an internet connection.', 'error');
        };

        recognition = rec;
        rec.start();
      },

      // ----- Saving -----
            validate() {
        if (!this.form.date) return 'Pick a date.';
        if (this.form.date < cfg.start_date || this.form.date > cfg.end_date) {
          return 'That date is outside your attachment period (' + cfg.start_date + ' to ' + cfg.end_date + ').';
        }
        if (!this.form.unit_sub_department.trim()) return 'Choose or type the unit you worked in.';
        if (!this.form.activity_summary.trim()) return 'Describe what you did today.';
        return '';
      },

      buildPayload() {
        const tallies = {};
        Object.entries(this.form.tallies).forEach(([k, v]) => {
          if (v !== '' && v !== null && !Number.isNaN(Number(v))) tallies[k] = Number(v);
        });
        const details = {};
        Object.entries(this.form.details).forEach(([k, v]) => {
          if (String(v || '').trim()) details[k] = String(v).trim();
        });
        return {
          attachment_id: cfg.attachment_id,
          date: this.form.date,
          unit_sub_department: this.form.unit_sub_department.trim(),
          activity_summary: this.form.activity_summary.trim(),
          skills_acquired: this.form.skills_acquired.trim(),
          challenges_faced: this.form.challenges_faced.trim(),
          solutions_applied: this.form.solutions_applied.trim(),
          tags: this.form.tags,
          numeric_tallies: tallies,
          department_details: details,
        };
      },

      async queueOffline(payload, authProblem) {
        await window.MPOffline.queueLog(payload);
        this.setMessage(
          authProblem
            ? 'Session expired. Saved on this device; log in again to sync.'
            : 'Saved on this device. It will sync automatically when you are back online.',
          'success'
        );
      },

      async submit() {
        const problem = this.validate();
        if (problem) { this.setMessage(problem, 'error'); return; }

        const payload = this.buildPayload();
        this.saving = true;
        try {
          if (!navigator.onLine) {
            await this.queueOffline(payload, false);
            return;
          }

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
          this.setMessage('Could not save: ' + e.message, 'error');
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