(() => {
  const messagesEl = document.getElementById('messages');
  const textEl = document.getElementById('text');
  const sendBtn = document.getElementById('send');
  const nameEl = document.getElementById('name');
  const setNameBtn = document.getElementById('set-name');
  const findBtn = document.getElementById('find');
  const topicSelect = document.getElementById('topic-select');
  const statusEl = document.getElementById('status');
  const cancelSearchBtn = document.getElementById('cancel-search');
  const leaveRoomBtn = document.getElementById('leave-room');
  const attachBtn = document.getElementById('attach');
  const fileInput = document.getElementById('file-input');
  const darkToggle = document.getElementById('dark-toggle');
  const previewArea = document.getElementById('preview-area');
  const previewThumb = document.getElementById('preview-thumb');
  const previewName = document.getElementById('preview-name');
  const previewSendBtn = document.getElementById('preview-send');
  const previewCancelBtn = document.getElementById('preview-cancel');
  const avatarEl = document.getElementById('avatar');

  let displayName = localStorage.getItem('chat_name') || '';
  if (displayName) nameEl.value = displayName;

  let currentRoom = null;
  let currentTopic = null;
  let es = null;
  let lastStatusPoll = 0;
  let matchController = null; // AbortController for /match request
  let pendingMedia = null; // { url, name }
  let typingTimeout = null;
  let typingCheckInterval = null;

  // initialize avatar from stored name
  function updateAvatar() {
    const val = (displayName || '').trim();
    avatarEl.textContent = val ? val[0].toUpperCase() : 'A';
  }
  updateAvatar();

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
      .replace(/'/g,'&#39;');
  }

  function appendMessage(m){
    const el = document.createElement('div');
    el.className = m.type === 'system' ? 'message system' : 'message';
    const time = new Date((m.ts||0)*1000).toLocaleTimeString();
    const author = m.author || 'Anonymous';
    
    if (m.type === 'system') {
      el.innerHTML = `<div class="system-message">${escapeHtml(m.text||'')}</div>`;
    } else {
      let body = `<div class="meta"><strong>${escapeHtml(author)}</strong> · <span class="timestamp">${time}</span></div><div class="body">${escapeHtml(m.text||'')}</div>`;
      if (m.media){
        // safe-URL: the server returns a relative path; render as image
        body += `<img src="${escapeHtml(m.media)}" alt="attachment" />`;
      }
      el.innerHTML = body;
    }
    
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setStatus(s, type = 'normal'){
    statusEl.textContent = s;
    statusEl.className = type;
  }

  function enableChat(enabled){
    textEl.disabled = !enabled;
    sendBtn.disabled = !enabled;
    leaveRoomBtn.disabled = !enabled;
  }

  // initial: disable chat
  enableChat(false);

  // Populate topics dropdown (client-side static list to match server)
  async function loadTopics(){
    try{
      const r = await fetch('/topics');
      if (!r.ok) throw new Error('failed');
      const topics = await r.json();
      topicSelect.innerHTML = '<option value="">Any topic (random)</option>';
      topics.forEach(t=>{
        const o = document.createElement('option'); o.value = t; o.textContent = t; topicSelect.appendChild(o);
      });
    }catch(e){
      // fallback small list
      const fallback = ["Anxiety","Depression","Loneliness","Motivation"];
      fallback.forEach(t=>{ const o = document.createElement('option'); o.value=t; o.textContent=t; topicSelect.appendChild(o); });
    }
  }
  loadTopics();

  // poll server for waiting counts (removed - no longer needed)

  function startStream(room){
    if(es){
      try{ es.close(); }catch(e){}
      es = null;
    }
    es = new EventSource(`/stream/${encodeURIComponent(room)}`);
    es.addEventListener('message', ev=>{
      try {
        const payload = JSON.parse(ev.data);
        appendMessage(payload);
      } catch(e){}
    });
    es.addEventListener('keepalive', ev=>{
      // Handle keepalive events to maintain connection
    });
    es.addEventListener('error', () => {
      // show reconnection attempts
      setStatus('Disconnected — attempting reconnect…');
      // Attempt to reconnect after a delay
      setTimeout(() => {
        if (currentRoom && !es) {
          startStream(currentRoom);
        }
      }, 3000);
    });
  }

  async function findPartner(){
    // cancel any previous controller
    if (matchController){
      matchController.abort();
      matchController = null;
    }

    setStatus('Searching for partner…', 'searching');
    enableChat(false);
    messagesEl.innerHTML = ''; // clear previous chat
    const topic = topicSelect.value || undefined;

    // prepare abort controller so we can cancel
    matchController = new AbortController();
    cancelSearchBtn.disabled = false;
    findBtn.disabled = true;

    try {
      const r = await fetch('/match', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ topic }),
        signal: matchController.signal
      });
      
      if (!r.ok){
        const err = await r.json().catch(()=>({}));
        setStatus(err.reason || 'No match found');
        return;
      }
      
      const res = await r.json();
      if (res.matched){
        currentRoom = res.room;
        currentTopic = res.topic;
        setStatus(`Connected — Topic: ${currentTopic}`, 'connected');
        enableChat(true);
        leaveRoomBtn.disabled = false;
        
        // Send join message (only for the first user)
        const joinResponse = await fetch('/join', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ room: currentRoom, author: displayName || 'Anonymous' })
        }).catch(()=>({}));
        
        // Update display name if server assigned a unique name
        if (joinResponse.ok) {
          const joinData = await joinResponse.json().catch(()=>({}));
          if (joinData.assigned_name) {
            displayName = joinData.assigned_name;
            updateAvatar();
          }
        }
        
        // load recent room history
        // Removed client-side history fetch, as SSE stream already provides it
        startStream(currentRoom);
        startTypingCheck();
      } else {
        setStatus('No match: ' + (res.reason || 'timeout'), 'error');
      }
    } catch (e) {
      // aborted or network error
      if (e.name === 'AbortError') {
        setStatus('Search cancelled');
      } else {
        setStatus('Connection error - please try again', 'error');
      }
      try { 
        fetch('/leave-queue', {
          method:'POST', 
          headers:{'Content-Type':'application/json'}, 
          body: JSON.stringify({topic})
        }).catch(()=>{}); 
      } catch(e){}
    } finally {
      cancelSearchBtn.disabled = true;
      findBtn.disabled = false;
      matchController = null;
    }
  }

  // allow canceling an in-progress match request
  cancelSearchBtn.addEventListener('click', ()=>{
    if (matchController){
      matchController.abort();
      cancelSearchBtn.disabled = true;
      findBtn.disabled = false;
    }
  });

  async function leaveRoom(){
    if (!currentRoom) return;
    try {
      // Send leave message
      await fetch('/leave', {
        method:'POST', 
        headers:{'Content-Type':'application/json'}, 
        body: JSON.stringify({ room: currentRoom, author: displayName || 'Anonymous' })
      }).catch(()=>{});
      
      // close SSE
      if (es){ try{ es.close(); }catch(e){} es = null; }
      stopTypingCheck();
    } finally {
      currentRoom = null;
      currentTopic = null;
      enableChat(false);
      leaveRoomBtn.disabled = true;
      setStatus('Left. Not connected', 'normal');
      messagesEl.innerHTML = '';
    }
  }

  // allow sending only if there's text or pending media
  function updateSendState(){
    const hasText = !!textEl.value.trim();
    sendBtn.disabled = !(hasText || pendingMedia) || !currentRoom;
  }

  textEl.addEventListener('input', () => {
    updateSendState();
    sendTypingIndicator();
  });

  // send on Enter (without Shift)
  textEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  async function sendMessage(){
    if (!currentRoom) return;
    const text = textEl.value.trim();
    const payload = { author: displayName || 'Anonymous', text, room: currentRoom, media: pendingMedia ? pendingMedia.url : undefined };
    
    // if nothing to send, abort
    if (!text && !pendingMedia) return;
    
    sendBtn.disabled = true;
    try {
      await fetch('/message', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
    } catch (e) {
      // ignore
    } finally {
      textEl.value = '';
      // clear pending media after send
      if (pendingMedia){
        pendingMedia = null;
        previewArea.style.display = 'none';
        previewThumb.src = '';
        previewName.textContent = '';
      }
      updateSendState();
      textEl.focus();
    }
  }

  // upload file but keep as draft (preview), do not auto-send
  async function uploadFile(file){
    const fd = new FormData();
    fd.append('file', file);
    try{
      setStatus('Uploading...');
      const r = await fetch('/upload', { method: 'POST', body: fd });
      if (!r.ok) {
        const j = await r.json().catch(()=>({}));
        setStatus(j.error || 'Upload failed');
        return null;
      }
      const j = await r.json();
      return j.url;
    }catch(e){ setStatus('Upload error'); return null; }
  }

  attachBtn.addEventListener('click', ()=> fileInput.click());
  fileInput.addEventListener('change', async (e)=>{
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    setStatus('Uploading...');
    const url = await uploadFile(f);
    if (url){
      // set pending media and show preview
      pendingMedia = { url, name: f.name };
      previewThumb.src = url;
      previewName.textContent = f.name;
      // ensure preview-area uses flex display when shown
      previewArea.style.display = 'flex';
      updateSendState();
    } else {
      pendingMedia = null;
    }
    fileInput.value = '';
    setStatus(currentRoom?`Connected — Topic: ${currentTopic}`:'Not connected');
  });

  // make messages focusable so clicks are registered reliably
  messagesEl.tabIndex = 0;

  // focus composer when clicking messages area (makes it feel clickable)
  messagesEl.addEventListener('click', (ev) => {
    try {
      // always try to give focus to the composer so users can start typing immediately
      textEl.focus();
      // ensure textarea is visible (useful on mobile)
      textEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) {
      try { messagesEl.focus(); } catch(e){}
    }
  });

  // Dynamically adjust bottom padding of messages so sticky footer/preview never cover content
  function adjustMessagesPadding() {
    try {
      const footerRect = document.querySelector('footer').getBoundingClientRect();
      const previewRect = previewArea && previewArea.style.display !== 'none'
        ? previewArea.getBoundingClientRect()
        : { height: 0 };
      // extra gap for comfortable scrolling
      const gap = 24;
      const pad = Math.ceil(footerRect.height + (previewRect.height || 0) + gap);
      messagesEl.style.paddingBottom = pad + 'px';
    } catch (e) {
      // fallback
      messagesEl.style.paddingBottom = '100px';
    }
  }

  // run initially and on viewport/element changes
  window.addEventListener('resize', adjustMessagesPadding);
  // when preview toggles we call adjust in the handlers below (and after upload)
  setTimeout(adjustMessagesPadding, 50); // initial adjust after layout

  previewSendBtn.addEventListener('click', async ()=>{
    // just focus composer so user can add text and press Send
    textEl.focus();
  });
  previewCancelBtn.addEventListener('click', ()=>{
    pendingMedia = null;
    previewArea.style.display = 'none';
    adjustMessagesPadding();
    previewThumb.src = '';
    previewName.textContent = '';
    updateSendState();
  });

  // when user sets name, update avatar initial
  setNameBtn.addEventListener('click', () => {
    displayName = nameEl.value.trim();
    localStorage.setItem('chat_name', displayName);
    updateAvatar();
  });

  // when preview is shown after upload, adjust padding
  // (we already set display:flex where appropriate during upload)
  const originalFileInputHandler = fileInput.onchange;
  // ensure adjust is called after upload flow
  (function wrapUploadAdjust() {
    // we hook into existing flow by intercepting preview display points below
  })();

  // wire up controls
  sendBtn.addEventListener('click', sendMessage);
  findBtn.addEventListener('click', findPartner);
  leaveRoomBtn.addEventListener('click', leaveRoom);

  // adjust when preview visibility changes via attach flow
  const origAttachHandler = fileInput.addEventListener;
  // Note: we adjust padding whenever we show/hide preview in code above; ensure final adjust call now
  setTimeout(adjustMessagesPadding, 300);
  // also run after any message appended so scroll/padding remain correct
  const origAppendMessage = appendMessage;
  appendMessage = function(m) {
    origAppendMessage(m);
    setTimeout(adjustMessagesPadding, 40);
  };

  // Typing indicator functions
  function sendTypingIndicator() {
    if (!currentRoom || !displayName) return;
    
    // Clear existing timeout
    if (typingTimeout) {
      clearTimeout(typingTimeout);
    }
    
    // Send typing indicator
    fetch('/typing', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ room: currentRoom, author: displayName })
    }).catch(()=>{});
    
    // Set timeout to stop typing indicator after 2 seconds of inactivity
    typingTimeout = setTimeout(() => {
      // Typing indicator will expire on server side
    }, 2000);
  }
  
  function startTypingCheck() {
    if (typingCheckInterval) return;
    
    typingCheckInterval = setInterval(async () => {
      if (!currentRoom) return;
      
      try {
        const response = await fetch(`/typing-status/${encodeURIComponent(currentRoom)}`);
        const data = await response.json();
        updateTypingIndicator(data.typing || []);
      } catch (e) {
        // Ignore errors
      }
    }, 1000);
  }
  
  function stopTypingCheck() {
    if (typingCheckInterval) {
      clearInterval(typingCheckInterval);
      typingCheckInterval = null;
    }
    if (typingTimeout) {
      clearTimeout(typingTimeout);
      typingTimeout = null;
    }
  }
  
  function updateTypingIndicator(typingUsers) {
    // Remove current user from typing list
    const otherTyping = typingUsers.filter(user => user !== displayName);
    
    // Update typing indicator in UI
    let typingEl = document.getElementById('typing-indicator');
    if (otherTyping.length > 0) {
      if (!typingEl) {
        typingEl = document.createElement('div');
        typingEl.id = 'typing-indicator';
        typingEl.className = 'typing-indicator';
        messagesEl.appendChild(typingEl);
      }
      const names = otherTyping.join(', ');
      typingEl.innerHTML = `<div class="typing-dots">${names} ${otherTyping.length === 1 ? 'is' : 'are'} typing<span class="dots">...</span></div>`;
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else if (typingEl) {
      typingEl.remove();
    }
  }

  // dark mode toggle
  function applyDark(val){
    if (val) document.body.classList.add('dark'); else document.body.classList.remove('dark');
    localStorage.setItem('dark', val? '1':'0');
  }
  darkToggle.addEventListener('change', ()=> applyDark(darkToggle.checked));
  // restore
  const savedDark = localStorage.getItem('dark') === '1';
  darkToggle.checked = savedDark; applyDark(savedDark);
})();
