// ==UserScript==
// @name         Spotify Web - Liked Songs Sorter
// @namespace    josh.spotify.liked-sorter
// @version      0.8.8
// @description  Full-library Spotify Liked Songs sorter/search with sorted-view playback queue support.
// @match        https://open.spotify.com/*
// @homepageURL  https://shdwolf579.github.io/things-i-made.html
// @downloadURL  https://shdwolf579.github.io/spotify-liked-sorter.user.js
// @updateURL    https://shdwolf579.github.io/spotify-liked-sorter.user.js
// @run-at       document-start
// @inject-into  page
// @grant        none
// ==/UserScript==

(() => {
    'use strict';

    const APP_ID = 'josh-liked-sorter';
    const CACHE_KEY = `${APP_ID}:tracks:v6`;
    const CACHE_TIME_KEY = `${APP_ID}:tracks-time:v6`;
    const CACHE_TTL_MS = 6 * 60 * 60 * 1000;

    const GQL_URL = 'https://api-partner.spotify.com/pathfinder/v2/query';
    const FETCH_LIBRARY_TRACKS_FALLBACK_HASH =
        '087278b20b743578a6262c2b0b4bcd20d879c503cc359a2285baf083ef944240';

    let spotifyBearer = '';
    let liveFetchLibraryTracksHash = '';

    // Spotify changed the native web-player Play command shape in the Aug 24
    // deployment. Capture one real Play command and clone that exact structure
    // for sorter playback instead of guessing private Connect fields.
    let nativePlayCommandTemplate = null;
    let sorterNativePlayDispatchDepth = 0;
    const NATIVE_PLAY_TEMPLATE_KEY = `${APP_ID}:native-play-template:v2`;

    try {
        const cached = sessionStorage.getItem(NATIVE_PLAY_TEMPLATE_KEY);
        if (cached) nativePlayCommandTemplate = JSON.parse(cached);
    } catch {}

    // Seed from the exact native Play payload captured from Spotify Web on
    // 2026-08-24. Any later real Spotify Play request automatically replaces this
    // template, so the script follows future web-player deployments instead of
    // freezing this private payload forever.
    if (!nativePlayCommandTemplate) {
        nativePlayCommandTemplate = {
            context: {
                uri: 'spotify:playlist:37i9dQZF1F5p3rmiWPIYgZ',
                url: 'context://spotify:playlist:37i9dQZF1F5p3rmiWPIYgZ',
                metadata: {}
            },
            play_origin: {
                feature_identifier: 'your_library',
                feature_version: 'web-player_2026-08-24_1787587600631_development',
                referrer_identifier: 'your_library'
            },
            options: {
                license: 'tft',
                skip_to: {},
                player_options_override: {}
            },
            logging_params: {
                page_instance_ids: [],
                interaction_ids: [],
                command_id: ''
            },
            endpoint: 'play'
        };
    }

    // Spotify Connect command details are captured from the web player's own
    // playback requests. We reuse that exact active device route/auth when
    // installing the sorter's explicit next/previous queue.
    let connectCommandUrl = '';
    let connectCommandAuth = '';
    let connectClientToken = '';
    let connectDeviceId = '';
    let connectSpClientBase = '';

    const SORTER_QUEUE_LOOKAHEAD = 80;
    const SORTER_QUEUE_HISTORY = 10;

    let sorterSequence = [];
    let sorterSequenceIndex = -1;
    let sorterSequenceActive = false;
    let sorterSequenceGuardUntil = 0;
    let sorterLastObservedTrackId = '';
    let sorterQueueSyncPromise = null;
    let sorterQueueMonitorTimer = null;
    let sorterQueueArmPromise = null;
    let activeSorterTrackId = '';
    const sorterQueueUidByTrackId = new Map();

    function releaseSorterPlayback(message = 'Sorter playback queue released — Spotify playback took over.') {
        if (!sorterSequenceActive && !sorterSequence.length) return;

        sorterSequenceActive = false;
        sorterSequence = [];
        sorterSequenceIndex = -1;
        sorterLastObservedTrackId = '';
        activeSorterTrackId = '';
        updateNowPlayingHighlight();
        setStatus(message);
    }

    function captureSpotifyRequest(url, headers, body) {
        try {
            const h = headers instanceof Headers ? headers : new Headers(headers || {});
            const urlText = String(url || '');
            const auth = h.get('authorization');

            if (auth && /^Bearer\s+/i.test(auth)) {
                spotifyBearer = auth.replace(/^Bearer\s+/i, '').trim();
                window.__joshSpotifyBearer = spotifyBearer;
            }

            const spClientMatch = urlText.match(/https?:\/\/([a-z0-9-]*spclient[a-z0-9.\-]*)/i);
            if (spClientMatch) {
                connectSpClientBase = `https://${spClientMatch[1]}`;
                if (auth) connectCommandAuth = auth;
                connectClientToken = h.get('client-token') || connectClientToken;
            }

            // Spotify's web player registers its local playback device before it
            // necessarily sends a player-command request. Capture that device id
            // so we can derive the self-targeted Connect command route early.
            if (urlText.includes('/track-playback/v1/devices') && body) {
                try {
                    const registration = typeof body === 'string' ? JSON.parse(body) : body;
                    const deviceId = registration?.device?.device_id || '';
                    if (deviceId) connectDeviceId = deviceId;
                } catch {}
            }

            if (urlText.includes('/connect-state/v1/player/command/from/')) {
                connectCommandUrl = urlText;
                if (auth) connectCommandAuth = auth;
                connectClientToken = h.get('client-token') || connectClientToken;

                const route = urlText.match(/\/from\/([^/]+)\/to\/([^/?#]+)/);
                if (route?.[1]) connectDeviceId = route[1];

                // Keep a current native Play command template, but only learn it
                // from a REAL Spotify Play started on Liked Songs. v0.8.7 learned
                // every Play command, so playing an album/search/playlist track
                // could replace the sorter's context and make that outside track
                // leak back into Next.
                if (body) {
                    try {
                        const requestPayload = typeof body === 'string' ? JSON.parse(body) : body;
                        const command = requestPayload?.command;
                        const isPlay = command?.endpoint === 'play' && command?.context?.uri;
                        const isSorterReplay = sorterNativePlayDispatchDepth > 0;

                        if (isPlay && !isSorterReplay) {
                            // A genuine Spotify Play click means the user chose to
                            // leave sorter playback. Release ownership immediately
                            // instead of waiting for DOM now-playing detection.
                            if (sorterSequenceActive) {
                                releaseSorterPlayback();
                            }

                            // Only a native Play made from the actual Liked Songs
                            // route is safe to become the sorter's future context.
                            if (isLikedSongsPage()) {
                                nativePlayCommandTemplate = JSON.parse(JSON.stringify(command));
                                window.__joshNativePlayCommandTemplate = nativePlayCommandTemplate;
                                try {
                                    sessionStorage.setItem(
                                        NATIVE_PLAY_TEMPLATE_KEY,
                                        JSON.stringify(nativePlayCommandTemplate)
                                    );
                                } catch {}
                            }
                        }
                    } catch {}
                }
            }

            if (!connectCommandUrl && connectSpClientBase && connectDeviceId && connectCommandAuth) {
                connectCommandUrl =
                    `${connectSpClientBase}/connect-state/v1/player/command/from/${connectDeviceId}/to/${connectDeviceId}`;
            }

            if (!urlText.includes('pathfinder')) return;
            if (!body) return;

            const payload = typeof body === 'string' ? JSON.parse(body) : body;
            if (
                payload?.operationName === 'fetchLibraryTracks' &&
                payload?.extensions?.persistedQuery?.sha256Hash
            ) {
                liveFetchLibraryTracksHash =
                    payload.extensions.persistedQuery.sha256Hash;
                window.__joshFetchLibraryTracksHash = liveFetchLibraryTracksHash;
            }
        } catch {}
    }

    function installSpotifyAuthCapture() {
        if (window.__joshLikedSorterAuthHookInstalled) {
            spotifyBearer = window.__joshSpotifyBearer || spotifyBearer;
            liveFetchLibraryTracksHash =
                window.__joshFetchLibraryTracksHash || liveFetchLibraryTracksHash;
            return;
        }
        window.__joshLikedSorterAuthHookInstalled = true;

        try {
            const open0 = XMLHttpRequest.prototype.open;
            const send0 = XMLHttpRequest.prototype.send;
            const setHeader0 = XMLHttpRequest.prototype.setRequestHeader;

            XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                this.__joshUrl = url;
                this.__joshHeaders = {};
                return open0.call(this, method, url, ...rest);
            };

            XMLHttpRequest.prototype.setRequestHeader = function(k, v) {
                try { this.__joshHeaders[k] = v; } catch {}
                if (/^authorization$/i.test(k) && /^Bearer\s+/i.test(v)) {
                    spotifyBearer = String(v).replace(/^Bearer\s+/i, '').trim();
                    window.__joshSpotifyBearer = spotifyBearer;
                }
                return setHeader0.call(this, k, v);
            };

            XMLHttpRequest.prototype.send = function(body) {
                captureSpotifyRequest(this.__joshUrl, this.__joshHeaders, body);
                return send0.call(this, body);
            };
        } catch (err) {
            console.warn('[Liked Sort] XHR auth hook failed:', err);
        }

        try {
            const fetch0 = window.fetch;
            window.fetch = function(input, init = {}) {
                try {
                    const url = typeof input === 'string' ? input : input?.url;
                    const headers = new Headers(
                        init?.headers || (typeof input === 'object' ? input?.headers : {}) || {}
                    );
                    captureSpotifyRequest(url, headers, init?.body);
                } catch {}
                return fetch0.call(this, input, init);
            };
        } catch (err) {
            console.warn('[Liked Sort] fetch auth hook failed:', err);
        }
    }

    installSpotifyAuthCapture();

    let tracks = [];
    let visibleTracks = [];
    let sortMode = 'artist-asc';
    let filterText = '';
    let loading = false;
    let spotifyScrollContainer = null;

    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
    const sleep = ms => new Promise(r => setTimeout(r, ms));

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function formatDuration(ms) {
        const total = Math.floor((ms || 0) / 1000);
        const min = Math.floor(total / 60);
        const sec = String(total % 60).padStart(2, '0');
        return `${min}:${sec}`;
    }

    function formatDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return d.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }

    function injectCss() {
        if ($(`#${APP_ID}-style`)) return;

        const style = document.createElement('style');
        style.id = `${APP_ID}-style`;
        style.textContent = `
            #${APP_ID}-button {
                position: fixed;
                right: 24px;
                bottom: 105px;
                z-index: 2147483646;
                border: 0;
                border-radius: 999px;
                padding: 10px 16px;
                background: #1ed760;
                color: #000;
                font: 700 14px/1 Arial, sans-serif;
                cursor: pointer;
                box-shadow: 0 4px 18px rgba(0,0,0,.45);
            }

            #${APP_ID}-button:hover {
                transform: scale(1.03);
                background: #3be477;
            }

            #${APP_ID}-overlay {
                position: fixed;
                inset: 62px 12px 92px 12px;
                z-index: 2147483645;
                display: none;
                background: #121212;
                color: #fff;
                border: 1px solid #2b2b2b;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 10px 35px rgba(0,0,0,.7);
                font-family: Arial, sans-serif;
            }

            #${APP_ID}-overlay.open {
                display: flex;
                flex-direction: column;
            }

            #${APP_ID}-toolbar {
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 12px 14px;
                background: #181818;
                border-bottom: 1px solid #2a2a2a;
                flex-wrap: wrap;
            }

            #${APP_ID}-title {
                font-size: 18px;
                font-weight: 800;
                margin-right: 8px;
            }

            #${APP_ID}-count {
                color: #b3b3b3;
                font-size: 13px;
                margin-right: auto;
            }

            #${APP_ID}-toolbar input,
            #${APP_ID}-toolbar select,
            #${APP_ID}-toolbar button {
                border: 1px solid #3a3a3a;
                background: #242424;
                color: #fff;
                border-radius: 7px;
                padding: 8px 10px;
                font-size: 13px;
            }

            #${APP_ID}-toolbar input {
                width: 260px;
            }

            #${APP_ID}-toolbar button {
                cursor: pointer;
            }

            #${APP_ID}-toolbar button:hover {
                background: #303030;
            }

            #${APP_ID}-status {
                padding: 10px 14px;
                color: #b3b3b3;
                background: #151515;
                border-bottom: 1px solid #242424;
                font-size: 13px;
                display: none;
            }

            #${APP_ID}-status.show {
                display: block;
            }

            #${APP_ID}-list-wrap {
                flex: 1;
                overflow: auto;
                min-height: 0;
            }

            #${APP_ID}-header,
            .${APP_ID}-row {
                display: grid;
                grid-template-columns: 52px minmax(260px, 2fr) minmax(180px, 1.3fr) minmax(180px, 1.2fr) 125px 70px;
                gap: 12px;
                align-items: center;
                padding: 7px 16px;
            }

            #${APP_ID}-header {
                position: sticky;
                top: 0;
                z-index: 3;
                background: #181818;
                color: #b3b3b3;
                border-bottom: 1px solid #2b2b2b;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: .04em;
            }

            .${APP_ID}-row {
                min-height: 52px;
                border-bottom: 1px solid #1e1e1e;
                font-size: 13px;
            }

            .${APP_ID}-row:hover {
                background: #242424;
            }

            .${APP_ID}-row.${APP_ID}-now-playing {
                background: rgba(30, 215, 96, .12);
                box-shadow: inset 4px 0 0 #1ed760;
            }

            .${APP_ID}-row.${APP_ID}-now-playing:hover {
                background: rgba(30, 215, 96, .17);
            }

            .${APP_ID}-row.${APP_ID}-now-playing .${APP_ID}-song-title {
                color: #1ed760;
            }

            .${APP_ID}-playing-badge {
                display: none;
                flex: 0 0 auto;
                border: 1px solid rgba(30, 215, 96, .55);
                border-radius: 999px;
                padding: 2px 6px;
                color: #1ed760;
                font-size: 9px;
                font-weight: 800;
                letter-spacing: .05em;
            }

            .${APP_ID}-row.${APP_ID}-now-playing .${APP_ID}-playing-badge {
                display: inline-flex;
            }

            .${APP_ID}-cover {
                width: 42px;
                height: 42px;
                border-radius: 3px;
                object-fit: cover;
                background: #282828;
            }

            .${APP_ID}-song {
                min-width: 0;
            }

            .${APP_ID}-song-title {
                display: flex;
                align-items: center;
                gap: 8px;
                color: #fff;
                font-size: 14px;
                font-weight: 600;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .${APP_ID}-song-title button {
                border: 0;
                background: transparent;
                color: #1ed760;
                cursor: pointer;
                font-size: 16px;
                padding: 0 2px;
                flex: 0 0 auto;
            }

            .${APP_ID}-song-title button:hover {
                transform: scale(1.15);
            }

            .${APP_ID}-muted {
                color: #b3b3b3;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .${APP_ID}-empty {
                padding: 40px;
                text-align: center;
                color: #b3b3b3;
            }

            @media (max-width: 1050px) {
                #${APP_ID}-header,
                .${APP_ID}-row {
                    grid-template-columns: 48px minmax(250px, 2fr) minmax(180px, 1fr) 115px 60px;
                }
                .${APP_ID}-album {
                    display: none;
                }
            }
        `;
        document.head.appendChild(style);
    }

    function buildUi() {
        if ($(`#${APP_ID}-button`)) return;

        const button = document.createElement('button');
        button.id = `${APP_ID}-button`;
        button.textContent = 'Liked Sort';
        button.title = 'Open sortable Liked Songs';
        button.addEventListener('click', openOverlay);
        document.body.appendChild(button);

        const overlay = document.createElement('div');
        overlay.id = `${APP_ID}-overlay`;
        overlay.innerHTML = `
            <div id="${APP_ID}-toolbar">
                <div id="${APP_ID}-title">Liked Songs</div>
                <div id="${APP_ID}-count">Not loaded</div>

                <input id="${APP_ID}-search" type="search"
                    placeholder="Search title, artist, album..." autocomplete="off">

                <select id="${APP_ID}-sort">
                    <option value="artist-asc">Artist A → Z</option>
                    <option value="artist-desc">Artist Z → A</option>
                    <option value="title-asc">Title A → Z</option>
                    <option value="title-desc">Title Z → A</option>
                    <option value="album-asc">Album A → Z</option>
                    <option value="album-desc">Album Z → A</option>
                    <option value="date-desc">Date added: newest</option>
                    <option value="date-asc">Date added: oldest</option>
                </select>

                <button id="${APP_ID}-refresh">Refresh library</button>
                <button id="${APP_ID}-close">Close</button>
            </div>

            <div id="${APP_ID}-status"></div>

            <div id="${APP_ID}-list-wrap">
                <div id="${APP_ID}-header">
                    <div></div>
                    <div>Title / Artist</div>
                    <div class="${APP_ID}-album">Album</div>
                    <div>Artist</div>
                    <div>Date added</div>
                    <div>Time</div>
                </div>
                <div id="${APP_ID}-rows"></div>
            </div>
        `;
        document.body.appendChild(overlay);

        $(`#${APP_ID}-close`).addEventListener('click', closeOverlay);
        $(`#${APP_ID}-refresh`).addEventListener('click', () => loadLibrary(true));
        $(`#${APP_ID}-search`).addEventListener('input', (e) => {
            filterText = e.target.value.trim().toLocaleLowerCase();
            applySortAndFilter();
        });
        $(`#${APP_ID}-sort`).addEventListener('change', (e) => {
            sortMode = e.target.value;
            applySortAndFilter();
        });

        $(`#${APP_ID}-rows`).addEventListener('click', (e) => {
            const play = e.target.closest(`button[data-${APP_ID}-play]`);
            if (!play) return;

            const id = play.getAttribute(`data-${APP_ID}-play`);
            const track = tracks.find(t => t.id === id);
            if (track) playTrackInPlace(track);
        });

        // Spotify-style convenience: double-click anywhere on a song row to play
        // it. Ignore interactive controls so a double-click on the green button
        // does not accidentally issue two play requests.
        $(`#${APP_ID}-rows`).addEventListener('dblclick', (e) => {
            if (e.target.closest('button, input, select, a')) return;
            const row = e.target.closest(`.${APP_ID}-row`);
            if (!row) return;

            const id = row.getAttribute(`data-${APP_ID}-track`);
            const track = tracks.find(t => t.id === id);
            if (track) playTrackInPlace(track);
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && $(`#${APP_ID}-overlay`).classList.contains('open')) {
                closeOverlay();
            }
        });
    }

    function setStatus(text = '', isError = false) {
        const el = $(`#${APP_ID}-status`);
        if (!el) return;

        if (!text) {
            el.classList.remove('show');
            el.textContent = '';
            return;
        }

        el.classList.add('show');
        el.style.color = isError ? '#ff7b7b' : '#b3b3b3';
        el.textContent = text;
    }

    function detectTargetCount() {
        const grids = $$('[role="grid"][aria-rowcount], [aria-rowcount]');
        for (const grid of grids) {
            const n = Number(grid.getAttribute('aria-rowcount') || 0);
            if (Number.isFinite(n) && n > 1 && n < 100000) {
                // Spotify normally counts the column-header row too.
                return Math.max(1, n - 1);
            }
        }

        const heading = $$('h1, [data-testid="entityTitle"]').find(el =>
            /liked songs/i.test(el.textContent || '')
        );
        const texts = [
            heading?.parentElement?.textContent || '',
            document.body.textContent || ''
        ];
        for (const text of texts) {
            const m = text.match(/([\d,]{2,})\s+songs\b/i);
            if (!m) continue;
            const n = Number(m[1].replaceAll(',', ''));
            if (Number.isFinite(n) && n > 0 && n < 100000) return n;
        }
        return 0;
    }

    function trackRows() {
        return $$('div[role="row"][aria-rowindex], [data-testid="tracklist-row"]')
            .filter(row =>
                row.querySelector('a[data-testid="internal-track-link"], a[href*="/track/"]')
            );
    }

    function visibleTrackIndexes() {
        return trackRows()
            .map(row => Number(row.getAttribute('aria-rowindex') || 0))
            .filter(n => Number.isFinite(n) && n > 0)
            .sort((a, b) => a - b);
    }

    function rowCells(row) {
        return $$('[role="gridcell"]', row).map(c => (c.innerText || '').trim());
    }

    function dateTextToIso(text) {
        if (!text) return '';
        const t = text.trim().toLowerCase();
        const now = new Date();
        let d = null;

        if (t === 'today') d = now;
        else if (t === 'yesterday') d = new Date(now.getTime() - 86400000);
        else {
            let m = t.match(/^(\d+)\s+day/);
            if (m) d = new Date(now.getTime() - Number(m[1]) * 86400000);
            m = m || t.match(/^(\d+)\s+week/);
            if (!d && m) d = new Date(now.getTime() - Number(m[1]) * 7 * 86400000);
            m = d ? null : t.match(/^(\d+)\s+month/);
            if (!d && m) d = new Date(now.getTime() - Number(m[1]) * 30 * 86400000);
            m = d ? null : t.match(/^(\d+)\s+year/);
            if (!d && m) d = new Date(now.getTime() - Number(m[1]) * 365 * 86400000);
        }

        if (!d) {
            const ms = Date.parse(text);
            if (!Number.isNaN(ms)) d = new Date(ms);
        }
        return d ? d.toISOString() : '';
    }

    function durationTextToMs(text) {
        const m = String(text || '').match(/^(\d{1,3}):(\d{2})$/);
        return m ? (Number(m[1]) * 60 + Number(m[2])) * 1000 : 0;
    }

    function parseVisibleRows(map) {
        let added = 0;
        for (const row of trackRows()) {
            const trackAnchor =
                row.querySelector('a[data-testid="internal-track-link"]') ||
                row.querySelector('a[href*="/track/"]');
            if (!trackAnchor) continue;

            const href = trackAnchor.getAttribute('href') || '';
            const id = href.match(/\/track\/([A-Za-z0-9]+)/)?.[1] || '';
            if (!id) continue;

            const title = (
                trackAnchor.querySelector('div')?.textContent ||
                trackAnchor.textContent || ''
            ).trim();
            if (!title) continue;

            const artists = [...new Set(
                $$('a[href*="/artist/"]', row)
                    .map(a => (a.textContent || '').trim())
                    .filter(Boolean)
            )];
            const album = (row.querySelector('a[href*="/album/"]')?.textContent || '').trim();
            const cells = rowCells(row);
            const fullText = cells.join(' | ');
            const duration = (fullText.match(/\b\d{1,3}:\d{2}\b/g) || []).at(-1) || '';

            let dateText = '';
            for (const c of cells) {
                if (
                    /\b(today|yesterday|\d+\s+(day|week|month|year)s?\s+ago)\b/i.test(c) ||
                    /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/i.test(c) ||
                    /\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/.test(c)
                ) dateText = c;
            }

            const img = row.querySelector('img');
            const rowIndex = Number(row.getAttribute('aria-rowindex') || 0) || null;
            const item = {
                id,
                href: href.startsWith('http') ? href : `https://open.spotify.com${href}`,
                title,
                artists,
                artistText: artists.join(', '),
                album,
                addedAt: dateTextToIso(dateText),
                durationMs: durationTextToMs(duration),
                rowIndex,
                image: img?.currentSrc || img?.src || ''
            };

            const old = map.get(id);
            if (!old) {
                map.set(id, item);
                added++;
            } else {
                map.set(id, {
                    ...old,
                    ...Object.fromEntries(Object.entries(item).filter(([, v]) =>
                        v !== '' && v !== null && !(Array.isArray(v) && !v.length)
                    ))
                });
            }
        }
        return added;
    }

    function candidateScrollContainers() {
        const set = new Set();
        const row = trackRows()[0];
        if (row) {
            let p = row.parentElement;
            while (p && p !== document.body) {
                if (p.clientHeight > 100 && p.scrollHeight > p.clientHeight + 40) set.add(p);
                p = p.parentElement;
            }
        }

        for (const selector of [
            '[data-overlayscrollbars-viewport]',
            '.os-viewport',
            '.main-view-container',
            '.Root__main-view',
            '[role="presentation"]'
        ]) {
            for (const el of $$(selector)) {
                if (el.clientHeight > 100 && el.scrollHeight > el.clientHeight + 40) set.add(el);
            }
        }

        const doc = document.scrollingElement || document.documentElement;
        if (doc) set.add(doc);
        return [...set];
    }

    async function findWorkingScrollContainer() {
        const before = visibleTrackIndexes();
        if (!before.length) throw new Error('Spotify track rows are not visible. Open Liked Songs first.');

        let best = null;
        let bestScore = -1;

        for (const el of candidateScrollContainers()) {
            const oldTop = el.scrollTop || 0;
            const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
            if (maxTop < 100) continue;

            const jump = Math.max(350, Math.floor(el.clientHeight * 0.72));
            el.scrollTop = Math.min(maxTop, oldTop + jump);
            await sleep(220);

            const after = visibleTrackIndexes();
            const beforeMin = before[0] || 0;
            const afterMin = after[0] || 0;
            const beforeMax = before.at(-1) || 0;
            const afterMax = after.at(-1) || 0;
            const score = Math.abs(afterMin - beforeMin) + Math.abs(afterMax - beforeMax);

            el.scrollTop = oldTop;
            await sleep(100);

            if (score > bestScore) {
                best = el;
                bestScore = score;
            }
        }

        if (!best || bestScore <= 0) {
            throw new Error('Could not find Spotify’s moving Liked Songs scroll area.');
        }
        spotifyScrollContainer = best;
        return best;
    }

    function pickImage(sources) {
        if (!Array.isArray(sources) || !sources.length) return '';
        const usable = sources.filter(x => x?.url);
        if (!usable.length) return '';
        return (
            usable.find(x => Number(x.width || 0) >= 64 && Number(x.width || 0) <= 160) ||
            usable.find(x => Number(x.width || 0) >= 160) ||
            usable.at(-1) ||
            usable[0]
        )?.url || '';
    }

    function parseAddedAt(item) {
        const candidates = [
            item?.addedAt?.isoString,
            item?.addedAt?.timestamp,
            item?.addedAt,
            item?.added_at,
            item?.savedAt?.isoString,
            item?.savedAt,
            item?.saved_at
        ];
        for (const value of candidates) {
            if (!value || typeof value === 'object') continue;
            const ms = Date.parse(value);
            if (!Number.isNaN(ms)) return new Date(ms).toISOString();
        }
        return '';
    }

    function parseLibraryItem(item, rowIndex) {
        const wrapper = item?.track;
        const t = wrapper?.data;
        if (!wrapper || !t) return null;

        const uri = wrapper?._uri || wrapper?.uri || t?.uri || t?._uri || '';
        const id = String(uri).match(/spotify:track:([A-Za-z0-9]+)/)?.[1] || '';
        if (!id) return null;

        const artistItems = t?.artists?.items || [];
        const artists = artistItems
            .map(a => a?.profile?.name || a?.name || '')
            .filter(Boolean);

        const albumObj = t?.albumOfTrack || {};
        const durationMs =
            Number(t?.duration?.totalMilliseconds || 0) ||
            Number(t?.durationMs || 0) ||
            Number(t?.duration_ms || 0) ||
            0;

        return {
            id,
            title: t?.name || '',
            artists,
            artistText: artists.join(', '),
            album: albumObj?.name || '',
            addedAt: parseAddedAt(item),
            durationMs,
            image: pickImage(albumObj?.coverArt?.sources || []),
            rowIndex
        };
    }

    async function waitForSpotifyBearer(timeoutMs = 2500) {
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            spotifyBearer = window.__joshSpotifyBearer || spotifyBearer;
            liveFetchLibraryTracksHash =
                window.__joshFetchLibraryTracksHash || liveFetchLibraryTracksHash;
            if (spotifyBearer) return spotifyBearer;
            await sleep(100);
        }
        return '';
    }

    async function fetchLibraryPage(offset, limit) {
        const bearer = await waitForSpotifyBearer();
        if (!bearer) {
            throw new Error(
                'I could not capture Spotify’s logged-in session token. Refresh the Spotify tab once with v0.6 enabled, then open Liked Songs again.'
            );
        }

        const hash =
            liveFetchLibraryTracksHash ||
            window.__joshFetchLibraryTracksHash ||
            FETCH_LIBRARY_TRACKS_FALLBACK_HASH;

        const body = {
            variables: { offset, limit },
            operationName: 'fetchLibraryTracks',
            extensions: {
                persistedQuery: {
                    version: 1,
                    sha256Hash: hash
                }
            }
        };

        while (true) {
            const response = await fetch(GQL_URL, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${bearer}`,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json;charset=UTF-8',
                    'app-platform': 'WebPlayer'
                },
                body: JSON.stringify(body)
            });

            if (response.status === 429) {
                const wait = Math.max(1, Number(response.headers.get('Retry-After') || 2));
                setStatus(`Spotify rate-limited the library read. Waiting ${wait}s…`);
                await sleep(wait * 1000);
                continue;
            }

            const text = await response.text();
            let data = null;
            try { data = JSON.parse(text); } catch {}

            if (!response.ok) {
                throw new Error(
                    `Spotify library GraphQL failed (${response.status}). ${text.slice(0, 180)}`
                );
            }

            const errorText = JSON.stringify(data?.errors || []);
            if (/PersistedQueryNotFound/i.test(errorText)) {
                throw new Error(
                    'Spotify rotated the Liked Songs query hash. Refresh Spotify once so the sorter can capture the new live hash, then try again.'
                );
            }

            const tracksData = data?.data?.me?.library?.tracks;
            if (!tracksData || !Array.isArray(tracksData.items)) {
                throw new Error(
                    `Spotify returned an unexpected Liked Songs shape. Response keys: ${Object.keys(data?.data?.me || {}).join(', ') || 'none'}`
                );
            }

            return tracksData;
        }
    }

    async function scanLikedSongs() {
        const pageSize = 50;
        const result = [];
        const seen = new Set();
        let offset = 0;
        let total = null;
        let pageNo = 0;

        while (total === null || offset < total) {
            const page = await fetchLibraryPage(offset, pageSize);
            pageNo++;
            if (total === null) total = Number(page.totalCount || 0);

            const items = page.items || [];
            for (let i = 0; i < items.length; i++) {
                const track = parseLibraryItem(items[i], offset + i + 2);
                if (!track || seen.has(track.id)) continue;
                seen.add(track.id);
                result.push(track);
            }

            setStatus(
                total
                    ? `Loading Liked Songs… ${result.length.toLocaleString()} / ${total.toLocaleString()}`
                    : `Loading Liked Songs… ${result.length.toLocaleString()} found`
            );

            if (!items.length) break;
            offset += items.length;

            if (items.length < pageSize && (!total || offset >= total)) break;
            if (pageNo > 500) throw new Error('Spotify library pagination loop guard tripped.');
            await sleep(60);
        }

        if (!result.length) throw new Error('Spotify returned zero Liked Songs.');
        if (total && result.length < total) {
            throw new Error(
                `Spotify reported ${total.toLocaleString()} Liked Songs but only ${result.length.toLocaleString()} unique tracks were returned.`
            );
        }
        return result;
    }

    function readCache() {
        try {
            const when = Number(localStorage.getItem(CACHE_TIME_KEY) || 0);
            if (!when || (Date.now() - when) > CACHE_TTL_MS) return null;

            const raw = localStorage.getItem(CACHE_KEY);
            if (!raw) return null;

            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : null;
        } catch {
            return null;
        }
    }

    function writeCache(data) {
        try {
            localStorage.setItem(CACHE_KEY, JSON.stringify(data));
            localStorage.setItem(CACHE_TIME_KEY, String(Date.now()));
        } catch (err) {
            console.warn('[Liked Sort] Could not cache library:', err);
        }
    }

    async function loadLibrary(force = false) {
        if (loading) return;
        loading = true;
        const refresh = $(`#${APP_ID}-refresh`);
        if (refresh) {
            refresh.disabled = true;
            refresh.textContent = 'Loading…';
        }

        try {
            if (!force) {
                const cached = readCache();
                if (cached?.length) {
                    tracks = cached;
                    setStatus('');
                    applySortAndFilter();
                    return;
                }
            }

            setStatus('Using Spotify’s logged-in web session to load your full Liked Songs library…');
            tracks = await scanLikedSongs();
            writeCache(tracks);
            setStatus(`Done — ${tracks.length.toLocaleString()} songs loaded. Sort/search away 😎`);
            applySortAndFilter();
        } catch (err) {
            console.error('[Liked Sort]', err);
            setStatus(err.message, true);
        } finally {
            loading = false;
            if (refresh) {
                refresh.disabled = false;
                refresh.textContent = 'Refresh library';
            }
        }
    }

    function compareText(a, b) {
        return String(a || '').localeCompare(
            String(b || ''),
            undefined,
            { sensitivity: 'base', numeric: true }
        );
    }

    function sortedCopy(data) {
        const out = [...data];

        switch (sortMode) {
            case 'artist-desc':
                return out.sort((a, b) =>
                    compareText(b.artistText, a.artistText) ||
                    compareText(b.title, a.title)
                );

            case 'title-asc':
                return out.sort((a, b) =>
                    compareText(a.title, b.title) ||
                    compareText(a.artistText, b.artistText)
                );

            case 'title-desc':
                return out.sort((a, b) =>
                    compareText(b.title, a.title) ||
                    compareText(b.artistText, a.artistText)
                );

            case 'album-asc':
                return out.sort((a, b) =>
                    compareText(a.album, b.album) ||
                    compareText(a.title, b.title)
                );

            case 'album-desc':
                return out.sort((a, b) =>
                    compareText(b.album, a.album) ||
                    compareText(b.title, a.title)
                );

            case 'date-asc':
                return out.sort((a, b) =>
                    new Date(a.addedAt || 0) - new Date(b.addedAt || 0)
                );

            case 'date-desc':
                return out.sort((a, b) =>
                    new Date(b.addedAt || 0) - new Date(a.addedAt || 0)
                );

            case 'artist-asc':
            default:
                return out.sort((a, b) =>
                    compareText(a.artistText, b.artistText) ||
                    compareText(a.title, b.title)
                );
        }
    }

    function applySortAndFilter() {
        let data = tracks;

        if (filterText) {
            data = data.filter(t => {
                const haystack =
                    `${t.title} ${t.artistText} ${t.album}`.toLocaleLowerCase();
                return haystack.includes(filterText);
            });
        }

        visibleTracks = sortedCopy(data);
        renderRows();
    }

    function renderRows() {
        const rows = $(`#${APP_ID}-rows`);
        const count = $(`#${APP_ID}-count`);
        if (!rows || !count) return;

        count.textContent =
            filterText
                ? `${visibleTracks.length.toLocaleString()} shown / ${tracks.length.toLocaleString()} liked`
                : `${tracks.length.toLocaleString()} liked songs`;

        if (!visibleTracks.length) {
            rows.innerHTML =
                `<div class="${APP_ID}-empty">No matching songs.</div>`;
            return;
        }

        rows.innerHTML = visibleTracks.map(t => `
            <div class="${APP_ID}-row" data-${APP_ID}-track="${escapeHtml(t.id)}">
                <div>
                    ${t.image
                        ? `<img class="${APP_ID}-cover" src="${escapeHtml(t.image)}" alt="">`
                        : `<div class="${APP_ID}-cover"></div>`}
                </div>

                <div class="${APP_ID}-song">
                    <div class="${APP_ID}-song-title">
                        <button
                            type="button"
                            data-${APP_ID}-play="${escapeHtml(t.id)}"
                            data-title="${escapeHtml(t.title)}"
                            title="Play ${escapeHtml(t.title)}">▶</button>
                        <span title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
                        <span class="${APP_ID}-playing-badge">NOW PLAYING</span>
                    </div>
                    <div class="${APP_ID}-muted" title="${escapeHtml(t.artistText)}">
                        ${escapeHtml(t.artistText)}
                    </div>
                </div>

                <div class="${APP_ID}-muted ${APP_ID}-album"
                    title="${escapeHtml(t.album)}">${escapeHtml(t.album)}</div>

                <div class="${APP_ID}-muted"
                    title="${escapeHtml(t.artistText)}">${escapeHtml(t.artistText)}</div>

                <div class="${APP_ID}-muted">${escapeHtml(formatDate(t.addedAt))}</div>

                <div class="${APP_ID}-muted">${escapeHtml(formatDuration(t.durationMs))}</div>
            </div>
        `).join('');

        updateNowPlayingHighlight();
    }

    async function openOverlay() {
        $(`#${APP_ID}-overlay`).classList.add('open');

        if (!tracks.length) {
            await loadLibrary(false);
        } else {
            applySortAndFilter();
        }

        setTimeout(() => $(`#${APP_ID}-search`)?.focus(), 50);
    }

    function closeOverlay() {
        $(`#${APP_ID}-overlay`).classList.remove('open');
    }

    function isLikedSongsPage() {
        return location.pathname === '/collection/tracks'
            || location.pathname.startsWith('/collection/tracks/');
    }

    function findScrollContainer() {
        if (spotifyScrollContainer?.isConnected) return spotifyScrollContainer;

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

    function renderedTrackRow(trackId) {
        const safe = window.CSS?.escape ? CSS.escape(trackId) : trackId;
        const anchors = $$(`a[href*="/track/${safe}"]`);
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
            .sort((a, b) => a.index - b.index);
    }

    function estimateRowStep(indexed) {
        if (indexed.length >= 2) {
            for (let i = 1; i < indexed.length; i++) {
                const di = indexed[i].index - indexed[i - 1].index;
                const dp = indexed[i].top - indexed[i - 1].top;
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


    async function waitUntil(test, timeoutMs = 5000, intervalMs = 80) {
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            try {
                const value = test();
                if (value) return value;
            } catch {}
            await sleep(intervalMs);
        }
        return null;
    }

    function nowPlayingTrackId() {
        // Spotify has moved the now-playing metadata around over time. Prefer a
        // real /track/ link from any current player surface, then fall back to
        // title + artist matching against the loaded library.
        const linkSelectors = [
            '[aria-label^="Now playing:"] a[href^="/track/"]',
            'footer a[href^="/track/"]',
            '[data-testid="now-playing-widget"] a[href^="/track/"]',
            '[data-testid="now-playing-bar"] a[href^="/track/"]',
            'a[data-testid="context-item-link"][href^="/track/"]'
        ];

        for (const selector of linkSelectors) {
            for (const a of $$(selector)) {
                const href = a.getAttribute('href') || '';
                const id = href.match(/\/track\/([A-Za-z0-9]+)/)?.[1] || '';
                if (id) {
                    activeSorterTrackId = id;
                    return id;
                }
            }
        }

        const scopes = [
            ...$$('[aria-label^="Now playing:"]'),
            ...$$('footer'),
            ...$$('[data-testid="now-playing-widget"]'),
            ...$$('[data-testid="now-playing-bar"]')
        ];

        for (const scope of scopes) {
            const title = (
                scope.querySelector('a[data-testid="context-item-link"]')?.textContent ||
                scope.querySelector('a[href^="/track/"]')?.textContent ||
                scope.querySelector('a[href^="/album/"]')?.textContent ||
                ''
            ).trim();
            if (!title) continue;

            const artists = $$('a[href^="/artist/"]', scope)
                .map(a => (a.textContent || '').trim())
                .filter(Boolean);
            const artistText = artists.join(', ');

            const matches = tracks.filter(t =>
                t.title.localeCompare(title, undefined, { sensitivity: 'base' }) === 0
            );
            const exact = artistText
                ? matches.find(t =>
                    t.artistText.localeCompare(artistText, undefined, { sensitivity: 'base' }) === 0
                )
                : null;
            const found = exact || (matches.length === 1 ? matches[0] : null);
            if (found?.id) {
                activeSorterTrackId = found.id;
                return found.id;
            }
        }

        // Keep the last sorter-started id while Spotify's DOM is transitioning.
        return activeSorterTrackId || '';
    }

    function syncBrowserTabTitle(activeId) {
        if (!activeId) return;

        const track = tracks.find(t => t.id === activeId);
        if (!track?.title) return;

        const artist = track.artistText ? ` • ${track.artistText}` : '';
        const wanted = `${track.title}${artist} | Spotify`;

        // Spotify's SPA route restoration can overwrite document.title after a
        // sorter-started track begins. Re-assert the actual now-playing title so
        // the browser tab follows playback instead of sticking on Liked Songs or
        // the temporary track page we used to start playback.
        if (document.title !== wanted) document.title = wanted;
    }

    function updateNowPlayingHighlight() {
        const activeId = nowPlayingTrackId();
        const attr = `data-${APP_ID}-track`;

        syncBrowserTabTitle(activeId);

        for (const row of $$(`.${APP_ID}-row`)) {
            const isActive = Boolean(activeId && row.getAttribute(attr) === activeId);
            row.classList.toggle(`${APP_ID}-now-playing`, isActive);

            const play = row.querySelector(`button[data-${APP_ID}-play]`);
            if (play) {
                play.textContent = isActive ? '🔊' : '▶';
                play.title = isActive
                    ? 'Currently playing'
                    : `Play ${play.getAttribute('data-title') || ''}`.trim();
            }
        }
    }

    function startNowPlayingHighlightMonitor() {
        updateNowPlayingHighlight();
        window.setInterval(updateNowPlayingHighlight, 650);
    }

    function randomHex32() {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        return [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    }

    function queueUidForTrack(track) {
        let uid = sorterQueueUidByTrackId.get(track.id);
        if (!uid) {
            uid = randomHex32();
            sorterQueueUidByTrackId.set(track.id, uid);
        }
        return uid;
    }

    function sorterQueueTrackPayload(track) {
        const metadata = {};
        if (track.title) metadata.title = track.title;
        if (track.artistText) metadata.artist_name = track.artistText;
        if (track.album) metadata.album_title = track.album;
        if (track.image) metadata.image_url = track.image;

        return {
            uri: `spotify:track:${track.id}`,
            uid: queueUidForTrack(track),
            metadata,
            provider: 'queue'
        };
    }

    async function waitForConnectCommandRoute(timeoutMs = 2500) {
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            if (!connectCommandUrl && connectSpClientBase && connectDeviceId && connectCommandAuth) {
                connectCommandUrl =
                    `${connectSpClientBase}/connect-state/v1/player/command/from/${connectDeviceId}/to/${connectDeviceId}`;
            }
            if (connectCommandUrl && connectCommandAuth) return true;
            await sleep(80);
        }
        return Boolean(connectCommandUrl && connectCommandAuth);
    }

    async function sendSorterConnectCommand(endpoint, extra = {}) {
        const ready = await waitForConnectCommandRoute();
        if (!ready) {
            console.warn('[Liked Sort] Spotify Connect route was not captured yet.');
            return false;
        }

        const headers = {
            'Authorization': connectCommandAuth,
            'Accept': '*/*',
            'Content-Type': 'application/json;charset=UTF-8',
            'app-platform': 'WebPlayer'
        };
        if (connectClientToken) headers['Client-Token'] = connectClientToken;

        const command = {
            endpoint,
            logging_params: {
                command_id: randomHex32(),
                page_instance_ids: [],
                interaction_ids: []
            },
            ...extra
        };

        try {
            let responsePromise;
            sorterNativePlayDispatchDepth += 1;
            try {
                responsePromise = fetch(connectCommandUrl, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ command })
                });
            } finally {
                sorterNativePlayDispatchDepth = Math.max(0, sorterNativePlayDispatchDepth - 1);
            }

            const response = await responsePromise;

            if (!response.ok) {
                const detail = (await response.text()).slice(0, 180);
                console.warn(
                    `[Liked Sort] Spotify Connect ${endpoint} failed (${response.status}):`,
                    detail
                );
                return false;
            }
            return true;
        } catch (err) {
            console.warn(`[Liked Sort] Spotify Connect ${endpoint} request failed:`, err);
            return false;
        }
    }

    async function syncSorterQueueAt(index, quiet = false) {
        if (!sorterSequenceActive) return false;
        if (index < 0 || index >= sorterSequence.length) return false;

        if (sorterQueueSyncPromise) return sorterQueueSyncPromise;

        sorterQueueSyncPromise = (async () => {
            const prevTracks = sorterSequence
                .slice(Math.max(0, index - SORTER_QUEUE_HISTORY), index)
                .map(sorterQueueTrackPayload);

            const nextTracks = sorterSequence
                .slice(index + 1, index + 1 + SORTER_QUEUE_LOOKAHEAD)
                .map(sorterQueueTrackPayload);

            const ok = await sendSorterConnectCommand('set_queue', {
                next_tracks: nextTracks,
                prev_tracks: prevTracks
            });

            if (ok) {
                sorterSequenceIndex = index;
                if (!quiet) {
                    const remaining = Math.max(0, sorterSequence.length - index - 1);
                    setStatus(
                        `Playing in sorter order — ${remaining.toLocaleString()} song${remaining === 1 ? '' : 's'} after this one 😎`
                    );
                }
            }
            return ok;
        })();

        try {
            return await sorterQueueSyncPromise;
        } finally {
            sorterQueueSyncPromise = null;
        }
    }

    function startSorterQueueMonitor() {
        if (sorterQueueMonitorTimer) return;

        sorterQueueMonitorTimer = window.setInterval(async () => {
            if (!sorterSequenceActive || sorterQueueSyncPromise) return;

            const id = nowPlayingTrackId();
            if (!id || id === sorterLastObservedTrackId) return;

            sorterLastObservedTrackId = id;
            const index = sorterSequence.findIndex(t => t.id === id);

            if (index >= 0) {
                sorterSequenceIndex = index;
                // Refill Spotify's explicit queue as playback advances so even
                // very large sorted libraries can keep going past the 80-track
                // next_tracks window exposed by Connect state.
                await syncSorterQueueAt(index, true);
                return;
            }

            // If the user deliberately starts something outside the sorter after
            // the initial playback transition, release queue ownership.
            if (Date.now() > sorterSequenceGuardUntil) {
                releaseSorterPlayback();
            }
        }, 700);
    }

    async function activateSorterSequence(track) {
        const source = visibleTracks.length ? [...visibleTracks] : [...tracks];
        const index = source.findIndex(t => t.id === track.id);
        if (index < 0) return false;

        sorterSequence = source;
        sorterSequenceIndex = index;
        sorterSequenceActive = true;
        sorterSequenceGuardUntil = Date.now() + 4500;
        activeSorterTrackId = track.id;
        updateNowPlayingHighlight();

        // Do not let the queue monitor react to the OLD now-playing track while
        // Spotify is still switching to the song the sorter just requested.
        await waitUntil(() => nowPlayingMatches(track), 2500, 100);
        sorterLastObservedTrackId = nowPlayingTrackId() || track.id;
        startSorterQueueMonitor();

        // Spotify sometimes exposes its Connect device a little after playback
        // begins. v0.8.2 waits quietly and arms the queue as soon as that route is
        // ready instead of flashing a false failure after 2.5 seconds.
        setStatus(`Playing “${track.title}” — arming sorter queue…`);

        if (sorterQueueArmPromise) {
            try { await sorterQueueArmPromise; } catch {}
        }

        sorterQueueArmPromise = (async () => {
            const ready = await waitForConnectCommandRoute(45000);
            if (!ready || !sorterSequenceActive) return false;

            // Re-resolve the index in case the player advanced while Spotify was
            // finishing Connect initialization.
            const currentId = nowPlayingTrackId() || track.id;
            const currentIndex = sorterSequence.findIndex(t => t.id === currentId);
            return await syncSorterQueueAt(currentIndex >= 0 ? currentIndex : index, false);
        })();

        try {
            const ok = await sorterQueueArmPromise;
            if (!ok && sorterSequenceActive) {
                setStatus(`Playing “${track.title}” — Spotify queue is still initializing…`);
            }
            return ok;
        } finally {
            sorterQueueArmPromise = null;
        }
    }

    function nowPlayingMatches(track) {
        if (!track?.id) return false;
        const detected = nowPlayingTrackId();
        if (detected) return detected === track.id;

        const scopes = [
            document.querySelector('[aria-label^="Now playing:"]'),
            document.querySelector('footer'),
            document.querySelector('[data-testid="now-playing-widget"]'),
            document.querySelector('[data-testid="now-playing-bar"]')
        ].filter(Boolean);

        return scopes.some(scope => {
            const txt = (scope.textContent || '').toLowerCase();
            return Boolean(track.title && txt.includes(track.title.toLowerCase()));
        });
    }

    function trackPagePlayButton(track) {
        const main = document.querySelector('main') || document;
        const candidates = [
            ...main.querySelectorAll(
                'button[data-testid="play-button"], button[aria-label^="Play "], button[aria-label="Play"], button[aria-label^="Pause "]'
            )
        ].filter(btn => !btn.closest(`#${APP_ID}-overlay`));

        if (!candidates.length) return null;

        const title = String(track.title || '').toLowerCase();

        return (
            candidates.find(btn => {
                const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                return title && label.includes(title);
            }) ||
            candidates.find(btn => btn.getAttribute('data-testid') === 'play-button') ||
            candidates[0]
        );
    }

    async function restoreSpotifyRoute(returnPath, returnSearch, returnHash) {
        history.back();

        const restored = await waitUntil(
            () =>
                location.pathname === returnPath &&
                location.search === returnSearch,
            3500,
            80
        );

        if (!restored) {
            // Last-resort SPA nudge. This does not reload the document, so our
            // sorter overlay remains alive.
            try {
                history.replaceState(
                    history.state,
                    '',
                    `${returnPath}${returnSearch}${returnHash || ''}`
                );
                window.dispatchEvent(
                    new PopStateEvent('popstate', { state: history.state })
                );
            } catch {}
        }
    }

    async function playViaCapturedNativeCommand(track) {
        if (!track?.id) return false;

        const ready = await waitForConnectCommandRoute(5000);
        if (!ready) {
            setStatus(
                'Spotify player connection is not ready yet. Play one song normally in Liked Songs, then try again.',
                true
            );
            return false;
        }

        nativePlayCommandTemplate =
            window.__joshNativePlayCommandTemplate || nativePlayCommandTemplate;

        if (!nativePlayCommandTemplate?.context?.uri) {
            setStatus(
                'I need one current Spotify Play command first: play any song normally in Liked Songs once, then use Liked Sort.',
                true
            );
            return false;
        }

        const headers = {
            'Authorization': connectCommandAuth,
            'Accept': '*/*',
            'Content-Type': 'application/json;charset=UTF-8',
            'app-platform': 'WebPlayer'
        };
        if (connectClientToken) headers['Client-Token'] = connectClientToken;

        const command = JSON.parse(JSON.stringify(nativePlayCommandTemplate));
        const uri = `spotify:track:${track.id}`;

        command.endpoint = 'play';
        command.context = command.context || {};
        command.context.metadata = command.context.metadata || {};
        command.play_origin = command.play_origin || {};
        command.play_origin.feature_identifier =
            command.play_origin.feature_identifier || 'your_library';
        command.play_origin.referrer_identifier =
            command.play_origin.referrer_identifier || 'your_library';

        command.options = command.options || {};
        command.options.license = command.options.license || 'tft';
        command.options.player_options_override =
            command.options.player_options_override || {};

        // Native Spotify currently sends track_uid + track_index + track_uri.
        // The uid is context-internal and cannot be safely invented for another
        // track, so replace the target with the stable URI and the original
        // Liked Songs index that we already retain from the library scan.
        const skipTo = { track_uri: uri };
        const originalIndex = Number(track.rowIndex || 0) - 2;
        if (Number.isFinite(originalIndex) && originalIndex >= 0) {
            skipTo.track_index = originalIndex;
        }
        command.options.skip_to = skipTo;

        const oldLogging = command.logging_params || {};
        command.logging_params = {
            ...oldLogging,
            command_id: randomHex32(),
            page_instance_ids: Array.isArray(oldLogging.page_instance_ids)
                ? oldLogging.page_instance_ids
                : [],
            interaction_ids: [
                (crypto.randomUUID ? crypto.randomUUID() : randomHex32())
            ]
        };

        // nowPlayingTrackId() deliberately keeps the last sorter id while Spotify's
        // DOM is transitioning. Clear it before verification so an old highlight
        // cannot make a failed Play request look successful.
        activeSorterTrackId = '';

        try {
            const response = await fetch(connectCommandUrl, {
                method: 'POST',
                headers,
                body: JSON.stringify({ command })
            });

            if (!response.ok) {
                const detail = (await response.text()).slice(0, 220);
                setStatus(
                    `Spotify rejected the native Play command (${response.status})${detail ? ` — ${detail}` : ''}`,
                    true
                );
                return false;
            }

            const switched = await waitUntil(() => {
                const detected = nowPlayingTrackId();
                return detected === track.id;
            }, 4000, 100);

            if (!switched) {
                setStatus(
                    `Spotify accepted Play for “${track.title}”, but the player did not switch to it.`,
                    true
                );
                return false;
            }

            setStatus(`Playing “${track.title}” — sorter stays open 😎`);
            return true;
        } catch (err) {
            console.warn('[Liked Sort] Native Spotify Play request failed:', err);
            setStatus(`Spotify Play request failed: ${err?.message || err}`, true);
            return false;
        }
    }

    async function playViaTrackPage(track) {
        if (nowPlayingMatches(track)) {
            setStatus(`“${track.title}” is already playing 😎`);
            return true;
        }

        const returnPath = location.pathname;
        const returnSearch = location.search;
        const returnHash = location.hash;
        const targetPath = `/track/${track.id}`;

        setStatus(`Opening “${track.title}” under the sorter…`);

        try {
            // Spotify is a single-page app. Push the track route underneath our
            // overlay and fire popstate so Spotify renders the track page without
            // reloading this userscript.
            history.pushState(history.state, '', targetPath);
            window.dispatchEvent(
                new PopStateEvent('popstate', { state: history.state })
            );
        } catch (e) {
            console.warn('[Liked Sort] SPA navigation failed:', e);
            return false;
        }

        const playButton = await waitUntil(
            () => {
                if (location.pathname !== targetPath) return null;
                return trackPagePlayButton(track);
            },
            5000,
            100
        );

        if (!playButton) {
            await restoreSpotifyRoute(returnPath, returnSearch, returnHash);
            setStatus(
                `Spotify opened “${track.title}” but never rendered its Play button.`,
                true
            );
            return false;
        }

        const label = (playButton.getAttribute('aria-label') || '').toLowerCase();

        // If Spotify already says Pause for this track, it is already playing.
        if (!label.startsWith('pause')) {
            playButton.click();
        }

        // Give Spotify's player command a moment to land before returning the
        // underlying page to Liked Songs.
        await sleep(900);

        const started =
            nowPlayingMatches(track) ||
            (playButton.getAttribute('aria-label') || '').toLowerCase().startsWith('pause');

        await restoreSpotifyRoute(returnPath, returnSearch, returnHash);

        if (started) {
            setStatus(`Playing “${track.title}” — sorter stays open 😎`);
            return true;
        }

        // Spotify sometimes updates the player a beat after the route is restored.
        await sleep(500);
        if (nowPlayingMatches(track)) {
            setStatus(`Playing “${track.title}” — sorter stays open 😎`);
            return true;
        }

        setStatus(
            `Spotify rendered “${track.title}” and received the Play click, but I could not confirm playback.`,
            true
        );
        return false;
    }

    async function playTrackInPlace(track) {
        if (!track?.id) return;

        if (!isLikedSongsPage()) {
            setStatus(
                'Open Spotify → Liked Songs underneath the sorter for in-place playback.',
                true
            );
            return;
        }

        setStatus(`Playing “${track.title}”…`);

        // Spotify's Aug 24 web-player deployment changed the private native Play
        // payload. Clone a real current Play command captured from Spotify itself,
        // replace only the target track, then keep v0.8.3's proven sorter queue.
        const startedNative = await playViaCapturedNativeCommand(track);
        if (startedNative) {
            await activateSorterSequence(track);
            return;
        }

        // If no native template has been captured yet, do not spray more guessed
        // private commands. A visible row can still receive the user's immediate
        // click gesture; deep-library playback waits until Spotify has given us a
        // real native template from one normal Liked Songs play.
        let row = renderedTrackRow(track.id);
        if (row && !nativePlayCommandTemplate) {
            triggerSpotifyPlay(row);
            await sleep(400);
            if (nowPlayingMatches(track)) {
                await activateSorterSequence(track);
            }
        }
    }

    function init() {
        injectCss();
        buildUi();
        startNowPlayingHighlightMonitor();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
