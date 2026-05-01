// Mini Perplexity — webapp client
//
// Owns:
//   - The composer (question input + send).
//   - Streaming events from POST /api/run via fetch + ReadableStream.
//   - Routing each event into the chat (user / final) or the
//     reasoning panel (llm / tool_call / tool_result / error / system).
//   - The Show-reasoning toggle.
//
// Architecture: vanilla JS, zero deps. SSE consumed via fetch+stream
// instead of EventSource because EventSource is GET-only and our run
// endpoint takes a JSON body.

(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const chatEl       = $('chat');
  const reasoningEl  = $('reasoning');
  const reasoningBody = $('reasoning-body');
  const toggleEl     = $('reasoning-toggle');
  const clearBtn     = $('clear-reasoning');
  const composer     = $('composer');
  const questionEl   = $('question');
  const sendBtn      = $('send');
  const statusEl     = $('status');
  const imageToggle  = $('image-toggle');
  const themeToggle  = $('theme-toggle');

  // Image-mode is one-shot: enabled by toggle or `/image` prefix, then
  // reset after the next send.
  let imageMode = false;

  // ── Show-reasoning toggle ──────────────────────────────────────────

  toggleEl.addEventListener('change', () => {
    reasoningEl.classList.toggle('hidden', !toggleEl.checked);
  });

  clearBtn.addEventListener('click', () => {
    reasoningBody.innerHTML = '';
  });

  // ── Image-mode toggle ──────────────────────────────────────────────

  imageToggle.addEventListener('click', () => {
    imageMode = !imageMode;
    imageToggle.setAttribute('aria-pressed', imageMode ? 'true' : 'false');
  });

  // ── Theme toggle ───────────────────────────────────────────────────

  themeToggle.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
  });

  // ── Helpers ────────────────────────────────────────────────────────

  /** Append a chat bubble. Returns the element so callers can mutate it. */
  function appendBubble(role, html) {
    const div = document.createElement('div');
    div.className = `bubble ${role}`;
    div.innerHTML = html;
    chatEl.appendChild(div);
    chatEl.scrollTop = chatEl.scrollHeight;
    return div;
  }

  /** Append an event chip into the reasoning panel. */
  function appendEvent(event) {
    const wrap = document.createElement('div');
    wrap.className = `event ${event.kind}`;

    const label = document.createElement('div');
    label.className = 'event-label';
    label.textContent = labelFor(event.kind);
    if (event.iteration) {
      const iter = document.createElement('span');
      iter.className = 'event-iter';
      iter.textContent = `iter ${event.iteration}`;
      label.appendChild(iter);
    }
    wrap.appendChild(label);

    const pre = document.createElement('pre');
    pre.textContent = formatPayload(event);
    wrap.appendChild(pre);

    reasoningBody.appendChild(wrap);
    reasoningBody.scrollTop = reasoningBody.scrollHeight;
  }

  function labelFor(kind) {
    return {
      user: 'You',
      llm: 'LLM thought',
      tool_call: 'Tool call',
      tool_result: 'Tool result',
      final: 'Final answer',
      error: 'Error',
      system: 'System',
    }[kind] || kind;
  }

  /** Pretty-print a payload for the reasoning panel. */
  function formatPayload(event) {
    const p = event.payload;
    if (typeof p === 'string') return p;
    try {
      return JSON.stringify(p, null, 2);
    } catch {
      return String(p);
    }
  }

  /** Escape HTML so user/LLM strings can't inject markup. */
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Status pill — hybrid in-chat trace summary ─────────────────────
  //
  // One pill per chat turn, anchored under the user's bubble. While the
  // agent is running, it shows "→ <tool_name>…" pulsing. When the turn
  // ends, it collapses to "→ N tools · Xs ✓". Full inputs/outputs still
  // live on /dashboard via /api/recent-activity.

  function makeStatusPill() {
    const pill = document.createElement('div');
    pill.className = 'status-pill';
    pill.dataset.calls = '0';
    pill.dataset.start = String(Date.now());

    const dot = document.createElement('span');
    dot.className = 'pill-dot';
    pill.appendChild(dot);

    const txt = document.createElement('span');
    txt.className = 'pill-text';
    txt.textContent = 'thinking…';
    pill.appendChild(txt);

    chatEl.appendChild(pill);
    chatEl.scrollTop = chatEl.scrollHeight;
    return pill;
  }

  function pillToolCall(pill, name) {
    if (!pill) return;
    pill.dataset.calls = String(Number(pill.dataset.calls) + 1);
    pill.querySelector('.pill-text').textContent = `→ ${name}…`;
  }

  function pillCollapse(pill) {
    if (!pill) return;
    const calls = Number(pill.dataset.calls);
    const elapsed = ((Date.now() - Number(pill.dataset.start)) / 1000).toFixed(1);
    const txt = calls === 0
      ? `done · ${elapsed}s`
      : `${calls} tool${calls === 1 ? '' : 's'} · ${elapsed}s ✓`;
    pill.querySelector('.pill-text').textContent = txt;
    pill.classList.add('collapsed');
  }

  // ── Image / comic strip rendering ─────────────────────────────────
  //
  // The server emits a synthetic `image` SSE event right after a
  // render_image tool_result, with payload shape:
  //   {kind:'image', slug, url, alt}
  //   {kind:'comic_strip', panels:[{slug,url,alt,error?}, ...]}

  function renderImagePayload(payload) {
    if (payload.kind === 'image') {
      const card = document.createElement('figure');
      card.className = 'image-card';
      card.innerHTML = `
        <img src="${esc(payload.url)}" alt="${esc(payload.alt || '')}" />
        <figcaption class="caption">
          <span class="slug">${esc(payload.slug)}</span>
          <a href="${esc(payload.url)}" target="_blank" rel="noopener">open ↗</a>
        </figcaption>`;
      chatEl.appendChild(card);
    } else if (payload.kind === 'comic_strip') {
      const panels = payload.panels || [];
      const wrap = document.createElement('div');
      wrap.className = 'comic-strip';
      wrap.dataset.panels = String(panels.length);
      for (const p of panels) {
        const cell = document.createElement('div');
        cell.className = 'panel' + (p.error ? ' error' : '');
        if (p.url) {
          cell.innerHTML =
            `<img src="${esc(p.url)}" alt="${esc(p.alt || '')}" />`;
        } else {
          cell.textContent = `failed: ${p.error || 'unknown'}`;
        }
        wrap.appendChild(cell);
      }
      chatEl.appendChild(wrap);
    }
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  /** Pull the markdown answer + sources out of the last save_answer call. */
  function extractFinalAnswer(events) {
    // Walk events backwards to find the most recent save_answer.
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.kind === 'tool_call' && e.payload?.name === 'save_answer') {
        const args = e.payload.arguments || {};
        return {
          answer: args.answer || '',
          sources: args.sources || [],
          path: null,
        };
      }
    }
    return null;
  }

  /** Render the assistant bubble — answer markdown + numbered sources. */
  function renderAnswer(events, finalText) {
    const data = extractFinalAnswer(events);
    let answer = data?.answer || finalText || '';
    const sources = data?.sources || [];

    // Tiny markdown: turn [n] citations into superscript links if a
    // matching source exists; otherwise leave them inline.
    answer = esc(answer).replace(/\[(\d+)\]/g, (m, n) => {
      const s = sources[Number(n) - 1];
      if (!s?.url) return m;
      return `<sup><a href="${esc(s.url)}" target="_blank" rel="noopener">[${n}]</a></sup>`;
    });
    // Newlines → <br> for visual paragraphing.
    answer = answer.replace(/\n/g, '<br>');

    let html = `<div class="body">${answer}</div>`;
    if (sources.length) {
      const list = sources.map((s, i) => {
        const title = esc(s.title || s.url || `Source ${i + 1}`);
        const url = esc(s.url || '#');
        return `<li><a href="${url}" target="_blank" rel="noopener">${title}</a></li>`;
      }).join('');
      html += `<div class="sources"><strong>Sources</strong><ol>${list}</ol></div>`;
    }
    return html;
  }

  // ── Run the agent ──────────────────────────────────────────────────

  async function ask(question, opts) {
    const collected = [];
    let thinkingBubble;
    const useImageMode = !!(opts && opts.imageMode);

    sendBtn.disabled = true;
    questionEl.disabled = true;
    statusEl.textContent = 'Running…';

    appendBubble('user', esc(question));
    const pill = makeStatusPill();
    thinkingBubble = appendBubble('thinking', 'thinking…');

    let resp;
    try {
      resp = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, image_mode: useImageMode }),
      });
    } catch (err) {
      thinkingBubble.remove();
      pillCollapse(pill);
      appendBubble('assistant', `<em>Network error:</em> ${esc(err.message)}`);
      reset();
      return;
    }

    if (!resp.ok) {
      thinkingBubble.remove();
      pillCollapse(pill);
      const text = await resp.text().catch(() => '');
      appendBubble('assistant', `<em>HTTP ${resp.status}:</em> ${esc(text)}`);
      reset();
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // Read the SSE stream chunk by chunk. Events are separated by
    // a blank line; each event's data is on a `data: ` line.
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);

        const line = raw.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;

        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          continue;
        }

        collected.push(event);

        // Always feed the reasoning panel.
        if (event.kind !== 'user' && event.kind !== 'image') appendEvent(event);

        // Status pill updates — synthetic in-chat trace summary.
        if (event.kind === 'tool_call') {
          pillToolCall(pill, event.payload?.name || 'tool');
        }

        // Inline image / comic-strip card on the synthetic image SSE event.
        if (event.kind === 'image') {
          renderImagePayload(event.payload);
        }

        // On final → swap the thinking bubble for the answer.
        if (event.kind === 'final') {
          thinkingBubble.remove();
          pillCollapse(pill);
          appendBubble('assistant', renderAnswer(collected, event.payload));
          statusEl.textContent =
            `Done — ${collected.length} events.`;
        }
      }
    }

    // Stream closed. If we never got a `final`, surface that.
    if (!collected.some((e) => e.kind === 'final')) {
      thinkingBubble.remove();
      pillCollapse(pill);
      const errs = collected.filter((e) => e.kind === 'error');
      const msg = errs.length
        ? errs[errs.length - 1].payload
        : 'Agent ended without producing a final answer.';
      appendBubble('assistant', `<em>${esc(msg)}</em>`);
      statusEl.textContent = `Ended without final — ${collected.length} events.`;
    }

    reset();
  }

  function reset() {
    sendBtn.disabled = false;
    questionEl.disabled = false;
    questionEl.value = '';
    questionEl.focus();
  }

  // ── Wire submit ────────────────────────────────────────────────────

  composer.addEventListener('submit', (ev) => {
    ev.preventDefault();
    let q = questionEl.value.trim();
    if (!q) return;

    // `/image <prompt>` is sugar: strip the prefix, force image mode for this turn.
    let mode = imageMode;
    if (q.startsWith('/image ')) {
      q = q.slice('/image '.length).trim();
      mode = true;
      if (!q) return;
    } else if (q === '/image') {
      // Just `/image` with nothing after → ignore.
      return;
    }

    ask(q, { imageMode: mode });

    // One-shot reset of the toggle (image mode resets per send).
    if (imageMode) {
      imageMode = false;
      imageToggle.setAttribute('aria-pressed', 'false');
    }
  });

  questionEl.focus();
})();
