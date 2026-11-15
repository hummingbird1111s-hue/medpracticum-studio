document.addEventListener('alpine:init', () => {
  Alpine.data('logForm', (cfg) => {
    let recognition = null;

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

      init() {
        const now = new Date();
        const localToday = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
        
        // Use the existing date if editing, otherwise default to today.
        // We removed the clamping logic so users can select any date freely.
        this.form.date = cfg.existing ? cfg.existing.date : localToday;

        const ex = cfg.existing;
        if (ex) {
          this.form.id = ex.id || null;
          this.form.unit_sub_department = ex.unit_sub_department || '';
          ['activity_summary', 'skills_acquired', 'challenges_faced', 'solutions_applied']
            .forEach((f) => { this.form[f] = ex[f] || ''; });
        }
      },

      get weekNumber() {
        if (!this.form.date) return '';
        const d = new Date(this.form.date + 'T00:00:00');
        const s = new Date(cfg.start_date + 'T00:00:00');
        const days = Math.round((d - s) / 86400000);
        return Math.floor(days / 7) + 1;
      },

      dictate(field) {
        console.log("Dictate clicked for:", field);
        
        if (!this.speechSupported) {
          this.setMessage('Voice dictation is not supported in this browser. Use Chrome or Edge.', 'error');
          return;
        }
        
        if (this.listening === field && recognition) {
          recognition.stop();
          return;
        }
        if (recognition) recognition.stop();

        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        const rec = new SR();
        rec.lang = 'en-US'; 
        rec.continuous = true;
        rec.interimResults = true; 

        rec.onstart = () => { 
          console.log("Mic started successfully");
          this.listening = field; 
          this.interimText = '';
          this.setMessage('Listening... Speak now.', 'success');
        };

        rec.onresult = (e) => {
          let interim = '';
          let final = '';

          for (let i = e.resultIndex; i < e.results.length; i++) {
            const transcript = e.results[i][0].transcript;
            if (e.results[i].isFinal) {
              final += transcript + ' ';
            } else {
              interim += transcript;
            }
          }

          if (final) {
            this.form[field] = (this.form[field] + ' ' + final).trim();
          }
          this.interimText = interim; 
        };

        rec.onend = () => { 
          console.log("Mic stopped");
          this.listening = null; 
          this.interimText = '';
        };

        rec.onerror = (e) => {
          console.error("Mic Error:", e.error);
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

        recognition = rec;
        try {
          rec.start();
        } catch (err) {
          console.error("Mic Start failed:", err);
          this.setMessage('Could not start mic. Wait a second and try again.', 'error');
        }
      },

      validate() {
        if (!this.form.date) return 'Pick a date.';
        // Date range validation removed to allow any date selection
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
        await window.MPOffline.queueLog(payload);
        this.setMessage(
          authProblem ? 'Session expired. Saved on this device; log in to sync.' : 'Saved offline. Will sync when online.',
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