/* Shared card-detail modal — used by the Card Database (/database), Meta Decks
   (/meta) AND Market Watch (/market). Click a card anywhere -> CardDetail.open(cardObj).

   PRICES/CHART are OFF by default and shown ONLY when the caller opts in with
   CardDetail.open(card, {prices:true}) — Market Watch does this (prices are its
   whole point), while Database and Meta deliberately hide them (exact prices +
   history stay a Market-Watch / paid-tracker feature, not on the browse pages).

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
  let _lastOpts = {};

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

  // One Piece traits, for splitting OPTCGAPI's sub_types string. OPTCGAPI joins a
  // card's traits with spaces and NO delimiter ("Water Seven The Franky Family" =
  // Water Seven + The Franky Family), and a single trait can itself contain spaces
  // ("East Blue", "The Franky Family"), so they can't be split on spaces alone.
  // splitTraits() greedily matches the longest known trait from the front; any run
  // of unknown words is kept together as ONE trait (never shattered into words),
  // so an unlisted trait degrades to "whole", not "over-split". Vocab derived from
  // the full catalog; add new ones as sets ship.
  const TRAIT_PHRASES = new Set([
    'Accino Family', 'Alabasta', 'Alvida Pirates', 'Amazon Lily', 'Ancient Weapon', 'Animal',
    'Animal Kingdom Pirates', 'Arlong Pirates', 'Asuka Island', 'Baroque Works', 'Barto Club',
    'Baterilla', 'Beautiful Pirates', 'Bellamy Pirates', 'Big Mom Pirates', 'Biological Weapon',
    'Biologist', 'Black Cat Pirates', 'Blackbeard Pirates', 'Bluejam Pirates', 'Bonney Pirates',
    'Botanist', 'Bowin Island', 'Brownbeard Pirates', 'Buggy Pirates', 'Buggy\'s Delivery',
    'Caribou Pirates', 'Celestial Dragons', 'Charlotte Family', 'Cipher Pol', 'CP0', 'CP6', 'CP7',
    'CP8', 'CP9', 'Cross Guild', 'Crown Island', 'Donquixote Pirates', 'Drake Pirates',
    'Dressrosa', 'Drum Kingdom', 'East Blue', 'Egghead', 'Elbaph', 'Eldoraggo Crew', 'Enies Lobby',
    'Fallen Monk Pirates', 'Film', 'FILM', 'Fire Tank Pirates', 'Firetank Pirates', 'Fish-Man',
    'Fish-Man Island', 'Five Elders', 'Flevance', 'Flying Pirates', 'Foolshout Island',
    'Former Baroque Works', 'Former CP9', 'Former Navy', 'Former Rocks Pirates',
    'Former Roger Pirates', 'Former Rolling Pirates', 'Former Rumbar Pirates',
    'Former Whitebeard Pirates', 'Foxy Pirates', 'Frost Moon Village', 'Galley-La Company',
    'Gasparde Pirates', 'GERMA 66', 'Germa 66', 'Giant', 'Giant Warrior Pirates', 'Goa Kingdom',
    'God Valley', 'Golden Lion Pirates', 'Gyro Pirates', 'Happo Navy', 'Happosui Army',
    'Hawkins Pirates', 'Heart Pirates', 'Homies', 'Hot Springs Island', 'Ideo Pirates',
    'Impel Down', 'Jailer Beast', 'Jaya', 'Jaya Botanist', 'Journalist', 'Kano Country',
    'Kid Pirates', 'King of the Pirates', 'Kingdom of GERMA', 'Kingdom of Prodence',
    'Kouzuki Clan', 'Kozuki Clan', 'Krieg Pirates', 'Kuja Pirates', 'Kurozumi Clan',
    'Land of Wano', 'Long Ring Long Land', 'Lulucia Kingdom', 'Lunarian', 'Marine', 'Marineford',
    'Mary Geoise', 'Mecha Island', 'Merfolk', 'Mink', 'Minks', 'Mogaro Kingdom',
    'Monkey Mountain Alliance', 'Mountain Bandits', 'Muggy Kingdom', 'Music', 'Navy', 'Neo Navy',
    'Neptune Army', 'Neptunian', 'New Fish-Man Pirates', 'Nine Red Scabbards', 'Nox Pirates',
    'Numbers', 'ODYSSEY', 'Ohara', 'Omatsuri Island', 'On-Air Pirates', 'Pacifista',
    'Peachbeard Pirates', 'Pirate Crew', 'Plague', 'Punk', 'Punk Hazard', 'Red-Haired Pirates',
    'Revolutionary Army', 'Rocks Pirates', 'Roger Pirates', 'Rolling Pirates', 'Rumbar Pirates',
    'Sabaody', 'Science', 'Scientist', 'Seraphim', 'Seven Warlords of the Sea', 'Shandian Warrior',
    'Shipbuilding Town', 'Sky Island', 'Skypiea', 'SMILE', 'Sniper Island', 'Spade Pirates',
    'Straw Hat Crew', 'Sun Pirates', 'Supernovas', 'The Akazaya Nine', 'The Flying Fish Riders',
    'The Four Emperors', 'The Franky Family', 'The House of Lambs', 'The Moon',
    'The Moon Space Pirates', 'The Owner of Cindry\'s Shadow', 'The Pirates Fest',
    'The Victims\' Club', 'The Vinsmoke Family', 'Thriller Bark Pirates', 'Tontatta Tribe',
    'Treasure Pirates', 'Trump Pirates', 'Usopp Pirates', 'Vinsmoke', 'Vinsmoke Family', 'Wano',
    'Wano Country', 'Water Seven', 'Weevil\'s Mother', 'Whitebeard Pirates', 'Whole Cake Island',
    'Windmill Village', 'World Government', 'World Nobles', 'World Pirates', 'Yonta Maria Fleet',
    'Yonta Maria Grand Fleet', 'Zou',
  ]);
  let TRAIT_MAXWORDS = 1;
  TRAIT_PHRASES.forEach(p => { const n = p.split(' ').length; if (n > TRAIT_MAXWORDS) TRAIT_MAXWORDS = n; });
  // Bad-data tokens that sometimes leak into sub_types (power values, NULL, an
  // attribute) -- never shown as a trait.
  const TRAIT_JUNK = /^(\d+|\?|null|special)$/i;

  function splitTraits(s) {
    s = (s || '').trim();
    if (!s) return [];
    let parts;
    if (s.indexOf('/') >= 0) {
      parts = s.split('/').map(x => x.trim());
    } else {
      const toks = s.split(/\s+/);
      parts = [];
      let buf = [], i = 0;
      while (i < toks.length) {
        let hit = 0;
        for (let L = Math.min(TRAIT_MAXWORDS, toks.length - i); L >= 1; L--) {
          if (TRAIT_PHRASES.has(toks.slice(i, i + L).join(' '))) { hit = L; break; }
        }
        if (hit) {
          if (buf.length) { parts.push(buf.join(' ')); buf = []; }
          parts.push(toks.slice(i, i + hit).join(' ')); i += hit;
        } else { buf.push(toks[i]); i++; }
      }
      if (buf.length) parts.push(buf.join(' '));
    }
    return parts.filter(t => t && !TRAIT_JUNK.test(t));
  }

  function _fmtDay(d) {
    try { return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
    catch (e) { return d; }
  }
  // Tiny inline line chart of a printing's daily price history.
  function sparkline(points) {
    if (!points || points.length < 2)
      return `<div class="cd-chart-empty">Price history is still building (updated daily) — check back as it grows.</div>`;
    const W = 320, H = 112, padL = 40, padR = 8, padT = 10, padB = 20;
    const prices = points.map(p => p.price);
    let min = Math.min(...prices), max = Math.max(...prices);
    if (min === max) { min = min * 0.9; max = max * 1.1 || 1; }
    const n = points.length;
    const x = i => padL + (n === 1 ? 0 : i * (W - padL - padR) / (n - 1));
    const y = v => padT + (H - padT - padB) * (1 - (v - min) / (max - min));
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(' ');
    return `<svg viewBox="0 0 ${W} ${H}" class="cd-spark" preserveAspectRatio="none">
      <text x="4" y="${(y(max) + 3).toFixed(1)}" class="cd-ax">$${max.toFixed(2)}</text>
      <text x="4" y="${(y(min) + 3).toFixed(1)}" class="cd-ax">$${min.toFixed(2)}</text>
      <path class="cd-line" d="${path}"/>
      <text x="${padL}" y="${H - 5}" class="cd-ax">${esc(_fmtDay(points[0].date))}</text>
      <text x="${W - padR}" y="${H - 5}" class="cd-ax" text-anchor="end">${esc(_fmtDay(points[n - 1].date))}</text>
    </svg>`;
  }
  function renderChart(series) {
    const el = document.getElementById('cdChart');
    if (!el) return;
    if (!series || !series.length) { el.hidden = true; el.innerHTML = ''; return; }
    let idx = series.findIndex(s => s.is_base);
    if (idx < 0) idx = 0;
    const sel = series.length > 1
      ? `<select class="cd-chart-sel">${series.map((s, i) =>
          `<option value="${i}"${i === idx ? ' selected' : ''}>${esc(s.label)}</option>`).join('')}</select>` : '';
    el.hidden = false;
    el.innerHTML = `<div class="cd-chart-h">Price history ${sel}</div>
                    <div class="cd-chart-body">${sparkline(series[idx].points)}</div>`;
    const s = el.querySelector('.cd-chart-sel');
    if (s) s.onchange = () => { el.querySelector('.cd-chart-body').innerHTML = sparkline(series[+s.value].points); };
  }

  // Format effect text like the printed card: keyword tags -> badges, {traits}
  // emphasised, and the [Trigger] clause on its own line.
  function formatCardText(text) {
    // OPTCGAPI returns the literal string "NULL" for cards with no effect (e.g.
    // vanilla characters) -- treat that (and blanks) as no text, so the effect
    // box is hidden rather than showing "NULL".
    if (!text || String(text).trim().toUpperCase() === 'NULL') return '';
    let t = esc(text).trim();
    // OPTCGAPI drops the minus in the DON!! activation cost ("DON!! 2:" should be
    // "DON!! -2:"). That cost is always negative (you return DON), so restore it.
    t = t.replace(/DON!!\s*(\d+)\s*:/g, 'DON!! -$1:');
    t = t.replace(/\s*\[Trigger\]/gi, '\n<span class="cd-trigger"></span>[Trigger]');
    t = t.replace(/\[([^\]]+)\]/g, (m, kw) => {
      const low = kw.toLowerCase().trim();
      const cls = /don!!/.test(low) ? 'cd-kw cd-kw-don'
                : /once per/.test(low) ? 'cd-kw cd-kw-once'
                : /trigger/.test(low) ? 'cd-kw cd-kw-trig'
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
  .cd-modal{display:none;position:fixed;inset:0;z-index:2000;background:rgba(9,9,11,0.8);
    backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);align-items:center;justify-content:center;padding:32px;
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;}
  .cd-modal.open{display:flex;animation:modalFadeIn 0.2s ease;}
  @keyframes modalFadeIn { from { opacity: 0; } to { opacity: 1; } }

  .cd-box{background:#fafaf9;border-radius:24px;max-width:880px;width:100%;
    max-height:90vh;overflow:auto;box-shadow:0 25px 50px -12px rgba(0,0,0,0.5);display:grid;
    grid-template-columns:360px 1fr;color:#18181b;text-align:left;}

  .cd-img{background:#f4f4f5;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 32px;}
  .cd-img img.main{max-width:100%;max-height:440px;border-radius:12px;box-shadow:0 10px 25px -5px rgba(0,0,0,0.15);}
  .cd-thumbs{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:20px}
  .cd-thumbs img{width:48px;height:68px;object-fit:cover;border-radius:6px;cursor:pointer;
    border:2px solid transparent;box-shadow:0 2px 6px rgba(0,0,0,0.1);transition:all 0.2s;}
  .cd-thumbs img:hover{border-color:#a1a1aa;transform:translateY(-1px);}
  .cd-thumbs img.sel{border-color:#0ea5e9;}
  .cd-altnote{font-size:11px;color:#71717a;margin-top:12px;text-align:center;width:100%;font-weight:500;}

  .cd-body{padding:40px 48px 40px 32px;position:relative}
  .cd-close{position:absolute;top:24px;right:24px;background:#e4e4e7;border:none;
    border-radius:50%;width:32px;height:32px;font-size:14px;cursor:pointer;display:flex;
    align-items:center;justify-content:center;color:#52525b;transition:all 0.2s;}
  .cd-close:hover{background:#d4d4d8;color:#18181b;transform:scale(1.05);}

  .cd-box h2{font-family:'Fredoka',sans-serif;font-size:26px;margin:0 40px 6px 0;color:#18181b;line-height:1.2;font-weight:600;}
  .cd-sub{color:#71717a;font-size:13px;margin-bottom:20px;font-weight:500;}

  .cd-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
  .cd-chip{background:#e4e4e7;border-radius:6px;padding:6px 12px;font-size:12px;font-weight:500;color:#27272a;}
  .cd-chip b{color:#52525b;font-weight:600;margin-right:4px;}

  .cd-traits{font-size:13px;color:#18181b;margin:-8px 0 20px}
  .cd-traits b{color:#71717a;font-weight:600;}

  .cd-prices{background:#fff;border:1px solid #e4e4e7;border-radius:12px;padding:16px;margin-bottom:24px;box-shadow:0 1px 2px rgba(0,0,0,0.05);}
  .cd-prices-h{font-size:11px;font-weight:600;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px}
  .cd-price-row{display:flex;justify-content:space-between;align-items:center;font-size:14px;padding:6px 0}
  .cd-price-row + .cd-price-row{border-top:1px dashed #e4e4e7;margin-top:4px;}
  .cd-price-row em{color:#71717a;font-style:normal;font-size:12px;font-weight:500;margin-left:6px}
  .cd-price-row b{color:#18181b;font-weight:700;}
  .cd-prices-note{font-size:11px;color:#71717a;margin-top:12px;padding-top:12px;border-top:1px dashed #e4e4e7}

  .cd-chart{margin-bottom:24px}
  .cd-chart-h{font-size:11px;font-weight:600;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;
              margin-bottom:12px;display:flex;align-items:center;gap:12px}
  .cd-chart-sel{font-size:12px;padding:4px 8px;border:1px solid #e4e4e7;border-radius:6px;background:#fff;color:#18181b;cursor:pointer;font-family:'Inter',sans-serif;}
  .cd-spark{display:block;width:100%;height:120px;background:#fff;border:1px solid #e4e4e7;border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,0.05);}
  .cd-ax{fill:#a1a1aa;font-size:10px;font-weight:500;}
  .cd-line{fill:none;stroke:#0ea5e9;stroke-width:2;vector-effect:non-scaling-stroke}
  .cd-chart-empty{font-size:12px;color:#71717a;background:#fff;border:1px dashed #e4e4e7;border-radius:10px;padding:24px;text-align:center}

  .cd-text{background:#fff;border:1px solid #e4e4e7;border-radius:12px;padding:16px 20px;
    font-size:13px;line-height:1.7;margin-bottom:24px;box-shadow:0 1px 2px rgba(0,0,0,0.05);}

  .cd-kw{display:inline-block;background:#0ea5e9;color:#fff;border-radius:4px;padding:2px 6px;
    font-size:11px;font-weight:700;margin:0 4px 0 0;vertical-align:1px;text-transform:uppercase;letter-spacing:0.5px;}
  .cd-kw-trig{background:#f59e0b;color:#4a2f10;}
  .cd-kw-counter{background:#ef4444;color:#fff;}
  .cd-kw-ability{background:#f97316;color:#fff;}
  .cd-kw-don{background:#18181b;color:#fff;}
  .cd-kw-once{background:#d946ef;color:#fff;}
  .cd-trait{font-weight:600;color:#ef4444;}
  .cd-trigger{display:block;margin-top:12px;padding-top:12px;border-top:1px dashed #e4e4e7}

  .cd-buyrow{display:flex;gap:12px;flex-wrap:wrap;align-items:stretch}
  .cd-buyrow a{flex:1;min-width:160px;text-decoration:none;display:flex}
  .cd-buybtn{width:100%;border:none;border-radius:8px;padding:12px;font-size:13px;font-weight:600;color:#fff;cursor:pointer;transition:transform 0.2s, box-shadow 0.2s;
    display:flex;align-items:center;justify-content:center;text-align:center;line-height:1.3;min-height:48px;}
  .cd-buybtn:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,0.15);}

  .cd-region{color:#71717a;font-size:12px;margin-top:16px;text-align:center}
  .cd-region b{color:#18181b;font-weight:600;}
  .cd-region a{color:#0ea5e9;font-weight:600;text-decoration:none;cursor:pointer}
  .cd-region a:hover{text-decoration:underline;text-underline-offset:2px;}
  .cd-buynote{color:#a1a1aa;font-size:11px;margin-top:8px;text-align:center;font-style:italic;}

  @media (max-width:800px){
    .cd-modal{padding:16px}
    .cd-box{grid-template-columns:1fr;max-width:100%;max-height:100vh;}
    .cd-img{padding:24px 24px 16px}
    .cd-img img.main{max-height:50vh}
    .cd-body{padding:24px}
    .cd-box h2{font-size:24px;}
    .cd-buybtn{font-size:14px;padding:14px;}
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

  function open(c, opts) {
    if (!c) return;
    opts = opts || {};
    const showPrices = !!opts.prices;  // prices + history chart are opt-in (Market Watch only)
    ensureDom();
    // Build the art strip: base image, then each priced variant's image, then
    // gallery alt-arts. De-duped by PRINTING (the "_pN" suffix, or "base" when
    // absent) rather than by URL, because the same artwork often arrives under
    // two different URLs -- OPTCGAPI's hosted copy (..._p1_xxxx.jpg) and our own
    // downloaded copy (/assets/alt/..._p1.jpg) -- which would otherwise show the
    // same picture twice (the "3 versions but 2 are identical" bug).
    const arts = [];
    const _seen = new Set();
    const _key = u => { const m = String(u).match(/_p(\d+)/i); return m ? 'p' + m[1] : 'base'; };
    const _push = u => { if (u) { const k = _key(u); if (!_seen.has(k)) { _seen.add(k); arts.push(u); } } };
    _push(c.image_url);
    (c.variants || []).forEach(v => { if (!v.is_base) _push(v.image); });
    (c.alt_arts || []).forEach(_push);
    // Broken thumbnails hide themselves rather than showing a broken-image icon.
    const onerr = "this.style.display='none'";
    let img;
    if (arts.length) {
      const thumbs = arts.length > 1
        ? `<div class="cd-thumbs">${arts.map((u, i) =>
             `<img src="${esc(u)}" class="${i === 0 ? 'sel' : ''}" loading="lazy" onerror="${onerr}"
                   onclick="CardDetail._swap(this,'${esc(u)}')" alt="art ${i + 1}">`).join('')}
           </div><div class="cd-altnote">${arts.length} versions — includes alternate art. Click to view.</div>`
        : '';
      img = `<img id="cdArtMain" class="main" src="${esc(arts[0])}" alt="${esc(c.name)}"
                  onerror="this.style.display='none'">${thumbs}`;
    } else {
      img = '<span style="color:#71717a">No image</span>';
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

    const traitList = splitTraits(c.sub_types);
    const traitsHtml = traitList.length
      ? `<div class="cd-traits"><b>Traits:</b> ${traitList.map(esc).join(' / ')}</div>` : '';

    // Effect text -- empty (incl. OPTCGAPI's "NULL") hides the box entirely.
    const cardTextInner = formatCardText(c.card_text);

    // Per-printing prices (base + alt-art/parallel), each with its own value.
    // We can show more artworks than we have prices for (images and prices come
    // from different sources), so note that when it happens.
    const variants = Array.isArray(c.variants) ? c.variants : [];
    const moreArts = arts.length > variants.length;
    const pricesHtml = (showPrices && variants.length) ? `<div class="cd-prices">
      <div class="cd-prices-h">Market price (USD)</div>
      ${variants.map(v => `<div class="cd-price-row">
        <span>${esc(v.label || 'Base')}${v.rarity ? ` <em>${esc(v.rarity)}</em>` : ''}</span>
        <b>$${Number(v.price).toFixed(2)}</b></div>`).join('')}
      ${moreArts ? `<div class="cd-prices-note">Prices shown only for versions with market data.</div>` : ''}
    </div>` : '';

    const buys = Object.keys(MARKET).filter(key => MARKET[key].region === REGION).map(key => {
      const m = MARKET[key];
      return `<a href="${esc(buyUrl(key, c))}" target="_blank" rel="nofollow sponsored noopener">
        <button class="cd-buybtn" style="background:${m.color}">🛒 Buy on ${m.label}</button></a>`;
    }).join('');
    const here = REGION === 'id' ? '🇮🇩 Indonesia' : '🌍 International';
    const other = REGION === 'id' ? '🌍 International' : '🇮🇩 Indonesia';
    const regionBar = `<div class="cd-region">Showing <b>${here}</b> stores ·
      <a href="#" onclick="CardDetail.switchRegion();return false;">switch to ${other}</a></div>`;

    document.getElementById('cdBox').innerHTML = `
      <div class="cd-img">${img}</div>
      <div class="cd-body">
        <button class="cd-close" onclick="CardDetail.close()" aria-label="Close">✕</button>
        <h2>${esc(c.name)}</h2>
        <div class="cd-sub">${esc(c.card_id)}${c.set_name ? ' · ' + esc(c.set_name) : ''}</div>
        <div class="cd-chips">${chips.join('')}</div>
        ${traitsHtml}
        ${pricesHtml}
        ${showPrices ? '<div class="cd-chart" id="cdChart" hidden></div>' : ''}
        ${cardTextInner ? `<div class="cd-text">${cardTextInner}</div>` : ''}
        <div class="cd-buyrow">${buys}</div>
        ${regionBar}
        <div class="cd-buynote">Opens a marketplace search for this card. Prices vary by seller.</div>
      </div>`;
    _lastCard = c;
    _lastOpts = opts;
    document.getElementById('cdModal').classList.add('open');

    // Load the price-history chart (async, non-blocking) — only when prices are shown.
    if (showPrices && c.card_id) {
      fetch('/api/price_history', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_id: c.card_id }),
      }).then(r => r.json())
        .then(d => { if (_lastCard === c) renderChart((d && d.series) || []); })
        .catch(() => {});
    }
  }

  function close() {
    const m = document.getElementById('cdModal');
    if (m) m.classList.remove('open');
  }

  // Flip Indonesia <-> International, remember it, and re-render the open card.
  function switchRegion() {
    REGION = (REGION === 'id') ? 'intl' : 'id';
    try { localStorage.setItem('gl_region', REGION); } catch (e) {}
    if (_lastCard) open(_lastCard, _lastOpts);
  }

  return { open, close, esc, switchRegion, _swap: swapArt };
})();
