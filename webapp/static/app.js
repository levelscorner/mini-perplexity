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

  // ── Show-reasoning toggle ──────────────────────────────────────────

  toggleEl.addEventListener('change', () => {
    reasoningEl.classList.toggle('hidden', !toggleEl.checked);
  });

  clearBtn.addEventListener('click', () => {
    reasoningBody.innerHTML = '';
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

  async function ask(question) {
    const collected = [];
    let thinkingBubble;

    sendBtn.disabled = true;
    questionEl.disabled = true;
    statusEl.textContent = 'Running…';

    appendBubble('user', esc(question));
    thinkingBubble = appendBubble('thinking', 'thinking…');

    let resp;
    try {
      resp = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
    } catch (err) {
      thinkingBubble.remove();
      appendBubble('assistant', `<em>Network error:</em> ${esc(err.message)}`);
      reset();
      return;
    }

    if (!resp.ok) {
      thinkingBubble.remove();
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
        if (event.kind !== 'user') appendEvent(event);

        // On final → swap the thinking bubble for the answer.
        if (event.kind === 'final') {
          thinkingBubble.remove();
          appendBubble('assistant', renderAnswer(collected, event.payload));
          statusEl.textContent =
            `Done — ${collected.length} events.`;
        }

        if (event.kind === 'error' && !collected.some((e) => e.kind === 'final')) {
          // Show first error if we never got a final answer.
          // Don't remove the thinking bubble yet; iter may recover.
        }
      }
    }

    // Stream closed. If we never got a `final`, surface that.
    if (!collected.some((e) => e.kind === 'final')) {
      thinkingBubble.remove();
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
    const q = questionEl.value.trim();
    if (!q) return;
    ask(q);
  });

  questionEl.focus();
})();
