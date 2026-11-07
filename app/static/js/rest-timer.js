/* Timestamp-based, user/session-scoped rest. No per-second live announcements. */
(() => {
  'use strict';
  const prefix = 'fithub:rest:';
  const user = document.body.dataset.userId || '';
  const read = key => { try { return JSON.parse(localStorage.getItem(key)); } catch (_) { return null; } };
  const remove = key => { try { localStorage.removeItem(key); } catch (_) {} };
  const clearAll = () => {
    try { Object.keys(localStorage).filter(k => k.startsWith(prefix)).forEach(remove); } catch (_) {}
  };
  if (!user) { clearAll(); return; }
  const owner = prefix + 'owner';
  if (read(owner) !== user) clearAll();
  try { localStorage.setItem(owner, JSON.stringify(user)); } catch (_) {}
  document.querySelectorAll('form[action="/logout"]').forEach(form => form.addEventListener('submit', clearAll));
  const configElement = document.getElementById('rest-config');
  if (!configElement) return;
  const config = JSON.parse(configElement.textContent);
  const key = prefix + user + ':' + config.workout;
  if (config.finished) { remove(key); return; }
  const panel = document.querySelector('[data-rest-timer]');
  if (!panel) return;
  const display = panel.querySelector('.rest-display');
  const status = panel.querySelector('.rest-status');
  const toggle = panel.querySelector('[data-rest-action="toggle"]');
  const duration = panel.querySelector('[data-rest-duration]');
  const sound = panel.querySelector('[data-rest-sound]');
  const audioStatus = panel.querySelector('.rest-audio-status');
  const offset = config.server_ms - Date.now();
  const now = () => Date.now() + offset;
  const fresh = () => ({phase:'idle',remaining:90000,base:90000,deadline:null,sound:false,lastEvent:null});
  const valid = value => value && ['idle','running','paused','complete'].includes(value.phase)
    && Number.isFinite(value.remaining) && value.remaining >= 0 && value.remaining <= 3600000
    && Number.isFinite(value.base) && value.base >= 1000 && value.base <= 3600000
    && (value.phase !== 'running' || Number.isFinite(value.deadline));
  let state = read(key);
  if (!valid(state)) state = fresh();
  let audio;
  let cleared = false;
  const persist = () => { try { localStorage.setItem(key, JSON.stringify(state)); } catch (_) {
    audioStatus.textContent = 'Browser storage is unavailable; the timer cannot survive a reload.';
  } };
  const remaining = () => state.phase === 'running' ? Math.min(3600000, Math.max(0,state.deadline-now())) : state.remaining;
  const announce = text => { if (status.textContent !== text) status.textContent = text; };
  const beep = () => {
    if (!state.sound) return;
    if (!audio || audio.state !== 'running') {
      audioStatus.textContent = 'Rest complete. Tap the sound toggle to enable audio for this page.'; return;
    }
    try {
      const oscillator=audio.createOscillator(), gain=audio.createGain();
      oscillator.connect(gain);gain.connect(audio.destination);oscillator.frequency.value=660;
      gain.gain.setValueAtTime(0.1,audio.currentTime);gain.gain.exponentialRampToValueAtTime(0.001,audio.currentTime+0.45);
      oscillator.start();oscillator.stop(audio.currentTime+0.45);
    } catch (_) { audioStatus.textContent = 'Sound unavailable. Rest complete.'; }
  };
  const render = () => {
    const milliseconds = remaining();
    if (state.phase === 'running' && milliseconds <= 0) {
      state.phase='complete';state.remaining=0;state.deadline=null;persist();announce('Rest complete. Ready for your next set.');beep();
    }
    const seconds=Math.ceil(milliseconds/1000);
    display.textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
    panel.dataset.phase=state.phase;
    toggle.textContent=state.phase==='running'?'Pause':state.phase==='paused'?'Resume':'Start';
    sound.checked=Boolean(state.sound);
  };
  const event=config.event;
  if (event && event.set_id > (state.lastEvent || 0)) {
    state.lastEvent=event.set_id;
    if (Number.isInteger(event.seconds) && event.seconds>=1 && event.seconds<=3600) {
      state.base=event.seconds*1000;state.remaining=state.base;state.deadline=event.started_ms+state.base;state.phase='running';
      announce('Set saved. Prescribed rest started.');
    } else { announce('Set saved. No prescribed rest: start a manual timer, default 90 seconds.'); }
    persist();
  } else if (state.phase==='running') announce('Rest running. You can continue logging sets.');
  else if (state.phase==='paused') announce('Rest paused.');
  else if (state.phase==='complete') announce('Rest complete. Ready for your next set.');
  // A refresh or copied redirect must not replay a successful set event.
  if (new URL(location.href).searchParams.has('rest')) {
    const url=new URL(location.href);url.searchParams.delete('rest');history.replaceState(null,'',url);
  }
  panel.querySelectorAll('[data-rest-action]').forEach(button => button.addEventListener('click',() => {
    if (cleared) return;
    const action=button.dataset.restAction;
    if (action==='manual') {
      const seconds=Number(duration.value);
      if (!Number.isInteger(seconds) || seconds<1 || seconds>3600) { duration.reportValidity();return; }
      state.base=seconds*1000;state.remaining=state.base;state.deadline=now()+state.remaining;state.phase='running';announce('New manual rest started. Previous timer replaced.');
    } else if (action==='toggle') {
      if (state.phase==='running') {
        state.remaining=remaining();state.deadline=null;state.phase='paused';announce('Rest paused.');
      } else {
        if (state.phase==='idle') {
          const seconds=Number(duration.value);
          if (!Number.isInteger(seconds) || seconds<1 || seconds>3600) { duration.reportValidity(); return; }
          state.base=seconds*1000;state.remaining=state.base;
        } else if (state.phase==='complete') state.remaining=state.base;
        state.deadline=now()+state.remaining;state.phase='running';announce('Rest running. You can continue logging sets.');
      }
    } else if (action==='reset') {
      state.remaining=state.base;state.deadline=null;state.phase='paused';announce('Rest reset and paused.');
    } else if (action==='skip') {
      const lastEvent=state.lastEvent, enabled=state.sound;state=fresh();state.lastEvent=lastEvent;state.sound=enabled;
      duration.value='90';announce('Rest skipped. Ready for a manual rest.');
    } else {
      state.remaining=Math.max(0,Math.min(3600000,remaining()+(action==='plus'?15000:-15000)));
      if (state.phase==='running') state.deadline=now()+state.remaining;
      else if (state.phase==='idle') { state.base=Math.max(1000,state.remaining);duration.value=String(state.base/1000); }
      else state.phase='paused';
      announce(action==='plus'?'Added 15 seconds.':'Subtracted 15 seconds.');
    }
    persist();render();
  }));
  sound.addEventListener('change',async () => {
    state.sound=sound.checked;persist();
    if (!state.sound) { audioStatus.textContent='Sound is off. Visual completion is always available.';return; }
    try {
      const Context=window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error('unsupported');
      audio=audio || new Context();await audio.resume();
      audioStatus.textContent=audio.state==='running'?'Sound enabled for this page.':'Audio blocked by browser; visual completion remains available.';
    } catch (_) { audioStatus.textContent='Audio unavailable or blocked by browser. Visual completion remains available.'; }
  });
  if (state.sound) audioStatus.textContent='Sound selected. Toggle it off and on to enable audio after reloading.';
  let interval=setInterval(render,250);
  const stop = () => {
    cleared=true;clearInterval(interval);state=fresh();render();announce('Timer cleared. This workout ended or you logged out.');
    panel.querySelectorAll('button,input').forEach(control=>control.disabled=true);
  };
  window.addEventListener('storage',event => {
    if (event.key===key) { const value=read(key);if (valid(value)) {
      state=value;render();announce(state.phase==='complete'?'Rest complete. Ready for your next set.':state.phase==='running'?'Rest running. You can continue logging sets.':state.phase==='paused'?'Rest paused.':'Ready for a manual rest.');
    } else stop(); }
    else if (event.key===owner && read(owner)!==user) stop();
  });
  const check = async () => {
    render();
    try {
      const response=await fetch('/workouts/'+config.workout+'/timer-state',{credentials:'same-origin',cache:'no-store'});
      if (response.redirected || response.status===404 || (response.ok && !(await response.json()).active)) { remove(key);stop(); }
    } catch (_) { /* A temporary network failure does not stop local timing or logging. */ }
  };
  document.addEventListener('visibilitychange',() => { if (!document.hidden) check(); });
  window.addEventListener('focus',check);
  window.addEventListener('pageshow',event=>{if(event.persisted)check();});
  render();
})();
