// ==UserScript==
// @name         Spotify Web - Liked Songs Sorter (DOM Scan)
// @namespace    josh.spotify.liked-sorter
// @version      0.3.0
// @description  Adds full-library sorting/search to Spotify Web Player Liked Songs and plays tracks without leaving the sorter.
// @match        https://open.spotify.com/*
// @homepageURL  https://shdwolf579.github.io/things-i-made.html
// @downloadURL  https://shdwolf579.github.io/spotify-liked-sorter.user.js
// @updateURL    https://shdwolf579.github.io/spotify-liked-sorter.user.js
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(() => {
    'use strict';

    const APP = 'josh-liked-sorter';
    const CACHE_KEY = `${APP}:domtracks:v2`;
    const CACHE_TIME_KEY = `${APP}:domtracks-time:v2`;

    let tracks = [];
    let visibleTracks = [];
    let sortMode = 'artist-asc';
    let filterText = '';
    let scanning = false;

    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => [...r.querySelectorAll(s)];
    const sleep = ms => new Promise(r => setTimeout(r, ms));

    function esc(v) {
        return String(v ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function injectCss() {
        if ($(`#${APP}-style`)) return;

        const st = document.createElement('style');
        st.id = `${APP}-style`;
        st.textContent = `
            #${APP}-button {
                position: fixed;
                right: 24px;
                bottom: 92px;
                z-index: 2147483646;
                border: 0;
                border-radius: 999px;
                padding: 10px 16px;
                background: #1ed760;
                color: #000;
                font: 700 14px/1 Arial,sans-serif;
                cursor: pointer;
                box-shadow: 0 4px 18px rgba(0,0,0,.45);
            }
            #${APP}-overlay {
                position: fixed;
                inset: 64px 10px 92px 10px;
                z-index: 2147483645;
                display: none;
                flex-direction: column;
                background: #121212;
                color: #fff;
                border: 1px solid #2b2b2b;
                border-radius: 10px;
                overflow: hidden;
                box-shadow: 0 10px 35px rgba(0,0,0,.7);
                font-family: Arial,sans-serif;
            }
            #${APP}-overlay.open { display:flex; }

            #${APP}-toolbar {
                display:flex;
                gap:8px;
                align-items:center;
                flex-wrap:wrap;
                padding:10px 12px;
                background:#181818;
                border-bottom:1px solid #292929;
            }
            #${APP}-title { font-size:18px; font-weight:800; }
            #${APP}-count { color:#b3b3b3; font-size:13px; margin-right:auto; }

            #${APP}-toolbar input,
            #${APP}-toolbar select,
            #${APP}-toolbar button {
                border:1px solid #3b3b3b;
                background:#252525;
                color:#fff;
                border-radius:7px;
                padding:8px 9px;
                font-size:13px;
            }
            #${APP}-toolbar button { cursor:pointer; }
            #${APP}-toolbar button:hover { background:#333; }
            #${APP}-toolbar button.primary {
                background:#1ed760;
                color:#000;
                border-color:#1ed760;
                font-weight:700;
            }
            #${APP}-target { width:82px; }
            #${APP}-search { width:240px; }

            #${APP}-status {
                display:none;
                padding:9px 12px;
                background:#151515;
                border-bottom:1px solid #242424;
                color:#b3b3b3;
                font-size:13px;
            }
            #${APP}-status.show { display:block; }

            #${APP}-wrap { flex:1; overflow:auto; min-height:0; }

            #${APP}-head,
            .${APP}-row {
                display:grid;
                grid-template-columns:minmax(330px,2.2fr) minmax(220px,1.4fr) minmax(180px,1.1fr) 120px 62px;
                gap:12px;
                align-items:center;
                padding:8px 14px;
            }

            #${APP}-head {
                position:sticky;
                top:0;
                z-index:2;
                background:#181818;
                color:#b3b3b3;
                border-bottom:1px solid #2b2b2b;
                font-size:12px;
                font-weight:700;
                text-transform:uppercase;
            }

            .${APP}-row {
                min-height:42px;
                border-bottom:1px solid #1d1d1d;
                font-size:13px;
            }
            .${APP}-row:hover { background:#242424; }

            .${APP}-titleline {
                color:#fff;
                font-weight:600;
                white-space:nowrap;
                overflow:hidden;
                text-overflow:ellipsis;
            }
            .${APP}-titleline {
                display:flex;
                align-items:center;
                gap:8px;
            }
            .${APP}-play,
            .${APP}-songlink {
                border:0;
                background:transparent;
                padding:0;
                cursor:pointer;
                font:inherit;
            }
            .${APP}-play {
                color:#1ed760;
                font-size:15px;
                flex:0 0 auto;
            }
            .${APP}-play:hover {
                transform:scale(1.12);
            }
            .${APP}-songlink {
                color:#fff;
                font-weight:600;
                white-space:nowrap;
                overflow:hidden;
                text-overflow:ellipsis;
                text-align:left;
                min-width:0;
            }
            .${APP}-songlink:hover {
                color:#1ed760;
                text-decoration:underline;
            }
            .${APP}-artist-sub,
            .${APP}-muted {
                color:#b3b3b3;
                white-space:nowrap;
                overflow:hidden;
                text-overflow:ellipsis;
            }
            .${APP}-empty {
                padding:40px;
                text-align:center;
                color:#b3b3b3;
            }
            #${APP}-help {
                color:#8f8f8f;
                font-size:12px;
            }

            @media(max-width:1000px){
                #${APP}-head,.${APP}-row {
                    grid-template-columns:minmax(300px,2fr) minmax(180px,1.2fr) 110px 60px;
                }
                .${APP}-artist-col { display:none; }
            }
        `;
        document.head.appendChild(st);
    }

    function detectTargetCount() {
        const heading = $$('h1, [data-testid="entityTitle"]').find(el =>
            /liked songs/i.test(el.textContent || '')
        );

        const scope = heading?.closest('main, section, div') || document;
        const texts = [
            heading?.parentElement?.textContent || '',
            scope.textContent || '',
            document.body.textContent || ''
        ];

        for (const txt of texts) {
            const m = txt.match(/([\d,]{2,})\s+songs\b/i);
            if (m) {
                const n = Number(m[1].replaceAll(',', ''));
                if (Number.isFinite(n) && n > 0 && n < 100000) return n;
            }
        }
        return 0;
    }

    function buildUi() {
        if ($(`#${APP}-button`)) return;

        const btn = document.createElement('button');
        btn.id = `${APP}-button`;
        btn.textContent = 'Liked Sort';
        btn.addEventListener('click', openOverlay);
        document.body.appendChild(btn);

        const o = document.createElement('div');
        o.id = `${APP}-overlay`;
        o.innerHTML = `
            <div id="${APP}-toolbar">
                <div id="${APP}-title">Liked Songs Sorter</div>
                <div id="${APP}-count">Not scanned</div>

                <label id="${APP}-help">Target:
                    <input id="${APP}-target" type="number" min="1" step="1" placeholder="songs">
                </label>

                <button id="${APP}-scan" class="primary">Scan library</button>

                <input id="${APP}-search" type="search"
                    placeholder="Search title, artist, album..." autocomplete="off">

                <select id="${APP}-sort">
                    <option value="artist-asc">Artist A → Z</option>
                    <option value="artist-desc">Artist Z → A</option>
                    <option value="title-asc">Title A → Z</option>
                    <option value="title-desc">Title Z → A</option>
                    <option value="album-asc">Album A → Z</option>
                    <option value="album-desc">Album Z → A</option>
                    <option value="date-desc">Date added: newest</option>
                    <option value="date-asc">Date added: oldest</option>
                </select>

                <button id="${APP}-clear">Clear cache</button>
                <button id="${APP}-close">Close</button>
            </div>

            <div id="${APP}-status"></div>

            <div id="${APP}-wrap">
                <div id="${APP}-head">
                    <div>Title / Artist</div>
                    <div>Album</div>
                    <div class="${APP}-artist-col">Artist</div>
                    <div>Date added</div>
                    <div>Time</div>
                </div>
                <div id="${APP}-rows"></div>
            </div>
        `;
        document.body.appendChild(o);

        $(`#${APP}-close`).onclick = closeOverlay;
        $(`#${APP}-scan`).onclick = () => scanLibrary();
        $(`#${APP}-clear`).onclick = () => {
            localStorage.removeItem(CACHE_KEY);
            localStorage.removeItem(CACHE_TIME_KEY);
            tracks = [];
            applySortAndFilter();
            setStatus('Cache cleared. Open Liked Songs and scan again.');
        };
        $(`#${APP}-search`).oninput = e => {
            filterText = e.target.value.trim().toLowerCase();
            applySortAndFilter();
        };
        $(`#${APP}-sort`).onchange = e => {
            sortMode = e.target.value;
            applySortAndFilter();
        };

        $(`#${APP}-rows`).addEventListener('click', e => {
            const btn = e.target.closest(`button[data-track-id]`);
            if (!btn) return;

            const id = btn.getAttribute('data-track-id');
            const track = tracks.find(t => t.id === id);
            if (track) playTrackInPlace(track);
        });

        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && $(`#${APP}-overlay`)?.classList.contains('open')) {
                closeOverlay();
            }
        });

        loadCache();
    }

    function setStatus(msg = '', error = false) {
        const el = $(`#${APP}-status`);
        if (!el) return;
        el.textContent = msg;
        el.style.color = error ? '#ff7777' : '#b3b3b3';
        el.classList.toggle('show', Boolean(msg));
    }

    function loadCache() {
        try {
            const raw = localStorage.getItem(CACHE_KEY);
            if (!raw) return;
            const data = JSON.parse(raw);
            if (!Array.isArray(data) || !data.length) return;
            tracks = data;
            applySortAndFilter();
            const ts = Number(localStorage.getItem(CACHE_TIME_KEY) || 0);
            if (ts) {
                setStatus(`Loaded cached scan from ${new Date(ts).toLocaleString()}. Hit Scan library to refresh.`);
            }
        } catch (e) {
            console.warn('[Liked Sort] cache read failed', e);
        }
    }

    function saveCache() {
        try {
            localStorage.setItem(CACHE_KEY, JSON.stringify(tracks));
            localStorage.setItem(CACHE_TIME_KEY, String(Date.now()));
        } catch (e) {
            console.warn('[Liked Sort] cache write failed', e);
        }
    }

    function isLikedSongsPage() {
        return location.pathname === '/collection/tracks'
            || location.pathname.startsWith('/collection/tracks/');
    }

    function findScrollContainer() {
        const row = document.querySelector(
            'div[role="row"][aria-rowindex], [data-testid="tracklist-row"]'
        );

        if (row) {
            let p = row.parentElement;
            while (p && p !== document.body) {
                const cs = getComputedStyle(p);
                if (
                    p.scrollHeight > p.clientHeight + 50 &&
                    /(auto|scroll)/.test(cs.overflowY)
                ) {
                    return p;
                }
                p = p.parentElement;
            }
        }

        const selectors = [
            '.main-view-container',
            '.Root__main-view',
            '[data-overlayscrollbars-viewport]',
            '.os-viewport',
            '.os-content'
        ];

        for (const s of selectors) {
            for (const el of $$(s)) {
                if (el.scrollHeight > el.clientHeight + 50) return el;
            }
        }

        return document.scrollingElement || document.documentElement;
    }

    function rowCells(row) {
        return $$('[role="gridcell"]', row).map(c => (c.innerText || '').trim());
    }

    function parseVisibleRows(map) {
        const rows = $$('div[role="row"][aria-rowindex], [data-testid="tracklist-row"]');
        let added = 0;

        for (const row of rows) {
            const trackAnchor =
                row.querySelector('a[data-testid="internal-track-link"]') ||
                row.querySelector('a[href*="/track/"]');

            if (!trackAnchor) continue;

            const href = trackAnchor.getAttribute('href') || '';
            const idMatch = href.match(/\/track\/([A-Za-z0-9]+)/);
            const id = idMatch?.[1] || href;
            if (!id) continue;

            const title =
                (trackAnchor.querySelector('div')?.textContent ||
                 trackAnchor.textContent || '').trim();
            if (!title) continue;

            const artistLinks = $$('a[href*="/artist/"]', row);
            const artists = [...new Set(
                artistLinks.map(a => (a.textContent || '').trim()).filter(Boolean)
            )];

            const albumAnchor = row.querySelector('a[href*="/album/"]');
            const album = (albumAnchor?.textContent || '').trim();

            const cells = rowCells(row);
            const fullText = cells.join(' | ');

            const duration =
                (fullText.match(/\b\d{1,2}:\d{2}\b/g) || []).at(-1) || '';

            let dateAdded = '';
            for (const c of cells) {
                if (
                    /\b(today|yesterday|\d+\s+(day|week|month|year)s?\s+ago)\b/i.test(c) ||
                    /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/i.test(c) ||
                    /\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/.test(c)
                ) {
                    dateAdded = c;
                }
            }

            const rowIndex = Number(row.getAttribute('aria-rowindex') || 0) || null;

            const existing = map.get(id);
            const data = {
                id,
                href: href.startsWith('http') ? href : `https://open.spotify.com${href}`,
                title,
                artists,
                artistText: artists.join(', '),
                album,
                dateAdded,
                duration,
                rowIndex
            };

            if (!existing) {
                map.set(id, data);
                added++;
            } else {
                map.set(id, {
                    ...existing,
                    ...Object.fromEntries(
                        Object.entries(data).filter(([, v]) =>
                            v !== '' && v !== null && !(Array.isArray(v) && !v.length)
                        )
                    )
                });
            }
        }

        return added;
    }

    async function scanLibrary() {
        if (scanning) return;

        if (!isLikedSongsPage()) {
            setStatus('Open Spotify → Liked Songs first, then hit Scan library.', true);
            return;
        }

        scanning = true;
        const scanBtn = $(`#${APP}-scan`);
        scanBtn.disabled = true;
        scanBtn.textContent = 'Scanning…';

        try {
            let target = Number($(`#${APP}-target`).value || 0);
            if (!target) {
                target = detectTargetCount();
                if (target) $(`#${APP}-target`).value = target;
            }

            const container = findScrollContainer();
            const oldTop = container.scrollTop || 0;
            const map = new Map();

            setStatus('Scanning your Liked Songs from the page itself — no Spotify API token needed.');

            container.scrollTop = 0;
            await sleep(500);

            let stuck = 0;
            let lastCount = -1;
            let lastTop = -1;
            const maxLoops = 1500;

            for (let i = 0; i < maxLoops; i++) {
                parseVisibleRows(map);

                const count = map.size;
                const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
                const top = container.scrollTop;

                setStatus(
                    target
                        ? `Scanning… ${count.toLocaleString()} / ${target.toLocaleString()} songs`
                        : `Scanning… ${count.toLocaleString()} songs found`
                );

                if (target && count >= target) break;

                const atEnd = top >= maxTop - 5;

                if (count === lastCount && Math.abs(top - lastTop) < 2) {
                    stuck++;
                } else {
                    stuck = 0;
                }

                if (atEnd && stuck >= 5) {
                    await sleep(700);
                    parseVisibleRows(map);
                    break;
                }

                lastCount = count;
                lastTop = top;

                const step = Math.max(320, Math.floor(container.clientHeight * 0.82));
                container.scrollTop = Math.min(maxTop, top + step);

                await sleep(120);
            }

            await sleep(350);
            parseVisibleRows(map);

            tracks = [...map.values()];
            tracks.sort((a, b) => (a.rowIndex ?? 999999) - (b.rowIndex ?? 999999));
            saveCache();
            applySortAndFilter();

            container.scrollTop = oldTop;

            if (!tracks.length) {
                setStatus(
                    'I could not see Spotify track rows. Make sure Liked Songs is open and the song list is visible.',
                    true
                );
            } else if (target && tracks.length < target) {
                setStatus(
                    `Scan finished with ${tracks.length.toLocaleString()} / ${target.toLocaleString()} songs. ` +
                    `Hit Scan library again if Spotify had not finished rendering everything.`
                );
            } else {
                setStatus(`Done — ${tracks.length.toLocaleString()} songs scanned. Sort/search away 😎`);
            }
        } catch (e) {
            console.error('[Liked Sort]', e);
            setStatus(`Scan failed: ${e.message}`, true);
        } finally {
            scanning = false;
            scanBtn.disabled = false;
            scanBtn.textContent = 'Scan library';
        }
    }

    function renderedTrackRow(trackId) {
        const anchors = $$(`a[href*="/track/${CSS.escape(trackId)}"]`);
        for (const a of anchors) {
            const row =
                a.closest('div[role="row"][aria-rowindex]') ||
                a.closest('[data-testid="tracklist-row"]');
            if (row) return row;
        }
        return null;
    }

    function visibleIndexedRows() {
        return $$('div[role="row"][aria-rowindex]')
            .map(row => ({
                row,
                index: Number(row.getAttribute('aria-rowindex') || 0),
                top: row.getBoundingClientRect().top
            }))
            .filter(x => Number.isFinite(x.index) && x.index > 0)
            .sort((a,b) => a.index - b.index);
    }

    function estimateRowStep(indexed) {
        if (indexed.length >= 2) {
            for (let i = 1; i < indexed.length; i++) {
                const di = indexed[i].index - indexed[i-1].index;
                const dp = indexed[i].top - indexed[i-1].top;
                if (di > 0 && dp > 5) {
                    const step = dp / di;
                    if (step > 20 && step < 120) return step;
                }
            }
        }
        return 56;
    }

    function triggerSpotifyPlay(row) {
        const playButton =
            row.querySelector('button[data-testid="play-button"]') ||
            row.querySelector('button[aria-label^="Play "]') ||
            row.querySelector('button[aria-label="Play"]');

        if (playButton) {
            playButton.click();
            return true;
        }

        row.dispatchEvent(new MouseEvent('dblclick', {
            bubbles: true,
            cancelable: true,
            view: window,
            detail: 2,
            button: 0
        }));
        return true;
    }

    async function playTrackInPlace(track) {
        if (!track?.id) return;

        if (!isLikedSongsPage()) {
            setStatus('Playback control only works while Spotify Liked Songs is open underneath the sorter.', true);
            return;
        }

        setStatus(`Playing “${track.title}”…`);

        const container = findScrollContainer();
        const startingTop = container.scrollTop || 0;

        let row = renderedTrackRow(track.id);
        if (row) {
            triggerSpotifyPlay(row);
            setStatus(`Playing “${track.title}” — sorter stays open 😎`);
            return;
        }

        const targetIndex = Number(track.rowIndex || 0);

        for (let attempt = 0; attempt < 18; attempt++) {
            const indexed = visibleIndexedRows();

            if (targetIndex && indexed.length) {
                const ref = indexed[Math.floor(indexed.length / 2)];
                const step = estimateRowStep(indexed);

                const wanted =
                    container.scrollTop +
                    (targetIndex - ref.index) * step;

                const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
                container.scrollTop = Math.max(0, Math.min(maxTop, wanted));
            } else {
                const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
                container.scrollTop = Math.min(maxTop, container.scrollTop + container.clientHeight * 0.9);
            }

            await sleep(attempt < 4 ? 120 : 180);

            row = renderedTrackRow(track.id);
            if (row) {
                triggerSpotifyPlay(row);
                setStatus(`Playing “${track.title}” — sorter stays open 😎`);
                return;
            }
        }

        container.scrollTop = startingTop;
        setStatus(
            `Couldn’t render “${track.title}” in Spotify’s hidden list. Hit Scan library once more and try it again.`,
            true
        );
    }

    function cmp(a, b) {
        return String(a || '').localeCompare(
            String(b || ''), undefined,
            { sensitivity:'base', numeric:true }
        );
    }

    function parseDateText(s) {
        if (!s) return 0;
        const now = Date.now();
        const t = s.trim().toLowerCase();

        if (t === 'today') return now;
        if (t === 'yesterday') return now - 86400000;

        let m = t.match(/(\d+)\s+day/);
        if (m) return now - Number(m[1]) * 86400000;
        m = t.match(/(\d+)\s+week/);
        if (m) return now - Number(m[1]) * 7 * 86400000;
        m = t.match(/(\d+)\s+month/);
        if (m) return now - Number(m[1]) * 30 * 86400000;
        m = t.match(/(\d+)\s+year/);
        if (m) return now - Number(m[1]) * 365 * 86400000;

        const parsed = Date.parse(s);
        return Number.isNaN(parsed) ? 0 : parsed;
    }

    function sorted(data) {
        const out = [...data];

        switch (sortMode) {
            case 'artist-desc':
                return out.sort((a,b) => cmp(b.artistText,a.artistText) || cmp(b.title,a.title));
            case 'title-asc':
                return out.sort((a,b) => cmp(a.title,b.title) || cmp(a.artistText,b.artistText));
            case 'title-desc':
                return out.sort((a,b) => cmp(b.title,a.title) || cmp(b.artistText,a.artistText));
            case 'album-asc':
                return out.sort((a,b) => cmp(a.album,b.album) || cmp(a.title,b.title));
            case 'album-desc':
                return out.sort((a,b) => cmp(b.album,a.album) || cmp(b.title,a.title));
            case 'date-asc':
                return out.sort((a,b) => parseDateText(a.dateAdded) - parseDateText(b.dateAdded));
            case 'date-desc':
                return out.sort((a,b) => parseDateText(b.dateAdded) - parseDateText(a.dateAdded));
            case 'artist-asc':
            default:
                return out.sort((a,b) => cmp(a.artistText,b.artistText) || cmp(a.title,b.title));
        }
    }

    function applySortAndFilter() {
        let data = tracks;

        if (filterText) {
            data = data.filter(t =>
                `${t.title} ${t.artistText} ${t.album}`.toLowerCase().includes(filterText)
            );
        }

        visibleTracks = sorted(data);
        render();
    }

    function render() {
        const rows = $(`#${APP}-rows`);
        const count = $(`#${APP}-count`);
        if (!rows || !count) return;

        count.textContent = tracks.length
            ? (filterText
                ? `${visibleTracks.length.toLocaleString()} shown / ${tracks.length.toLocaleString()} scanned`
                : `${tracks.length.toLocaleString()} scanned`)
            : 'Not scanned';

        if (!visibleTracks.length) {
            rows.innerHTML = `<div class="${APP}-empty">${
                tracks.length ? 'No matches.' : 'Open Liked Songs and hit “Scan library”.'
            }</div>`;
            return;
        }

        rows.innerHTML = visibleTracks.map(t => `
            <div class="${APP}-row">
                <div>
                    <div class="${APP}-titleline">
                        <button
                            type="button"
                            class="${APP}-play"
                            data-track-id="${esc(t.id)}"
                            title="Play ${esc(t.title)}"
                        >▶</button>
                        <button
                            type="button"
                            class="${APP}-songlink"
                            data-track-id="${esc(t.id)}"
                            title="Play ${esc(t.title)}"
                        >${esc(t.title)}</button>
                    </div>
                    <div class="${APP}-artist-sub" title="${esc(t.artistText)}">${esc(t.artistText)}</div>
                </div>
                <div class="${APP}-muted" title="${esc(t.album)}">${esc(t.album)}</div>
                <div class="${APP}-muted ${APP}-artist-col" title="${esc(t.artistText)}">${esc(t.artistText)}</div>
                <div class="${APP}-muted">${esc(t.dateAdded)}</div>
                <div class="${APP}-muted">${esc(t.duration)}</div>
            </div>
        `).join('');
    }

    function openOverlay() {
        $(`#${APP}-overlay`).classList.add('open');

        const target = detectTargetCount();
        const input = $(`#${APP}-target`);
        if (target && !input.value) input.value = target;

        applySortAndFilter();
    }

    function closeOverlay() {
        $(`#${APP}-overlay`).classList.remove('open');
    }

    function init() {
        injectCss();
        buildUi();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once:true });
    } else {
        init();
    }
})();
