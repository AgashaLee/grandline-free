/* Shared card-detail modal — used by both the Card Database (/database) and the
   Meta Decks (/meta) pages. Click a card anywhere -> CardDetail.open(cardObj).

   The card object is a row from /api/database (card_id, name, set_name, image_url,
   rarity, card_type, card_color, card_cost, card_power, attribute, counter, life,
   sub_types, card_text, alt_arts[]). The modal is self-styled (cream surface) so it
   looks identical over the light database page and the dark meta page.

   ====================================================================
   AFFILIATE IDs (edit here ONCE — this file powers Buy buttons on BOTH pages):
   after you sign up for Shopee & Tokopedia affiliate (direct or via Involve Asia),
   set `id` and flip `on:true`, and update affiliate() if your deep-link differs.
   ==================================================================== */
window.CardDetail = (function () {
  const AFFILIATE = {
    // Indonesia (via Involve Asia deep-link)
    shopee:    { id: 'YOUR_SHOPEE_AFFILIATE_ID',    on: false },
    tokopedia: { id: 'YOUR_TOKOPEDIA_AFFILIATE_ID', on: false },
    // International (each program has its own link format — see affiliate())
    tcgplayer: { id: 'YOUR_TCGPLAYER_AFFILIATE_ID', on: false },
    ebay:      { id: 'YOUR_EBAY_CAMPAIGN_ID',       on: false },
  };
  const MARKET = {
    shopee:    { label: 'Shopee',    color: '#ee4d2d', region: 'id',
                 url: q => `https://shopee.co.id/search?keyword=${encodeURIComponent(q)}` },
    tokopedia: { label: 'Tokopedia', color: '#03ac0e', region: 'id',
                 url: q => `https://www.tokopedia.com/search?q=${encodeURIComponent(q)}` },
    tcgplayer: { label: 'TCGplayer', color: '#f8991d', region: 'intl',
                 url: q => `https://www.tcgplayer.com/search/all/product?q=${encodeURIComponent(q)}` },
    ebay:      { label: 'eBay',      color: '#0064d2', region: 'intl',
                 url: q => `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(q)}` },
  };

  // Which storefront set to show. Guessed from the browser (timezone/language),
  // overridable by the visitor and remembered in localStorage.
  function detectRegion() {
    try {
      const saved = localStorage.getItem('gl_region');
      if (saved === 'id' || saved === 'intl') return saved;
    } catch (e) {}
    try {
      const tz = (Intl.DateTimeFormat().resolvedOptions().timeZone || '').toLowerCase();
      const lang = (navigator.language || '').toLowerCase();
      if (/jakarta|makassar|jayapura|pontianak/.test(tz) || lang.startsWith('id')) return 'id';
    } catch (e) {}
    return 'intl';
  }
  let REGION = detectRegion();
  let _lastCard = null;

  function buyQuery(c) {
    const name = (c.name || '').replace(/\s*\(\d+\)\s*$/, '').trim();
    return `one piece card ${c.card_id} ${name}`.trim();
  }
  function affiliate(key, dest) {
    const a = AFFILIATE[key];
    if (!a || !a.on || !a.id || a.id.startsWith('YOUR_')) return dest;  // not set up -> direct
    // TODO after signup: each program wraps links differently —
    //   Shopee/Tokopedia (Involve Asia): https://invol.co/aff_m?aff_id=<id>&url=<dest>
    //   TCGplayer (Impact): append your tracking params, e.g. dest + '&partner=<id>'
    //   eBay (Partner Network): wrap dest in your rover/campid link
    if (key === 'shopee' || key === 'tokopedia')
      return `https://invol.co/aff_m?aff_id=${encodeURIComponent(a.id)}&source=deeplink&url=${encodeURIComponent(dest)}`;
    if (key === 'tcgplayer')
      return dest + (dest.includes('?') ? '&' : '?') + `partner=${encodeURIComponent(a.id)}`;
    return dest;  // eBay: fill in your Partner Network link format
  }
  function buyUrl(key, c) { return affiliate(key, MARKET[key].url(buyQuery(c))); }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // The default keyword badges (Once Per Turn etc.) follow the card's colour,
  // like the printed card. Returns [background, text] for the first colour.
  const CARD_COLOR = {
    Red:    ['#e13d3d', '#fff'],  // true card colours (match the card, not a tint)
    Blue:   ['#1f8fd6', '#fff'],
    Green:  ['#1f9d55', '#fff'],
    Purple: ['#9b59b6', '#fff'],
    Yellow: ['#f1c40f', '#4a2f10'],
    Black:  ['#2f2f2f', '#fff'],
  };
  function kwColors(cardColor) {
    const first = (cardColor || '').split(/[\/\s]+/)[0];
    return CARD_COLOR[first] || ['#1799d6', '#fff'];
  }

  // Format effect text like the printed card: keyword tags -> badges, {traits}
  // emphasised, and the [Trigger] clause on its own line.
  function formatCardText(text) {
    if (!text) return '';
    let t = esc(text).trim();
    t = t.replace(/\s*\[Trigger\]/gi, '\n<span class="cd-trigger"></span>[Trigger]');
    t = t.replace(/\[([^\]]+)\]/g, (m, kw) => {
      const low = kw.toLowerCase().trim();
      const cls = /trigger/.test(low) ? 'cd-kw cd-kw-trig'
                : /counter/.test(low) ? 'cd-kw cd-kw-counter'
                : /^(blocker|rush|double attack|banish)$/.test(low) ? 'cd-kw cd-kw-ability'
                : 'cd-kw';
      return `<span class="${cls}">${kw}</span>`;
    });
    t = t.replace(/\{([^}]+)\}/g, '<span class="cd-trait">$1</span>');
    return t.replace(/\n/g, '<br>');
  }

  function swapArt(el, url) {
    const main = document.getElementById('cdArtMain');
    if (main) main.src = url;
    el.parentNode.querySelectorAll('img').forEach(i => i.classList.remove('sel'));
    el.classList.add('sel');
  }

  const CSS = `
  .cd-modal{display:none;position:fixed;inset:0;z-index:2000;background:rgba(28,16,8,.72);
    backdrop-filter:blur(3px);align-items:center;justify-content:center;padding:20px;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  .cd-modal.open{display:flex}
  .cd-box{background:#fffaf0;border:3px solid #f6b93b;border-radius:18px;max-width:760px;width:100%;
    max-height:90vh;overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.4);display:grid;
    grid-template-columns:300px 1fr;color:#3a2a1e;text-align:left}
  .cd-img{background:#f7ead0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px}
  .cd-img img.main{max-width:100%;max-height:400px;border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.2)}
  .cd-thumbs{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:12px}
  .cd-thumbs img{width:44px;height:62px;object-fit:cover;border-radius:5px;cursor:pointer;
    border:2px solid transparent;box-shadow:0 2px 6px rgba(0,0,0,.15)}
  .cd-thumbs img:hover{border-color:#f6b93b}
  .cd-thumbs img.sel{border-color:#1799d6}
  .cd-altnote{font-size:10.5px;color:#9c8a76;margin-top:6px;text-align:center;width:100%}
  .cd-body{padding:22px 24px;position:relative}
  .cd-close{position:absolute;top:12px;right:14px;background:#fff;border:2px solid #ecdcc2;
    border-radius:50%;width:32px;height:32px;font-size:15px;cursor:pointer;line-height:1;color:#3a2a1e}
  .cd-box h2{font-family:'Fredoka',sans-serif;font-size:22px;margin:2px 40px 4px 0;color:#3a2a1e}
  .cd-sub{color:#9c8a76;font-size:12px;margin-bottom:14px}
  .cd-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
  .cd-chip{background:#f7ead0;border:1px solid #ecdcc2;border-radius:20px;padding:4px 11px;font-size:11.5px;font-weight:600}
  .cd-chip b{color:#0d6ea3}
  .cd-traits{font-size:12px;color:#3a2a1e;margin:-6px 0 14px}
  .cd-traits b{color:#9c8a76;font-weight:700}
  .cd-text{background:#fff;border:1px solid #ecdcc2;border-radius:10px;padding:12px 14px;
    font-size:12.5px;line-height:1.9;margin-bottom:16px}
  /* Default timing keywords (Once Per Turn, Activate:Main…) take the card's own
     colour via --cd-kw-bg (set per card in open()); the game-standard tags below
     keep their fixed meaning colours. */
  .cd-kw{display:inline-block;background:var(--cd-kw-bg,#1799d6);color:var(--cd-kw-fg,#fff);border-radius:5px;padding:0 6px;
    font-size:11px;font-weight:700;margin:0 3px 0 0;vertical-align:1px}
  .cd-kw-trig{background:#f6b93b;color:#4a2f10}
  .cd-kw-counter{background:#e23b3b;color:#fff}
  .cd-kw-ability{background:#e8820e;color:#fff}
  .cd-trait{font-weight:700;color:#c62828}
  .cd-trigger{display:block;margin-top:8px;padding-top:8px;border-top:1px dashed #ecdcc2}
  .cd-buyrow{display:flex;gap:10px;flex-wrap:wrap}
  .cd-buyrow a{flex:1;min-width:140px;text-decoration:none}
  .cd-buybtn{width:100%;border:none;border-radius:9px;padding:9px;font-size:12.5px;font-weight:700;color:#fff;cursor:pointer}
  .cd-region{color:#9c8a76;font-size:11px;margin-top:10px;text-align:center}
  .cd-region b{color:#3a2a1e}
  .cd-region a{color:#0d6ea3;font-weight:700;text-decoration:none;cursor:pointer}
  .cd-region a:hover{text-decoration:underline}
  .cd-buynote{color:#9c8a76;font-size:10.5px;margin-top:6px;text-align:center}
  @media (max-width:620px){
    .cd-modal{padding:0}
    .cd-box{grid-template-columns:1fr;max-width:100%;max-height:100vh;border-radius:0;border-width:0}
    .cd-img{padding:14px 14px 6px}
    .cd-img img.main{max-height:52vh}
    .cd-body{padding:18px 18px 30px}
    .cd-box h2{font-size:21px;margin-right:44px}
    .cd-sub{font-size:13px}
    .cd-chip{font-size:13px;padding:5px 12px}
    .cd-traits{font-size:13.5px}
    .cd-text{font-size:14.5px;line-height:1.85}
    .cd-kw{font-size:12.5px;padding:1px 7px}
    .cd-buybtn{padding:13px;font-size:14.5px}
    .cd-close{width:36px;height:36px;font-size:17px}
    .cd-region,.cd-buynote{font-size:12.5px}
  }
  `;

  let ready = false;
  function ensureDom() {
    if (ready) return;
    ready = true;
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);
    const modal = document.createElement('div');
    modal.className = 'cd-modal';
    modal.id = 'cdModal';
    modal.innerHTML = '<div class="cd-box" id="cdBox"></div>';
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    document.body.appendChild(modal);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  }

  function open(c) {
    if (!c) return;
    ensureDom();
    const arts = [c.image_url, ...(c.alt_arts || [])].filter(Boolean);
    let img;
    if (arts.length) {
      const thumbs = arts.length > 1
        ? `<div class="cd-thumbs">${arts.map((u, i) =>
             `<img src="${esc(u)}" class="${i === 0 ? 'sel' : ''}" loading="lazy"
                   onclick="CardDetail._swap(this,'${esc(u)}')" alt="art ${i + 1}">`).join('')}
           </div><div class="cd-altnote">${arts.length} versions — includes alternate art. Click to view.</div>`
        : '';
      img = `<img id="cdArtMain" class="main" src="${esc(arts[0])}" alt="${esc(c.name)}">${thumbs}`;
    } else {
      img = '<span style="color:#9c8a76">No image</span>';
    }

    const chips = [];
    if (c.rarity)     chips.push(`<span class="cd-chip"><b>Rarity</b> ${esc(c.rarity)}</span>`);
    if (c.card_type)  chips.push(`<span class="cd-chip"><b>Type</b> ${esc(c.card_type)}</span>`);
    if (c.card_color) chips.push(`<span class="cd-chip"><b>Color</b> ${esc(c.card_color)}</span>`);
    if (c.card_cost != null && c.card_cost !== '')   chips.push(`<span class="cd-chip"><b>Cost</b> ${esc(c.card_cost)}</span>`);
    if (c.card_power != null && c.card_power !== '') chips.push(`<span class="cd-chip"><b>Power</b> ${esc(c.card_power)}</span>`);
    if (c.attribute)  chips.push(`<span class="cd-chip"><b>Attribute</b> ${esc(c.attribute)}</span>`);
    if (c.counter != null && String(c.counter).replace(/[^0-9]/g, '') !== '' && Number(c.counter) > 0)
      chips.push(`<span class="cd-chip"><b>Counter</b> +${esc(Number(c.counter))}</span>`);
    if (c.life != null && c.life !== '') chips.push(`<span class="cd-chip"><b>Life</b> ${esc(c.life)}</span>`);

    const traits = (c.sub_types || '').trim();
    const traitsHtml = traits
      ? `<div class="cd-traits"><b>Traits:</b> ${esc(traits.replace(/\s*\/\s*/g, ' / '))}</div>` : '';

    const buys = Object.keys(MARKET).filter(key => MARKET[key].region === REGION).map(key => {
      const m = MARKET[key];
      return `<a href="${esc(buyUrl(key, c))}" target="_blank" rel="nofollow sponsored noopener">
        <button class="cd-buybtn" style="background:${m.color}">🛒 Buy on ${m.label}</button></a>`;
    }).join('');
    const here = REGION === 'id' ? '🇮🇩 Indonesia' : '🌍 International';
    const other = REGION === 'id' ? '🌍 International' : '🇮🇩 Indonesia';
    const regionBar = `<div class="cd-region">Showing <b>${here}</b> stores ·
      <a href="#" onclick="CardDetail.switchRegion();return false;">switch to ${other}</a></div>`;

    const box = document.getElementById('cdBox');
    const [kwBg, kwFg] = kwColors(c.card_color);
    box.style.setProperty('--cd-kw-bg', kwBg);
    box.style.setProperty('--cd-kw-fg', kwFg);
    box.innerHTML = `
      <div class="cd-img">${img}</div>
      <div class="cd-body">
        <button class="cd-close" onclick="CardDetail.close()" aria-label="Close">✕</button>
        <h2>${esc(c.name)}</h2>
        <div class="cd-sub">${esc(c.card_id)}${c.set_name ? ' · ' + esc(c.set_name) : ''}</div>
        <div class="cd-chips">${chips.join('')}</div>
        ${traitsHtml}
        ${c.card_text ? `<div class="cd-text">${formatCardText(c.card_text)}</div>` : ''}
        <div class="cd-buyrow">${buys}</div>
        ${regionBar}
        <div class="cd-buynote">Opens a marketplace search for this card. Prices vary by seller.</div>
      </div>`;
    _lastCard = c;
    document.getElementById('cdModal').classList.add('open');
  }

  function close() {
    const m = document.getElementById('cdModal');
    if (m) m.classList.remove('open');
  }

  // Flip Indonesia <-> International, remember it, and re-render the open card.
  function switchRegion() {
    REGION = (REGION === 'id') ? 'intl' : 'id';
    try { localStorage.setItem('gl_region', REGION); } catch (e) {}
    if (_lastCard) open(_lastCard);
  }

  return { open, close, esc, switchRegion, _swap: swapArt };
})();
