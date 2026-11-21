// Global Application State & Chart Instances
let appCatalog = { dancers: [], couples: [] };
let overviewClubsChartInstance = null;
let coupleTrajectoryChartInstance = null;
let coupleDanceRadarChartInstance = null;
let coupleSingleJudgeHistoryChartInstance = null;
let coupleSingleJudgeDanceChartInstance = null;
let dancerCareerChartInstance = null;
let judgeClubBiasChartInstance = null;

const MODULE_TITLES = {
  overview: 'Pulpit & Architektura FTS',
  couples: 'Analityka Pary & Oceny Sędziów',
  dancers: 'Profil Tancerza & Historia Partnerstw',
  h2h: 'Porównywarka Par Head-to-Head',
  tournaments: 'Wyszukiwarka Turniejów & Arkusze Skating',
  judges: 'Komisje Sędziowskie & Macierz Przychylności (Bias)',
};

// Universal API / Static Bundle Resolver (Works both with FastAPI backend AND on static GitHub Pages!)
async function fetchFtsApi(endpointUrl) {
  try {
    if (window.location.protocol !== 'file:' && !window.location.hostname.includes('github.io')) {
      const resp = await fetch(endpointUrl);
      if (resp.ok) {
        return await resp.json();
      }
    }
  } catch (e) {
    // Fallback to static bundle
  }
  return resolveFromStaticBundle(endpointUrl);
}

function resolveFromStaticBundle(endpointUrl) {
  const b = window.FTS_STATIC_BUNDLE;
  if (!b) return { error: 'Static bundle not loaded' };

  const [pathPart, queryPart] = endpointUrl.split('?');
  const params = new URLSearchParams(queryPart || '');

  if (pathPart === '/api/overview') return b.overview;
  if (pathPart === '/api/catalog') return b.catalog;

  if (pathPart.startsWith('/api/dancer/')) {
    const dancerId = decodeURIComponent(pathPart.replace('/api/dancer/', ''));
    return b.dancersMap[dancerId] || { error: 'Not found' };
  }

  if (pathPart.includes('/judge/') && pathPart.startsWith('/api/couple/')) {
    const parts = pathPart.replace('/api/couple/', '').split('/judge/');
    const cKey = decodeURIComponent(parts[0]);
    const jId = decodeURIComponent(parts[1]);
    const stored = b.coupleJudgeHistoryMap?.[`${cKey}__${jId}`];
    return {
      couple: stored?.couple || b.couplesMap[cKey]?.couple || {},
      judge: stored?.judge || b.judgesMap[jId]?.selectedJudgeDetail?.judge || {},
      timelineEvents: stored?.timelineEvents || [],
      perDanceSummary: stored?.perDanceSummary || [],
    };
  }

  if (pathPart.startsWith('/api/couple/')) {
    const coupleKey = decodeURIComponent(pathPart.replace('/api/couple/', ''));
    return b.couplesMap[coupleKey] || { error: 'Not found' };
  }

  if (pathPart === '/api/head-to-head') {
    const coupleA = params.get('coupleA');
    const coupleB = params.get('coupleB');
    return computeStaticHeadToHead(b, coupleA, coupleB);
  }

  if (pathPart === '/api/tournaments') {
    const q = (params.get('q') || '').toLowerCase();
    const voivodeship = (params.get('voivodeship') || '').toLowerCase();
    const danceClass = (params.get('danceClass') || '').toLowerCase();
    const ageGroup = (params.get('ageGroup') || '').toLowerCase();
    const style = (params.get('style') || '').toLowerCase();
    const rank = (params.get('rank') || '').toLowerCase();

    const filtered = (b.tournamentsAll || []).filter((t) => {
      if (q && !t.competitionName.toLowerCase().includes(q) && !t.cityName.toLowerCase().includes(q)) return false;
      if (voivodeship && (t.voivodeship || '').toLowerCase() !== voivodeship) return false;
      if (rank && !(t.tournamentRank || '').toLowerCase().includes(rank)) return false;
      if (danceClass || ageGroup || style) {
        const cats = t.categories || [];
        const hasMatchingCat = cats.some((c) => {
          if (danceClass && (c.danceClass || '').toLowerCase() !== danceClass) return false;
          if (ageGroup && !(c.ageGroup || '').toLowerCase().includes(ageGroup)) return false;
          if (style && (c.style || '').toLowerCase() !== style) return false;
          return true;
        });
        if (!hasMatchingCat) return false;
      }
      return true;
    });
    return { tournaments: filtered };
  }

  if (pathPart.startsWith('/api/category/')) {
    const catId = decodeURIComponent(pathPart.replace('/api/category/', ''));
    return b.categoriesMap[catId] || { error: 'Category not found' };
  }

  if (pathPart === '/api/judges') {
    const jId = params.get('judgeId');
    if (jId && b.judgesMap[jId]) return b.judgesMap[jId];
    return b.judgesDefault;
  }

  return { status: 'static_github_pages_mode', syncedTournaments: b.tournamentsAll?.length || 11, syncedCategories: 40, syncedMarks: 22590 };
}

function computeStaticHeadToHead(b, coupleKeyA, coupleKeyB) {
  const analyticsA = b.couplesMap[coupleKeyA];
  const analyticsB = b.couplesMap[coupleKeyB];
  if (!analyticsA || !analyticsB) return { error: 'Not found' };

  const partsA = (b.allParticipations || []).filter((p) => p.coupleKey === coupleKeyA);
  const partsBByCat = {};
  (b.allParticipations || [])
    .filter((p) => p.coupleKey === coupleKeyB)
    .forEach((p) => {
      partsBByCat[p.categoryId] = p;
    });

  const directEncounters = [];
  partsA.forEach((pa) => {
    const pb = partsBByCat[pa.categoryId];
    if (pb) {
      directEncounters.push({
        tournamentId: pa.tournamentId,
        competitionName: pa.competitionName,
        cityName: pa.cityName,
        dateStart: pa.dateStart,
        tournamentRank: pa.tournamentRank,
        categoryId: pa.categoryId,
        categoryName: pa.categoryName,
        style: pa.style,
        competitorsCount: pa.competitorsCount,
        placeA: pa.finalPlace,
        pointsA: pa.pointsEarned,
        placeB: pb.finalPlace,
        pointsB: pb.pointsEarned,
      });
    }
  });

  const winsA = directEncounters.filter((r) => Number(r.placeA) < Number(r.placeB)).length;
  const winsB = directEncounters.filter((r) => Number(r.placeB) < Number(r.placeA)).length;

  // Shared judge preferences
  const marksAByKey = {};
  (b.allSkatingMarks || [])
    .filter((m) => m.coupleKey === coupleKeyA)
    .forEach((m) => {
      marksAByKey[`${m.categoryId}_${m.roundCode}_${m.danceCode}_${m.judgeId}`] = m;
    });

  const judgeStats = {};
  (b.allSkatingMarks || [])
    .filter((m) => m.coupleKey === coupleKeyB)
    .forEach((mb) => {
      const ma = marksAByKey[`${mb.categoryId}_${mb.roundCode}_${mb.danceCode}_${mb.judgeId}`];
      if (ma) {
        if (!judgeStats[mb.judgeId]) {
          judgeStats[mb.judgeId] = {
            judgeId: mb.judgeId,
            judgeName: mb.judgeName,
            judgeCity: mb.judgeCity,
            sharedDancesJudged: 0,
            sumA: 0,
            sumB: 0,
            judgePreferredACount: 0,
            judgePreferredBCount: 0,
          };
        }
        const st = judgeStats[mb.judgeId];
        st.sharedDancesJudged += 1;
        st.sumA += Number(ma.markValue);
        st.sumB += Number(mb.markValue);
        if (Number(ma.markValue) < Number(mb.markValue)) st.judgePreferredACount += 1;
        if (Number(mb.markValue) < Number(ma.markValue)) st.judgePreferredBCount += 1;
      }
    });

  const sharedJudgePreferences = Object.values(judgeStats)
    .map((st) => ({
      ...st,
      avgMarkA: (st.sumA / st.sharedDancesJudged).toFixed(2),
      avgMarkB: (st.sumB / st.sharedDancesJudged).toFixed(2),
    }))
    .sort((x, y) => y.judgePreferredACount - x.judgePreferredACount);

  return {
    coupleA: analyticsA.couple,
    coupleB: analyticsB.couple,
    metricsA: analyticsA.summaryMetrics,
    metricsB: analyticsB.summaryMetrics,
    perDanceA: analyticsA.perDanceBreakdown,
    perDanceB: analyticsB.perDanceBreakdown,
    directEncountersCount: directEncounters.length,
    directWinsA: winsA,
    directWinsB: winsB,
    directEncounters,
    sharedJudgePreferences,
  };
}

// Switch Navigation Tabs
function switchTab(tabName) {
  const allTabs = ['overview', 'couples', 'dancers', 'h2h', 'tournaments', 'judges'];
  allTabs.forEach((t) => {
    const viewEl = document.getElementById(`tabView-${t}`);
    const btnEl = document.getElementById(`tabBtn-${t}`);
    if (viewEl) viewEl.classList.toggle('hidden', t !== tabName);
    if (btnEl) btnEl.classList.toggle('active', t === tabName);
  });
  const badgeEl = document.getElementById('activeModuleBadge');
  if (badgeEl && MODULE_TITLES[tabName]) {
    badgeEl.textContent = MODULE_TITLES[tabName];
  }
}

// Navigate directly to a specific couple profile
function openCoupleProfile(coupleKey) {
  switchTab('couples');
  loadCoupleAnalytics(coupleKey);
}

// Navigate directly to a specific dancer profile
function openDancerProfile(dancerId) {
  switchTab('dancers');
  loadDancerAnalytics(dancerId);
}

// Navigate directly to a specific category skating inspector
function openCategorySkating(categoryId) {
  switchTab('tournaments');
  loadCategorySkatingDetails(categoryId);
}

// Navigate directly to a specific judge profile
function openJudgeProfile(judgeId) {
  switchTab('judges');
  loadJudgeAnalytics(judgeId);
}

// Multi-token Universal Matcher (Every space-separated word must match somewhere in the combined searchable string)
function matchesUniversalQuery(queryStr, searchableFields) {
  const tokens = queryStr
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (tokens.length === 0) return true;
  const haystack = searchableFields.filter(Boolean).join(' ').toLowerCase();
  return tokens.every((tok) => haystack.includes(tok));
}

// Initialize Dashboard & Catalogs
async function initApplication() {
  try {
    const [overviewRes, catalogRes] = await Promise.all([
      fetchFtsApi('/api/overview'),
      fetchFtsApi('/api/catalog'),
    ]);

    appCatalog = {
      dancers: catalogRes.dancers || [],
      couples: catalogRes.couples || [],
      judges: catalogRes.judges || [],
      tournaments: catalogRes.tournaments || [],
    };
    renderOverviewTab(overviewRes);
    setupUniversalSearchBoxes();

    // Preload initial tabs so switching is instantaneous
    if (appCatalog.couples.length > 0) {
      const defaultCouple =
        appCatalog.couples.find((c) => c.coupleKey === '20000030_20000172')?.coupleKey ||
        appCatalog.couples[0].coupleKey;
      await loadCoupleAnalytics(defaultCouple);
    }
    if (appCatalog.dancers.length > 0) {
      const defaultDancer =
        appCatalog.dancers.find((d) => d.dancerId === '20000030')?.dancerId ||
        appCatalog.dancers[0].dancerId;
      await loadDancerAnalytics(defaultDancer);
    }
    if (appCatalog.couples.length >= 2) {
      const cA = appCatalog.couples.find((c) => c.coupleKey === '20000030_20000172') || appCatalog.couples[0];
      const cB = appCatalog.couples.find((c) => c.coupleKey === '20001045_20001088') || appCatalog.couples[1];
      selectH2hCouple('A', cA.coupleKey, `${cA.coupleName} [${cA.coupleKey}] — ${cA.clubName}`);
      selectH2hCouple('B', cB.coupleKey, `${cB.coupleName} [${cB.coupleKey}] — ${cB.clubName}`);
      await runHeadToHeadComparison();
    }
    await filterTournaments();
    await loadJudgeAnalytics();

    // Automatically check baza.polskitaniec.org/cal/archive in the background on startup (only on GitHub Pages!)
    // without resetting the user's currently selected couple or tab!
    if (window.location.hostname.includes('github.io') && typeof runBrowserLiveFtsSync === 'function') {
      runBrowserLiveFtsSync(0).then((liveRes) => {
        if (liveRes && liveRes.newTournamentsAdded > 0) {
          console.log('Dynamically pulled new live tournaments from baza.polskitaniec.org:', liveRes);
        }
      });
    }
  } catch (err) {
    console.error('Initialization error:', err);
  }
}

function selectH2hCouple(side, coupleKey, labelText) {
  const hiddenEl = document.getElementById(`h2hSelect${side}`);
  const inputEl = document.getElementById(`h2hSearchInput${side}`);
  const resEl = document.getElementById(`h2hSearchResults${side}`);
  if (hiddenEl) hiddenEl.value = coupleKey;
  if (inputEl) inputEl.value = labelText;
  if (resEl) resEl.classList.add('hidden');
}

// Setup All Universal Instant Search Inputs (Replacing old static dropdowns!)
function setupUniversalSearchBoxes() {
  // 1. Global Header Omni-Search (Searches Couples, Dancers, Judges, Tournaments, Clubs, Classes, IDs)
  const globalInput = document.getElementById('globalSearchInput');
  const globalDropdown = document.getElementById('globalSearchDropdown');

  if (globalInput && globalDropdown) {
    const renderGlobalResults = () => {
      const q = globalInput.value.trim();
      if (!q) {
        globalDropdown.classList.add('hidden');
        return;
      }

      const matchedCouples = appCatalog.couples
        .filter((c) =>
          matchesUniversalQuery(q, [
            c.coupleName,
            c.coupleKey,
            c.maleDancerId,
            c.femaleDancerId,
            c.starterId,
            c.clubName,
            c.cityName,
            c.ageGroup,
            `klasa ${c.classSt}`,
            `klasa ${c.classLa}`,
          ])
        )
        .slice(0, 8);

      const matchedDancers = appCatalog.dancers
        .filter((d) =>
          matchesUniversalQuery(q, [
            d.fullName,
            d.dancerId,
            d.wdsfMin,
            d.currentClub,
            d.currentClassSt,
            d.currentClassLa,
          ])
        )
        .slice(0, 8);

      const matchedJudges = (appCatalog.judges || [])
        .filter((j) =>
          matchesUniversalQuery(q, [j.fullName, j.judgeId, j.cityName, j.regionTag, j.licenseCategory, j.wdsfLicense])
        )
        .slice(0, 5);

      const matchedTournaments = (appCatalog.tournaments || [])
        .filter((t) =>
          matchesUniversalQuery(q, [t.competitionName, t.cityName, t.voivodeship, t.dateStart, t.tournamentRank, t.venueName])
        )
        .slice(0, 5);

      let html = '';

      if (matchedCouples.length > 0) {
        html += `<div class="px-3 py-1.5 text-[10px] font-mono uppercase text-indigo-400 bg-obsidian/95 sticky top-0">Pary Taneczne (ID_M_ID_K • Klub • Klasa) — ${matchedCouples.length} wyników</div>`;
        matchedCouples.forEach((c) => {
          html += `
            <div onclick="openCoupleProfile('${c.coupleKey}'); document.getElementById('globalSearchDropdown').classList.add('hidden');"
                 class="px-3.5 py-2 hover:bg-gray-800/80 cursor-pointer flex items-center justify-between text-xs">
              <div>
                <span class="font-bold text-white">${c.coupleName}</span>
                <span class="ml-2 px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-300 font-mono text-[10px]">${c.coupleKey}</span>
                <div class="text-gray-400 text-[11px]">${c.clubName} • ${c.ageGroup} (ST:${c.classSt} / LA:${c.classLa})</div>
              </div>
              <span class="text-[11px] font-mono text-amber-300">${c.startsCount || 0} startów • ${c.totalPoints || 0} pkt →</span>
            </div>`;
        });
      }

      if (matchedDancers.length > 0) {
        html += `<div class="px-3 py-1.5 text-[10px] font-mono uppercase text-amber-400 bg-obsidian/95 sticky top-0">Tancerze (Unikalny Numer Licencji FTS/PZST) — ${matchedDancers.length} wyników</div>`;
        matchedDancers.forEach((d) => {
          html += `
            <div onclick="openDancerProfile('${d.dancerId}'); document.getElementById('globalSearchDropdown').classList.add('hidden');"
                 class="px-3.5 py-2 hover:bg-gray-800/80 cursor-pointer flex items-center justify-between text-xs">
              <div>
                <span class="font-bold text-white">${d.fullName}</span>
                <span class="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 font-mono text-[10px]">ID: ${d.dancerId}</span>
                <div class="text-gray-400 text-[11px]">${d.currentClub} • Klasa: ${d.currentClassSt}/${d.currentClassLa}</div>
              </div>
              <span class="text-[11px] font-mono text-emerald-300">Partnerstw: ${d.partnershipsCount} →</span>
            </div>`;
        });
      }

      if (matchedJudges.length > 0) {
        html += `<div class="px-3 py-1.5 text-[10px] font-mono uppercase text-emerald-400 bg-obsidian/95 sticky top-0">Sędziowie FTS / PZST — ${matchedJudges.length} wyników</div>`;
        matchedJudges.forEach((j) => {
          html += `
            <div onclick="openJudgeProfile('${j.judgeId}'); document.getElementById('globalSearchDropdown').classList.add('hidden');"
                 class="px-3.5 py-2 hover:bg-gray-800/80 cursor-pointer flex items-center justify-between text-xs">
              <div>
                <span class="font-bold text-white">${j.fullName}</span>
                <span class="ml-2 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 font-mono text-[10px]">ID: ${j.judgeId}</span>
                <div class="text-gray-400 text-[11px]">${j.cityName} (${j.regionTag}) • ${j.licenseCategory}</div>
              </div>
              <span class="text-[11px] font-mono text-gray-300">Profil Biasu →</span>
            </div>`;
        });
      }

      if (matchedTournaments.length > 0) {
        html += `<div class="px-3 py-1.5 text-[10px] font-mono uppercase text-purple-400 bg-obsidian/95 sticky top-0">Turnieje Tańca Sportowego — ${matchedTournaments.length} wyników</div>`;
        matchedTournaments.forEach((t) => {
          html += `
            <div onclick="switchTab('tournaments'); document.getElementById('filterTourQuery').value='${t.cityName}'; filterTournaments(); document.getElementById('globalSearchDropdown').classList.add('hidden');"
                 class="px-3.5 py-2 hover:bg-gray-800/80 cursor-pointer flex items-center justify-between text-xs">
              <div>
                <span class="font-bold text-white">${t.competitionName}</span>
                <div class="text-gray-400 text-[11px]">${t.dateStart} • ${t.cityName} (${t.voivodeship}) • ${t.tournamentRank}</div>
              </div>
              <span class="text-[11px] font-mono text-indigo-300">Kategorie: ${t.categoriesCount} →</span>
            </div>`;
        });
      }

      if (!html) {
        html = `<div class="p-4 text-xs text-gray-400 text-center">Brak wyników dla "${globalInput.value}"</div>`;
      }
      globalDropdown.innerHTML = html;
      globalDropdown.classList.remove('hidden');
    };

    globalInput.addEventListener('input', renderGlobalResults);
    globalInput.addEventListener('focus', () => {
      if (globalInput.value.trim()) renderGlobalResults();
    });
  }

  // 2. Couple Search Box in Tab 2
  bindEntitySearchBox({
    inputId: 'coupleSearchInput',
    resultsId: 'coupleSearchResults',
    getItems: () => appCatalog.couples,
    matchFn: (c, q) =>
      matchesUniversalQuery(q, [
        c.coupleName,
        c.coupleKey,
        c.maleDancerId,
        c.femaleDancerId,
        c.starterId,
        c.clubName,
        c.cityName,
        c.ageGroup,
        c.classSt,
        c.classLa,
      ]),
    renderItemFn: (c) => `
      <div class="flex items-center justify-between">
        <div>
          <span class="font-bold text-white">${c.coupleName}</span>
          <span class="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 font-mono text-[10px]">${c.coupleKey}</span>
          <div class="text-[11px] text-gray-400">${c.clubName} (${c.cityName || 'Polska'}) • ${c.ageGroup}</div>
        </div>
        <div class="text-right font-mono text-[11px]">
          <div class="text-amber-300 font-bold">ST: ${c.classSt} | ${c.classPointsSt != null ? Number(c.classPointsSt).toFixed(2) + 'M' + (c.classPodiumsSt || 0) : (c.totalPoints || 0) + ' pkt'}</div>
          <div class="text-indigo-300 font-bold">LA: ${c.classLa} | ${c.classPointsLa != null ? Number(c.classPointsLa).toFixed(2) + 'M' + (c.classPodiumsLa || 0) : c.startsCount + ' startów'}</div>
        </div>
      </div>`,
    onSelectFn: (c, inputEl) => {
      inputEl.value = `${c.coupleName} [${c.coupleKey}] — ${c.clubName} (${c.ageGroup} kl. ${c.classSt}/${c.classLa})`;
      loadCoupleAnalytics(c.coupleKey);
    },
  });

  // 3. Dancer Search Box in Tab 3
  bindEntitySearchBox({
    inputId: 'dancerSearchInput',
    resultsId: 'dancerSearchResults',
    getItems: () => appCatalog.dancers,
    matchFn: (d, q) =>
      matchesUniversalQuery(q, [
        d.fullName,
        d.dancerId,
        d.wdsfMin,
        d.currentClub,
        d.currentClassSt,
        d.currentClassLa,
      ]),
    renderItemFn: (d) => `
      <div class="flex items-center justify-between">
        <div>
          <span class="font-bold text-white">${d.fullName}</span>
          <span class="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 font-mono text-[10px]">ID: ${d.dancerId}</span>
          <div class="text-[11px] text-gray-400">${d.currentClub} • Klasa ST/LA: ${d.currentClassSt}/${d.currentClassLa}</div>
        </div>
        <div class="text-right font-mono text-[11px]">
          <div class="text-indigo-300 font-bold">Partnerstw: ${d.partnershipsCount}</div>
          <div class="text-gray-400">Startów: ${d.totalStarts}</div>
        </div>
      </div>`,
    onSelectFn: (d, inputEl) => {
      inputEl.value = `${d.fullName} [ID: ${d.dancerId}] — ${d.currentClub}`;
      loadDancerAnalytics(d.dancerId);
    },
  });

  // 4. Head-to-Head Search Boxes A & B in Tab 4
  ['A', 'B'].forEach((side) => {
    bindEntitySearchBox({
      inputId: `h2hSearchInput${side}`,
      resultsId: `h2hSearchResults${side}`,
      getItems: () => appCatalog.couples,
      matchFn: (c, q) =>
        matchesUniversalQuery(q, [
          c.coupleName,
          c.coupleKey,
          c.maleDancerId,
          c.femaleDancerId,
          c.clubName,
          c.cityName,
          c.ageGroup,
          c.classSt,
          c.classLa,
        ]),
      renderItemFn: (c) => `
        <div class="flex items-center justify-between">
          <div>
            <span class="font-bold text-white">${c.coupleName}</span>
            <span class="ml-1.5 px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-300 font-mono text-[10px]">${c.coupleKey}</span>
            <div class="text-[11px] text-gray-400">${c.clubName} • ${c.ageGroup} (${c.classSt}/${c.classLa})</div>
          </div>
          <span class="font-mono text-[11px] text-amber-300">${c.totalPoints || 0} pkt</span>
        </div>`,
      onSelectFn: (c) => {
        selectH2hCouple(side, c.coupleKey, `${c.coupleName} [${c.coupleKey}] — ${c.clubName}`);
        runHeadToHeadComparison();
      },
    });
  });

  // 5. Judge Search Box in Tab 6
  bindEntitySearchBox({
    inputId: 'judgeSearchInput',
    resultsId: 'judgeSearchResults',
    getItems: () => appCatalog.judges || [],
    matchFn: (j, q) =>
      matchesUniversalQuery(q, [j.fullName, j.judgeId, j.cityName, j.regionTag, j.licenseCategory, j.wdsfLicense]),
    renderItemFn: (j) => `
      <div class="flex items-center justify-between">
        <div>
          <span class="font-bold text-white">${j.fullName}</span>
          <span class="ml-2 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 font-mono text-[10px]">ID: ${j.judgeId}</span>
          <div class="text-[11px] text-gray-400">${j.cityName} (${j.regionTag}) • ${j.licenseCategory}</div>
        </div>
        <span class="font-mono text-[11px] text-amber-300">Kategorii: ${j.categoriesJudgedCount || 0}</span>
      </div>`,
    onSelectFn: (j, inputEl) => {
      inputEl.value = `${j.fullName} [ID: ${j.judgeId}] — ${j.cityName} (${j.regionTag})`;
      loadJudgeAnalytics(j.judgeId);
    },
  });
}

function bindEntitySearchBox({ inputId, resultsId, getItems, matchFn, renderItemFn, onSelectFn }) {
  const inputEl = document.getElementById(inputId);
  const resultsEl = document.getElementById(resultsId);
  if (!inputEl || !resultsEl) return;

  const updateList = () => {
    const q = inputEl.value.trim();
    const allItems = getItems() || [];
    const matched = (q ? allItems.filter((item) => matchFn(item, q)) : allItems).slice(0, 35);

    if (matched.length === 0) {
      resultsEl.innerHTML = `<div class="p-3.5 text-xs text-gray-400 text-center">Brak wyników dla "${q}"</div>`;
    } else {
      resultsEl.innerHTML =
        `<div class="px-3 py-1 text-[10px] font-mono uppercase text-gray-400 bg-obsidian/95 sticky top-0">Znaleziono: ${
          q ? matched.length : `${matched.length} z ${allItems.length} (wpisz tekst, aby zawęzić)`
        }</div>` +
        matched
          .map(
            (item, idx) =>
              `<div data-idx="${idx}" class="entity-result-row px-3.5 py-2 hover:bg-gray-800/80 cursor-pointer text-xs transition">${renderItemFn(
                item
              )}</div>`
          )
          .join('');

      resultsEl.querySelectorAll('.entity-result-row').forEach((rowEl) => {
        rowEl.addEventListener('click', () => {
          const item = matched[Number(rowEl.getAttribute('data-idx'))];
          resultsEl.classList.add('hidden');
          onSelectFn(item, inputEl);
        });
      });
    }
    resultsEl.classList.remove('hidden');
  };

  inputEl.addEventListener('input', updateList);
  inputEl.addEventListener('focus', () => {
    inputEl.select();
    updateList();
  });

  document.addEventListener('click', (e) => {
    if (!inputEl.contains(e.target) && !resultsEl.contains(e.target)) {
      resultsEl.classList.add('hidden');
    }
  });
}

// ==================== TAB 1: RENDER OVERVIEW ====================
function renderOverviewTab(data) {
  const t = data.totals;
  const kpiItems = [
    { label: 'Tancerze (Unikalne ID)', val: t.dancersCount, color: 'text-amber-400' },
    { label: 'Unikalne Pary (ID_M_ID_K)', val: t.couplesCount, color: 'text-indigo-400' },
    { label: 'Aktywne Pary', val: t.activeCouplesCount, color: 'text-emerald-400' },
    { label: 'Turnieje w Bazie', val: t.tournamentsCount, color: 'text-white' },
    { label: 'Kategorie Turniejowe', val: t.categoriesCount, color: 'text-amber-300' },
    { label: 'Sędziowie FTS / PZST', val: t.judgesCount, color: 'text-indigo-300' },
    { label: 'Starty Turniejowe', val: t.participationsCount, color: 'text-emerald-300' },
    { label: 'Noty Skating & Recall', val: t.judgeMarksCount.toLocaleString('pl-PL'), color: 'text-amber-400' },
  ];

  document.getElementById('overviewKpiGrid').innerHTML = kpiItems
    .map(
      (k) => `
      <div class="glass-card rounded-xl p-3.5 border border-gray-800">
        <div class="text-[11px] text-gray-400 truncate">${k.label}</div>
        <div class="text-xl font-extrabold mono-num mt-1 ${k.color}">${k.val}</div>
      </div>`
    )
    .join('');

  // Top Couples Table
  document.getElementById('overviewTopCouplesBody').innerHTML = data.topCouples
    .map(
      (c, idx) => `
      <tr class="hover:bg-gray-800/50 transition">
        <td class="py-2.5 px-2">
          <div class="flex items-center gap-2">
            <span class="w-5 h-5 rounded-full bg-gray-800 text-amber-400 font-mono text-[11px] flex items-center justify-center font-bold">${idx + 1}</span>
            <div>
              <button onclick="openCoupleProfile('${c.coupleKey}')" class="font-bold text-white hover:text-amber-400 text-left transition">
                ${c.coupleName}
              </button>
              <div class="flex items-center gap-1.5 mt-0.5">
                <span class="font-mono text-[10px] px-1.5 py-0.2 rounded bg-gray-800 text-amber-300 border border-gray-700">${c.coupleKey}</span>
                <span class="text-[10px] px-1.5 py-0.2 rounded ${c.isActive ? 'bg-emerald-500/15 text-emerald-300' : 'bg-gray-700/50 text-gray-400'}">
                  ${c.isActive ? 'Aktywna' : 'Archiwalna'}
                </span>
              </div>
            </div>
          </div>
        </td>
        <td class="py-2.5 px-2 text-gray-300">${c.clubName}</td>
        <td class="py-2.5 px-2">
          <span class="px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 font-mono text-[11px]">${c.ageGroup} • ST:${c.classSt} LA:${c.classLa}</span>
        </td>
        <td class="py-2.5 px-2 text-right mono-num text-gray-200">${c.startsCount}</td>
        <td class="py-2.5 px-2 text-right mono-num font-bold ${c.avgPlace <= 2.5 ? 'text-emerald-400' : 'text-amber-300'}">${c.avgPlace}</td>
        <td class="py-2.5 px-2 text-right mono-num text-gray-200">
          <span class="text-amber-400 font-bold">${c.winsCount}x 1m</span> / ${c.podiumsCount} pod.
        </td>
        <td class="py-2.5 px-2 text-right mono-num font-bold text-white">${c.totalPoints} pkt</td>
      </tr>`
    )
    .join('');

  // Top Clubs Chart
  const clubsCtx = document.getElementById('overviewClubsChart').getContext('2d');
  if (overviewClubsChartInstance) overviewClubsChartInstance.destroy();
  const topClubsSlice = data.topClubs.slice(0, 7);

  overviewClubsChartInstance = new Chart(clubsCtx, {
    type: 'bar',
    data: {
      labels: topClubsSlice.map((cl) => cl.clubName.replace('UKS ', '').replace('UKTS ', '')),
      datasets: [
        {
          label: 'Suma Punktów Rankingowych',
          data: topClubsSlice.map((cl) => cl.totalClubPoints),
          backgroundColor: 'rgba(245, 158, 11, 0.75)',
          borderColor: '#F59E0B',
          borderWidth: 1,
          borderRadius: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#D1D5DB', font: { size: 11 } } },
      },
      scales: {
        x: { ticks: { color: '#9CA3AF', font: { size: 10 } }, grid: { color: 'rgba(55,65,81,0.3)' } },
        y: { ticks: { color: '#9CA3AF', font: { size: 10 } }, grid: { color: 'rgba(55,65,81,0.3)' } },
      },
    },
  });
}

// ==================== TAB 2: COUPLE DEEP ANALYTICS ====================
async function loadCoupleAnalytics(coupleKey) {
  const data = await fetchFtsApi(`/api/couple/${coupleKey}`);
  if (data.error) return;

  const c = data.couple;
  const m = data.summaryMetrics;
  const coupleSearchInputEl = document.getElementById('coupleSearchInput');
  if (coupleSearchInputEl) {
    coupleSearchInputEl.value = `${c.coupleName} [${c.coupleKey}] — ${c.clubName} (${c.ageGroup} kl. ${c.classSt}/${c.classLa})`;
  }

  // Quick links to both individual dancers
  document.getElementById('coupleQuickPartnerLinks').innerHTML = `
    <button onclick="openDancerProfile('${c.maleDancerId}')" class="px-3 py-1.5 rounded-xl text-xs font-mono bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30 transition">
      ♂ Partner: ${c.maleFullName} (ID: ${c.maleDancerId}) →
    </button>
    <button onclick="openDancerProfile('${c.femaleDancerId}')" class="px-3 py-1.5 rounded-xl text-xs font-mono bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 border border-indigo-500/30 transition">
      ♀ Partnerka: ${c.femaleFullName} (ID: ${c.femaleDancerId}) →
    </button>
  `;

  // Header Banner
  document.getElementById('coupleHeaderBanner').innerHTML = `
    <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
      <div>
        <div class="flex flex-wrap items-center gap-2">
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold">
            Klucz Pary: ${c.coupleKey}
          </span>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono ${c.isActive ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'}">
            ${c.isActive ? '● Para Aktywna' : '○ Archiwalne Zestawienie Par'}
          </span>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-gray-800 text-gray-300">
            Starter ID: ${c.starterId || 'PZST'}
          </span>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold">
            ${data.promotionProgress?.Standard?.officialSummaryCode || `ST: ${c.classSt}`}
          </span>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 font-bold">
            ${data.promotionProgress?.Latin?.officialSummaryCode || `LA: ${c.classLa}`}
          </span>
        </div>
        <h2 class="text-2xl font-extrabold text-white mt-2">${c.coupleName}</h2>
        <p class="text-xs text-gray-400 mt-1">
          Klub: <strong class="text-gray-200">${c.clubName} (${c.cityName})</strong> •
          Kategoria: <strong class="text-amber-300">${c.ageGroup}</strong> •
          Klasy: <strong class="text-indigo-300">Standard: ${c.classSt} / Latin: ${c.classLa}</strong> •
          Okres wspólnych startów: <span class="mono-num text-gray-200">${c.firstTournamentDate} → ${c.lastTournamentDate}</span>
        </p>
      </div>

      <div class="grid grid-cols-3 sm:grid-cols-6 gap-2.5">
        <div class="bg-obsidian/80 p-3 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Starty</div>
          <div class="text-lg font-extrabold mono-num text-white">${m.startsCount}</div>
        </div>
        <div class="bg-obsidian/80 p-3 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Śr. Miejsce</div>
          <div class="text-lg font-extrabold mono-num text-amber-400">${m.avgPlace}</div>
        </div>
        <div class="bg-obsidian/80 p-3 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Zwycięstwa</div>
          <div class="text-lg font-extrabold mono-num text-emerald-400">${m.winsCount}</div>
        </div>
        <div class="bg-obsidian/80 p-3 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Podium (1-3)</div>
          <div class="text-lg font-extrabold mono-num text-indigo-400">${m.podiumsCount}</div>
        </div>
        <div class="bg-obsidian/80 p-3 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Stabilność σ</div>
          <div class="text-lg font-extrabold mono-num text-gray-200">±${m.stabilityIndexStdDev}</div>
        </div>
        <div class="bg-obsidian/80 p-3 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Nachylenie Trendu</div>
          <div class="text-lg font-extrabold mono-num ${m.percentileTrendSlope >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
            ${m.percentileTrendSlope > 0 ? '+' : ''}${m.percentileTrendSlope}
          </div>
        </div>
      </div>
    </div>
  `;

  // Related Partnerships of either dancer
  const related = data.relatedPartnerships || [];
  document.getElementById('coupleRelatedPartnershipsCard').innerHTML = `
    <div class="flex items-center justify-between mb-2.5">
      <div class="text-xs font-bold text-indigo-300 uppercase font-mono">
        Powiązane Partnerstwa Tancerzy (${c.maleFullName} [${c.maleDancerId}] oraz ${c.femaleFullName} [${c.femaleDancerId}])
      </div>
      <span class="text-[11px] text-gray-400">Weryfikacja unikalności klucza pary przy zmianach partnerów</span>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      ${related
        .map(
          (rel) => `
        <div onclick="openCoupleProfile('${rel.coupleKey}')"
             class="p-3 rounded-xl border cursor-pointer transition ${
               rel.coupleKey === c.coupleKey
                 ? 'bg-amber-500/15 border-amber-500/50'
                 : 'bg-obsidian/70 border-gray-800 hover:border-indigo-500/50'
             }">
          <div class="flex items-center justify-between">
            <span class="font-mono text-[11px] text-amber-300">${rel.coupleKey}</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded ${
              rel.isActive ? 'bg-emerald-500/20 text-emerald-300' : 'bg-gray-800 text-gray-400'
            }">${rel.isActive ? 'Aktywna' : 'Archiwalna'}</span>
          </div>
          <div class="font-bold text-xs text-white mt-1">${rel.coupleName}</div>
          <div class="text-[11px] text-gray-400 mt-0.5">
            Okres: ${rel.firstTournamentDate} → ${rel.lastTournamentDate} • Startów: <strong class="text-gray-200">${rel.startsCount}</strong> (Śr. m-ce: ${rel.avgPlace || '-'})
          </div>
        </div>`
        )
        .join('')}
    </div>
  `;

  // Trend status badge
  const trendBadge = document.getElementById('coupleTrendBadge');
  trendBadge.textContent = m.trendSummary;
  trendBadge.className = `px-3 py-1 rounded-full text-xs font-semibold ${
    m.percentileTrendSlope >= 0.5
      ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
      : m.percentileTrendSlope <= -0.8
      ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
      : 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40'
  }`;

  // Trajectory Chart (Inverted Place + Percentile)
  const starts = data.startsHistory || [];
  const trajCtx = document.getElementById('coupleTrajectoryChart').getContext('2d');
  if (coupleTrajectoryChartInstance) coupleTrajectoryChartInstance.destroy();

  coupleTrajectoryChartInstance = new Chart(trajCtx, {
    type: 'line',
    data: {
      labels: starts.map((s) => `${s.dateStart.slice(5)} ${s.tournamentCity} (${s.style.slice(0, 2)})`),
      datasets: [
        {
          label: 'Zajęte Miejsce (1 = Zwycięstwo)',
          data: starts.map((s) => s.finalPlace),
          borderColor: '#F59E0B',
          backgroundColor: 'rgba(245, 158, 11, 0.2)',
          borderWidth: 3,
          pointRadius: 5,
          pointBackgroundColor: '#F59E0B',
          tension: 0.25,
          yAxisID: 'yPlace',
        },
        {
          label: 'Percentyl Pokonanych Par (%)',
          data: starts.map((s) => s.fieldPercentile),
          borderColor: '#6366F1',
          backgroundColor: 'rgba(99, 102, 241, 0.15)',
          borderDash: [5, 4],
          borderWidth: 2,
          pointRadius: 4,
          fill: true,
          tension: 0.3,
          yAxisID: 'yPct',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#E5E7EB', font: { size: 11 } } },
      },
      scales: {
        x: { ticks: { color: '#9CA3AF', font: { size: 10 } }, grid: { color: 'rgba(55,65,81,0.25)' } },
        yPlace: {
          type: 'linear',
          position: 'left',
          reverse: true,
          min: 1,
          suggestedMax: 7,
          title: { display: true, text: 'Miejsce w Turnieju (1 na górze)', color: '#F59E0B', font: { size: 11 } },
          ticks: { stepSize: 1, color: '#F59E0B' },
          grid: { color: 'rgba(55,65,81,0.3)' },
        },
        yPct: {
          type: 'linear',
          position: 'right',
          min: 0,
          max: 100,
          title: { display: true, text: 'Percentyl Kategorii (%)', color: '#818CF8', font: { size: 11 } },
          ticks: { color: '#818CF8' },
          grid: { drawOnChartArea: false },
        },
      },
    },
  });

  // Per-Dance Breakdown Radar Chart
  const dances = data.perDanceBreakdown || [];
  const radarCtx = document.getElementById('coupleDanceRadarChart').getContext('2d');
  if (coupleDanceRadarChartInstance) coupleDanceRadarChartInstance.destroy();

  // Convert average skating mark (1..6) into a 0..100 dominance score: (7 - avgMark) / 6 * 100
  const danceDominanceScores = dances.map((d) =>
    d.avgJudgeMark ? Math.round(((7.0 - d.avgJudgeMark) / 6.0) * 100) : 50
  );

  coupleDanceRadarChartInstance = new Chart(radarCtx, {
    type: 'radar',
    data: {
      labels: dances.map((d) => d.danceCode),
      datasets: [
        {
          label: 'Indeks Siły Tańca w Finale (Skating)',
          data: danceDominanceScores,
          backgroundColor: 'rgba(245, 158, 11, 0.25)',
          borderColor: '#F59E0B',
          pointBackgroundColor: '#F59E0B',
          borderWidth: 2,
        },
        {
          label: 'Skuteczność Awansu z Półfinału (Recall %)',
          data: dances.map((d) => d.prelimRecallPercent || 0),
          backgroundColor: 'rgba(99, 102, 241, 0.2)',
          borderColor: '#6366F1',
          pointBackgroundColor: '#6366F1',
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0,
          max: 100,
          angleLines: { color: 'rgba(75, 85, 99, 0.4)' },
          grid: { color: 'rgba(75, 85, 99, 0.4)' },
          pointLabels: { color: '#F3F4F6', font: { size: 11, weight: 'bold' } },
          ticks: { display: false },
        },
      },
      plugins: {
        legend: { labels: { color: '#D1D5DB', font: { size: 10 } } },
      },
    },
  });

  document.getElementById('coupleDanceStatsList').innerHTML = dances
    .map(
      (d) => `
      <div class="flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-obsidian/70 border border-gray-800">
        <span class="font-semibold text-gray-200">${d.danceFullName}</span>
        <div class="flex items-center gap-3 mono-num">
          <span class="text-amber-400">Śr. Nota: <strong>${d.avgJudgeMark || '-'}</strong></span>
          <span class="text-emerald-400">1. miejsc: <strong>${d.firstPlaceMarksCount}</strong></span>
          <span class="text-indigo-300">Recall: <strong>${d.prelimRecallPercent}%</strong></span>
        </div>
      </div>`
    )
    .join('');

  // Class Promotion Progress Bars
  const promo = data.promotionProgress;
  document.getElementById('couplePromotionContainer').innerHTML = ['Standard', 'Latin']
    .map((styleKey) => {
      const p = promo[styleKey];
      return `
        <div class="p-4 rounded-xl bg-obsidian/80 border border-gray-800 space-y-2.5">
          <div class="flex items-center justify-between">
            <span class="font-bold text-sm text-white">${styleKey} — Klasa ${p.currentClass} → Awans do Klasy ${p.targetClass}</span>
            <span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 text-xs font-mono font-bold border border-amber-500/30">${p.officialSummaryCode || `${p.earnedPoints}M${p.earnedPodiums}`}</span>
          </div>
          <div>
            <div class="flex justify-between text-[11px] text-gray-400 mb-1">
              <span>Punkty Klasowe (Pkt. kl.): <strong class="text-white mono-num">${Number(p.earnedPoints).toFixed(2)} / ${p.requiredPoints} pkt</strong></span>
              <span class="mono-num text-amber-400">${p.pointsCompletionPct}%</span>
            </div>
            <div class="w-full h-2.5 bg-gray-800 rounded-full overflow-hidden">
              <div class="h-full bg-gradient-to-r from-amber-500 to-emerald-400 rounded-full" style="width: ${p.pointsCompletionPct}%"></div>
            </div>
          </div>
          <div>
            <div class="flex justify-between text-[11px] text-gray-400 mb-1">
              <span>Miejsca na Podium (M): <strong class="text-white mono-num">${p.earnedPodiums} / ${p.requiredPodiums} (M${p.earnedPodiums})</strong></span>
              <span class="mono-num text-indigo-300">${p.podiumsCompletionPct}%</span>
            </div>
            <div class="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
              <div class="h-full bg-indigo-500 rounded-full" style="width: ${p.podiumsCompletionPct}%"></div>
            </div>
          </div>
        </div>`;
    })
    .join('');

  // Judge Evaluations Table for this Couple + Interactive Judge History Drill-Down
  window.currentCoupleKey = coupleKey;
  const judgeEvals = data.judgeEvaluations || [];
  const specificJudgeSelect = document.getElementById('coupleSpecificJudgeDropdown');
  if (specificJudgeSelect) {
    if (judgeEvals.length > 0) {
      specificJudgeSelect.innerHTML = judgeEvals
        .map(
          (j) =>
            `<option value="${j.judgeId}">${j.judgeName} (${j.judgeCity}) — Śr: ${j.avgMarkGiven} (Δ ${j.biasDelta > 0 ? '+' : ''}${j.biasDelta})</option>`
        )
        .join('');
    } else {
      specificJudgeSelect.innerHTML = `<option value="">Brak ocen sędziowskich dla tej pary</option>`;
    }
  }

  document.getElementById('coupleJudgesTableBody').innerHTML =
    judgeEvals.length > 0
      ? judgeEvals
          .map((j) => {
            const delta = Number(j.biasDelta);
            return `
        <tr id="coupleJudgeRow-${j.judgeId}"
            onclick="loadCoupleJudgeHistory('${coupleKey}', '${j.judgeId}')"
            class="hover:bg-amber-500/15 cursor-pointer transition">
          <td class="py-2 px-2">
            <div class="font-bold text-white hover:text-amber-400">${j.judgeName}</div>
            <div class="text-[10px] font-mono text-gray-400">${j.judgeCity} • ${j.regionTag}</div>
          </td>
          <td class="py-2 px-1.5 text-right mono-num text-gray-300">${j.totalSkatingMarksGiven}</td>
          <td class="py-2 px-1.5 text-right mono-num font-bold text-amber-300">${j.avgMarkGiven}</td>
          <td class="py-2 px-1.5 text-right mono-num text-gray-400">${j.avgPanelConsensus}</td>
          <td class="py-2 px-1.5 text-right mono-num font-bold ${delta < 0 ? 'text-emerald-400' : delta > 0 ? 'text-rose-400' : 'text-gray-300'}">
            ${delta > 0 ? '+' : ''}${j.biasDelta}
          </td>
        </tr>`;
          })
          .join('')
      : `<tr><td colspan="5" class="py-8 text-center text-xs text-gray-400">Brak szczegółowych arkuszy ocen sędziowskich (Skating / Recall) dla tej pary.</td></tr>`;

  if (judgeEvals.length > 0) {
    await loadCoupleJudgeHistory(coupleKey, judgeEvals[0].judgeId);
  } else {
    if (coupleSingleJudgeHistoryChartInstance) coupleSingleJudgeHistoryChartInstance.destroy();
    if (coupleSingleJudgeDanceChartInstance) coupleSingleJudgeDanceChartInstance.destroy();
    document.getElementById('coupleJudgeDrilldownHeader').innerHTML = `<div class="text-xs text-gray-400">Brak danych sędziowskich dla tej pary.</div>`;
    document.getElementById('coupleSingleJudgeEventsList').innerHTML = '';
  }

  // Starts History Table
  document.getElementById('coupleStartsTableBody').innerHTML = starts
    .slice()
    .reverse()
    .map(
      (s) => `
      <tr class="hover:bg-gray-800/50 transition">
        <td class="py-2.5 px-2 mono-num text-gray-300">${s.dateStart}</td>
        <td class="py-2.5 px-2">
          <div class="font-bold text-white">${s.competitionName}</div>
          <div class="text-[11px] text-gray-400">${s.tournamentCity} (${s.tournamentRank})</div>
        </td>
        <td class="py-2.5 px-2 text-gray-200">${s.categoryName} <span class="text-indigo-300 font-mono">(${s.style})</span></td>
        <td class="py-2.5 px-2 text-right mono-num">
          <span class="px-2 py-0.5 rounded font-bold ${
            s.finalPlace === 1
              ? 'bg-amber-500/25 text-amber-300 border border-amber-500/40'
              : s.finalPlace <= 3
              ? 'bg-emerald-500/20 text-emerald-300'
              : 'bg-gray-800 text-gray-200'
          }">${s.finalPlace} / ${s.competitorsCount}</span>
        </td>
        <td class="py-2.5 px-2 text-right mono-num text-indigo-300">${s.fieldPercentile}%</td>
        <td class="py-2.5 px-2 text-right mono-num font-bold text-emerald-400">
          +${s.pointsEarned} pkt ${s.podiumCredit ? '🏆' : ''}
        </td>
        <td class="py-2.5 px-2 text-right">
          <button onclick="openCategorySkating('${s.categoryId}')" class="px-2 py-1 rounded-lg bg-indigo-600/25 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-500/30 text-[11px] font-semibold">
            Skating →
          </button>
        </td>
      </tr>`
    )
    .join('');
}

// Drill-down: Historical Evaluation of a Specific Couple by a Specific Judge
async function loadCoupleJudgeHistory(coupleKey, judgeId) {
  if (!coupleKey || !judgeId) return;
  const selectEl = document.getElementById('coupleSpecificJudgeDropdown');
  if (selectEl) selectEl.value = judgeId;

  // Highlight active row in left table
  document.querySelectorAll('[id^="coupleJudgeRow-"]').forEach((el) => {
    el.classList.remove('bg-amber-500/20', 'border-l-2', 'border-amber-400');
  });
  const activeRow = document.getElementById(`coupleJudgeRow-${judgeId}`);
  if (activeRow) activeRow.classList.add('bg-amber-500/20', 'border-l-2', 'border-amber-400');

  const data = await fetchFtsApi(`/api/couple/${encodeURIComponent(coupleKey)}/judge/${encodeURIComponent(judgeId)}`);
  if (data.error) return;

  const j = data.judge;
  const c = data.couple;
  const events = data.timelineEvents || [];
  const perDance = data.perDanceSummary || [];

  document.getElementById('coupleJudgeDrilldownHeader').innerHTML = `
    <div>
      <div class="flex items-center gap-2">
        <span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono text-[11px] font-bold">Sędzia: ${j.fullName} (ID: ${j.judgeId})</span>
        <span class="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 font-mono text-[11px]">${j.cityName} • ${j.regionTag}</span>
      </div>
      <div class="text-xs text-gray-300 mt-1">
        Historia oceniania pary <strong class="text-white">${c.coupleName}</strong> na <strong class="text-amber-400">${events.length}</strong> wspólnych kategoriach turniejowych
      </div>
    </div>
    <button onclick="openJudgeProfile('${j.judgeId}')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs text-amber-300 border border-gray-700 transition">
      Pełny Profil Sędziego →
    </button>
  `;

  // Chart 1: Chronological Judge Skating Mark vs Panel Skating Mark & Final Place
  const histCtx = document.getElementById('coupleSingleJudgeHistoryChart').getContext('2d');
  if (coupleSingleJudgeHistoryChartInstance) coupleSingleJudgeHistoryChartInstance.destroy();

  coupleSingleJudgeHistoryChartInstance = new Chart(histCtx, {
    type: 'line',
    data: {
      labels: events.map((ev) => `${ev.dateStart.slice(5)} ${ev.cityName} (${ev.style.slice(0, 2)})`),
      datasets: [
        {
          label: `Śr. Nota Sędziego (${j.fullName})`,
          data: events.map((ev) => ev.judgeSkatingAvg || ev.finalPlace),
          borderColor: '#F59E0B',
          backgroundColor: 'rgba(245, 158, 11, 0.25)',
          borderWidth: 3,
          pointRadius: 5,
          pointBackgroundColor: '#F59E0B',
          tension: 0.25,
        },
        {
          label: 'Średnia Całego Panelu Sędziowskiego',
          data: events.map((ev) => ev.panelSkatingAvg || ev.finalPlace),
          borderColor: '#6366F1',
          backgroundColor: 'rgba(99, 102, 241, 0.15)',
          borderDash: [5, 4],
          borderWidth: 2,
          pointRadius: 4,
          pointBackgroundColor: '#6366F1',
          tension: 0.25,
        },
        {
          label: 'Oficjalne Miejsce Końcowe Pary',
          data: events.map((ev) => ev.finalPlace),
          borderColor: '#10B981',
          borderWidth: 1.5,
          pointRadius: 3,
          borderDash: [2, 2],
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#9CA3AF', font: { size: 10 } }, grid: { color: 'rgba(55,65,81,0.3)' } },
        y: {
          reverse: true,
          min: 1,
          suggestedMax: 6,
          ticks: { stepSize: 1, color: '#F59E0B' },
          title: { display: true, text: 'Nota / Miejsce (1 = Najwyżej)', color: '#F59E0B', font: { size: 10 } },
          grid: { color: 'rgba(55,65,81,0.3)' },
        },
      },
      plugins: {
        legend: { labels: { color: '#E5E7EB', font: { size: 10 } } },
      },
    },
  });

  // Chart 2: Per-Dance comparison for this judge vs panel
  const danceCtx = document.getElementById('coupleSingleJudgeDanceChart').getContext('2d');
  if (coupleSingleJudgeDanceChartInstance) coupleSingleJudgeDanceChartInstance.destroy();

  coupleSingleJudgeDanceChartInstance = new Chart(danceCtx, {
    type: 'bar',
    data: {
      labels: perDance.map((d) => d.danceCode),
      datasets: [
        {
          label: `Sędzia ${j.fullName.split(' ')[1] || j.fullName}`,
          data: perDance.map((d) => d.judgeDanceAvg || 0),
          backgroundColor: 'rgba(245, 158, 11, 0.8)',
          borderRadius: 4,
        },
        {
          label: 'Konsensus Panelu',
          data: perDance.map((d) => d.panelOverallDanceAvg || 0),
          backgroundColor: 'rgba(99, 102, 241, 0.65)',
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#D1D5DB', font: { size: 10 } }, grid: { display: false } },
        y: {
          min: 0,
          suggestedMax: 6,
          ticks: { stepSize: 1, color: '#9CA3AF', font: { size: 10 } },
          title: { display: true, text: 'Śr. Miejsce w Tańcu (mniej = lepiej)', color: '#9CA3AF', font: { size: 9 } },
          grid: { color: 'rgba(55,65,81,0.3)' },
        },
      },
      plugins: {
        legend: { labels: { color: '#D1D5DB', font: { size: 10 } } },
      },
    },
  });

  // Detailed list of tournaments & per-dance marks given by this judge
  document.getElementById('coupleSingleJudgeEventsList').innerHTML = events
    .slice()
    .reverse()
    .map((ev) => {
      const skatingMarks = (ev.danceMarks || []).filter((dm) => dm.markType === 'SKATING');
      const delta = Number(ev.skatingBiasDelta || 0);
      return `
        <div class="p-2.5 rounded-xl bg-slateCard/90 border border-gray-800 space-y-1.5">
          <div class="flex items-center justify-between">
            <div>
              <span class="font-mono text-[11px] text-amber-400 font-bold">${ev.dateStart}</span>
              <span class="font-bold text-white ml-1">${ev.cityName} — ${ev.categoryName}</span>
            </div>
            <span class="font-mono text-[11px] px-1.5 py-0.5 rounded ${
              delta < 0 ? 'bg-emerald-500/20 text-emerald-300' : delta > 0 ? 'bg-rose-500/20 text-rose-300' : 'bg-gray-800 text-gray-300'
            }">
              Sędzia (${ev.judgeLetter || 'A'}): <strong>${ev.judgeSkatingAvg || '-'}</strong> vs Panel: ${ev.panelSkatingAvg || '-'} (Δ ${delta > 0 ? '+' : ''}${delta})
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-1.5">
            ${skatingMarks
              .map(
                (dm) => `
              <span class="px-2 py-0.5 rounded bg-obsidian border border-gray-700 font-mono text-[10px]">
                <strong class="text-indigo-300">${dm.danceCode}:</strong>
                <span class="${dm.markValue === 1 ? 'text-amber-400 font-bold' : 'text-white'}">${dm.markValue}m</span>
                <span class="text-gray-500">(panel ${dm.panelDanceAvg})</span>
              </span>`
              )
              .join('')}
            ${
              ev.judgeRecallPct != null
                ? `<span class="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 font-mono text-[10px]">
                    Krzyżyki 1/2F: ${ev.judgeRecallPct}%
                  </span>`
                : `<span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-300 font-mono text-[10px]">
                    Finał bezpośredni (Skating)
                  </span>`
            }
          </div>
        </div>`;
    })
    .join('');
}

// ==================== TAB 3: DANCER PROFILE & PARTNER HISTORY ====================
async function loadDancerAnalytics(dancerId) {
  const data = await fetchFtsApi(`/api/dancer/${dancerId}`);
  if (data.error) return;

  const d = data.dancer;
  const dancerSearchInputEl = document.getElementById('dancerSearchInput');
  if (dancerSearchInputEl) {
    dancerSearchInputEl.value = `${d.fullName} [ID: ${d.dancerId}] — ${d.currentClub}`;
  }
  document.getElementById('dancerProfileHeader').innerHTML = `
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div>
        <div class="flex items-center gap-2">
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold">
            Licencja FTS/PZST ID: ${d.dancerId}
          </span>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">
            WDSF MIN: ${d.wdsfMin || 'Brak'}
          </span>
        </div>
        <h2 class="text-2xl font-extrabold text-white mt-2">${d.fullName}</h2>
        <p class="text-xs text-gray-400 mt-1">
          Obecny klub: <strong class="text-white">${d.currentClub}</strong> •
          Klasa Standard: <strong class="text-amber-300">${d.currentClassSt}</strong> •
          Klasa Latin: <strong class="text-indigo-300">${d.currentClassLa}</strong>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <div class="bg-obsidian/80 p-3.5 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Unikalnych Partnerstw</div>
          <div class="text-xl font-extrabold mono-num text-amber-400">${data.partnershipsCount}</div>
        </div>
        <div class="bg-obsidian/80 p-3.5 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Wszystkich Startów</div>
          <div class="text-xl font-extrabold mono-num text-white">${data.careerStartsCount}</div>
        </div>
        <div class="bg-obsidian/80 p-3.5 rounded-xl border border-gray-800">
          <div class="text-[10px] text-gray-400 uppercase">Tendencja Kariery</div>
          <div class="text-xs font-bold text-emerald-400 mt-1">${data.careerTrendStatus}</div>
        </div>
      </div>
    </div>
  `;

  // Partnerships Cards
  const colors = ['border-amber-500/50 bg-amber-500/10', 'border-indigo-500/50 bg-indigo-500/10', 'border-emerald-500/50 bg-emerald-500/10'];
  document.getElementById('dancerPartnershipsGrid').innerHTML = (data.partnershipsHistory || [])
    .map(
      (p, idx) => `
      <div class="p-4 rounded-xl border ${colors[idx % colors.length]} flex flex-col justify-between gap-3">
        <div>
          <div class="flex items-center justify-between">
            <span class="font-mono text-xs font-bold text-amber-300">Klucz Pary: ${p.coupleKey}</span>
            <span class="px-2 py-0.5 rounded text-[11px] font-mono ${
              p.isActive ? 'bg-emerald-500/20 text-emerald-300' : 'bg-gray-800 text-gray-400'
            }">${p.isActive ? '● Aktualna Para' : '○ Archiwalna Para'}</span>
          </div>
          <h4 class="text-base font-bold text-white mt-1.5">${p.coupleName}</h4>
          <p class="text-xs text-gray-300 mt-0.5">
            Partner(ka): <button onclick="openDancerProfile('${p.partnerDancerId}')" class="text-amber-400 hover:underline font-semibold">${p.partnerFullName} (ID: ${p.partnerDancerId})</button>
          </p>
          <p class="text-[11px] text-gray-400 mt-1">
            Klub: ${p.clubName} • Okres: <span class="mono-num text-gray-200">${p.firstTournamentDate} → ${p.lastTournamentDate}</span>
          </p>
        </div>

        <div class="grid grid-cols-4 gap-2 pt-2 border-t border-gray-800/80 text-center text-xs">
          <div>
            <div class="text-[10px] text-gray-400">Starty</div>
            <div class="font-bold mono-num text-white">${p.startsCount}</div>
          </div>
          <div>
            <div class="text-[10px] text-gray-400">Śr. Miejsce</div>
            <div class="font-bold mono-num text-amber-400">${p.avgPlace || '-'}</div>
          </div>
          <div>
            <div class="text-[10px] text-gray-400">Zwycięstwa</div>
            <div class="font-bold mono-num text-emerald-400">${p.winsCount || 0}</div>
          </div>
          <div>
            <div class="text-[10px] text-gray-400">Punkty</div>
            <div class="font-bold mono-num text-indigo-300">${p.totalPoints || 0}</div>
          </div>
        </div>

        <button onclick="openCoupleProfile('${p.coupleKey}')" class="w-full py-1.5 rounded-lg bg-obsidian/80 hover:bg-obsidian text-xs font-semibold text-amber-300 border border-gray-700 transition">
          Otwórz Analitykę Tej Pary (${p.coupleKey}) →
        </button>
      </div>`
    )
    .join('');

  // Career Chart Across Partnerships
  const timeline = data.timelineStarts || [];
  const careerCtx = document.getElementById('dancerCareerChart').getContext('2d');
  if (dancerCareerChartInstance) dancerCareerChartInstance.destroy();

  const partnerColors = ['#F59E0B', '#6366F1', '#10B981', '#EC4899'];
  const uniqueKeys = [...new Set(timeline.map((s) => s.coupleKey))];

  dancerCareerChartInstance = new Chart(careerCtx, {
    type: 'line',
    data: {
      labels: timeline.map((s) => `${s.dateStart} (${s.style.slice(0, 2)})`),
      datasets: [
        {
          label: 'Miejsce Turniejowe Tancerza (Kolor punktu = Partnerstwo)',
          data: timeline.map((s) => s.finalPlace),
          borderColor: 'rgba(156, 163, 175, 0.5)',
          borderWidth: 2,
          pointRadius: 6,
          pointBackgroundColor: timeline.map((s) => partnerColors[uniqueKeys.indexOf(s.coupleKey) % partnerColors.length]),
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#9CA3AF', font: { size: 10 } }, grid: { color: 'rgba(55,65,81,0.3)' } },
        y: {
          reverse: true,
          min: 1,
          suggestedMax: 7,
          ticks: { stepSize: 1, color: '#F59E0B' },
          title: { display: true, text: 'Miejsce (1 na górze)', color: '#F59E0B' },
          grid: { color: 'rgba(55,65,81,0.3)' },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const item = timeline[ctx.dataIndex];
              return `Para: ${item.coupleName} [${item.coupleKey}]\nTurniej: ${item.competitionName}`;
            },
          },
        },
      },
    },
  });
}

// ==================== TAB 4: HEAD-TO-HEAD COMPARISON ====================
async function runHeadToHeadComparison() {
  const keyA = document.getElementById('h2hSelectA').value;
  const keyB = document.getElementById('h2hSelectB').value;
  if (!keyA || !keyB) return;

  const data = await fetchFtsApi(`/api/head-to-head?coupleA=${encodeURIComponent(keyA)}&coupleB=${encodeURIComponent(keyB)}`);
  if (data.error) return;

  const cA = data.coupleA;
  const cB = data.coupleB;
  const mA = data.metricsA;
  const mB = data.metricsB;

  document.getElementById('h2hSummaryContainer').innerHTML = `
    <div class="lg:col-span-5 glass-card rounded-2xl p-5 border border-amber-500/40">
      <span class="text-xs font-mono text-amber-400">PARA A • ${cA.coupleKey}</span>
      <h4 class="text-lg font-extrabold text-white mt-1">${cA.coupleName}</h4>
      <p class="text-xs text-gray-400">${cA.clubName} • Klasa ${cA.classSt}/${cA.classLa}</p>
      <div class="grid grid-cols-3 gap-2 mt-4 text-center">
        <div class="bg-obsidian p-2.5 rounded-xl">
          <div class="text-[10px] text-gray-400">Śr. Miejsce</div>
          <div class="text-base font-bold mono-num text-amber-400">${mA.avgPlace}</div>
        </div>
        <div class="bg-obsidian p-2.5 rounded-xl">
          <div class="text-[10px] text-gray-400">Zwycięstwa</div>
          <div class="text-base font-bold mono-num text-emerald-400">${mA.winsCount}</div>
        </div>
        <div class="bg-obsidian p-2.5 rounded-xl">
          <div class="text-[10px] text-gray-400">Podium</div>
          <div class="text-base font-bold mono-num text-white">${mA.podiumsCount}</div>
        </div>
      </div>
    </div>

    <div class="lg:col-span-2 glass-card rounded-2xl p-5 flex flex-col items-center justify-center text-center">
      <div class="text-xs font-mono uppercase text-gray-400">Bilans Bezpośredni</div>
      <div class="text-3xl font-extrabold mono-num mt-2">
        <span class="text-amber-400">${data.directWinsA}</span>
        <span class="text-gray-600 mx-1">:</span>
        <span class="text-indigo-400">${data.directWinsB}</span>
      </div>
      <div class="text-[11px] text-gray-400 mt-1">Wspólnych kategorii: ${data.directEncountersCount}</div>
    </div>

    <div class="lg:col-span-5 glass-card rounded-2xl p-5 border border-indigo-500/40">
      <span class="text-xs font-mono text-indigo-400">PARA B • ${cB.coupleKey}</span>
      <h4 class="text-lg font-extrabold text-white mt-1">${cB.coupleName}</h4>
      <p class="text-xs text-gray-400">${cB.clubName} • Klasa ${cB.classSt}/${cB.classLa}</p>
      <div class="grid grid-cols-3 gap-2 mt-4 text-center">
        <div class="bg-obsidian p-2.5 rounded-xl">
          <div class="text-[10px] text-gray-400">Śr. Miejsce</div>
          <div class="text-base font-bold mono-num text-indigo-400">${mB.avgPlace}</div>
        </div>
        <div class="bg-obsidian p-2.5 rounded-xl">
          <div class="text-[10px] text-gray-400">Zwycięstwa</div>
          <div class="text-base font-bold mono-num text-emerald-400">${mB.winsCount}</div>
        </div>
        <div class="bg-obsidian p-2.5 rounded-xl">
          <div class="text-[10px] text-gray-400">Podium</div>
          <div class="text-base font-bold mono-num text-white">${mB.podiumsCount}</div>
        </div>
      </div>
    </div>
  `;

  const encounters = data.directEncounters || [];
  document.getElementById('h2hDirectEncountersList').innerHTML =
    encounters.length === 0
      ? `<div class="text-xs text-gray-400 py-6 text-center">Brak bezpośrednich pojedynków tych dwóch par w tej samej kategorii.</div>`
      : encounters
          .map(
            (e) => `
          <div class="p-3 rounded-xl bg-obsidian/80 border border-gray-800 flex items-center justify-between text-xs">
            <div>
              <div class="font-bold text-white">${e.competitionName}</div>
              <div class="text-[11px] text-gray-400">${e.dateStart} • ${e.cityName} • ${e.categoryName}</div>
            </div>
            <div class="flex items-center gap-3 mono-num">
              <span class="px-2 py-1 rounded ${e.placeA < e.placeB ? 'bg-amber-500/25 text-amber-300 font-bold' : 'bg-gray-800 text-gray-400'}">
                Para A: ${e.placeA} m.
              </span>
              <span class="px-2 py-1 rounded ${e.placeB < e.placeA ? 'bg-indigo-500/25 text-indigo-300 font-bold' : 'bg-gray-800 text-gray-400'}">
                Para B: ${e.placeB} m.
              </span>
            </div>
          </div>`
          )
          .join('');

  const judgePrefs = data.sharedJudgePreferences || [];
  document.getElementById('h2hJudgesPreferenceTable').innerHTML =
    judgePrefs.length === 0
      ? `<div class="text-xs text-gray-400 py-6 text-center">Brak wspólnych ocen finałowych Skating dla wybranej pary.</div>`
      : `
      <table class="w-full text-left border-collapse text-xs">
        <thead>
          <tr class="border-b border-gray-800 text-gray-400 font-mono text-[11px]">
            <th class="py-2 px-2">Sędzia</th>
            <th class="py-2 px-2 text-right">Wspólne Tańce</th>
            <th class="py-2 px-2 text-right text-amber-400">Śr. Para A</th>
            <th class="py-2 px-2 text-right text-indigo-400">Śr. Para B</th>
            <th class="py-2 px-2 text-right">Wyżej A : Wyżej B</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-800/60">
          ${judgePrefs
            .map(
              (jp) => `
            <tr>
              <td class="py-2 px-2 font-semibold text-white">${jp.judgeName} <span class="text-gray-400 font-normal">(${jp.judgeCity})</span></td>
              <td class="py-2 px-2 text-right mono-num">${jp.sharedDancesJudged}</td>
              <td class="py-2 px-2 text-right mono-num font-bold text-amber-300">${jp.avgMarkA}</td>
              <td class="py-2 px-2 text-right mono-num font-bold text-indigo-300">${jp.avgMarkB}</td>
              <td class="py-2 px-2 text-right mono-num">
                <span class="text-amber-400 font-bold">${jp.judgePreferredACount}</span> : <span class="text-indigo-400 font-bold">${jp.judgePreferredBCount}</span>
              </td>
            </tr>`
            )
            .join('')}
        </tbody>
      </table>`;
}

// ==================== TAB 5: TOURNAMENTS & SKATING INSPECTOR ====================
function resetTournamentFilters() {
  document.getElementById('filterTourQuery').value = '';
  document.getElementById('filterTourVoivodeship').value = '';
  document.getElementById('filterTourClass').value = '';
  document.getElementById('filterTourAge').value = '';
  document.getElementById('filterTourStyle').value = '';
  document.getElementById('filterTourRank').value = '';
  filterTournaments();
}

async function filterTournaments() {
  const q = document.getElementById('filterTourQuery').value;
  const voivodeship = document.getElementById('filterTourVoivodeship').value;
  const danceClass = document.getElementById('filterTourClass').value;
  const ageGroup = document.getElementById('filterTourAge').value;
  const style = document.getElementById('filterTourStyle').value;
  const rank = document.getElementById('filterTourRank').value;

  const params = new URLSearchParams({ q, voivodeship, danceClass, ageGroup, style, rank });
  const data = await fetchFtsApi(`/api/tournaments?${params.toString()}`);
  const listEl = document.getElementById('tournamentsCatalogList');

  if (!data.tournaments || data.tournaments.length === 0) {
    listEl.innerHTML = `<div class="glass-card rounded-xl p-6 text-center text-xs text-gray-400">Brak turniejów spełniających wybrane kryteria.</div>`;
    return;
  }

  listEl.innerHTML = data.tournaments
    .map(
      (t) => `
      <div class="glass-card rounded-2xl p-4 border border-gray-800 hover:border-gray-700 transition space-y-3">
        <div class="flex items-start justify-between gap-2">
          <div>
            <div class="flex items-center gap-2">
              <span class="font-mono text-xs font-bold text-amber-400">${t.dateStart}</span>
              <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">${t.tournamentRank}</span>
              <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-gray-800 text-gray-400">${t.sourceSystem}</span>
            </div>
            <h4 class="font-bold text-sm text-white mt-1">${t.competitionName}</h4>
            <p class="text-xs text-gray-400 mt-0.5">📍 ${t.cityName} (${t.voivodeship}) — ${t.venueName}</p>
          </div>
        </div>

        <div class="space-y-1.5 pt-2 border-t border-gray-800/80">
          <div class="text-[11px] font-mono text-gray-400 uppercase">Kategorie Turniejowe (Kliknij, aby zbadać Skating i Sędziów):</div>
          <div class="flex flex-wrap gap-1.5">
            ${(t.categories || [])
              .map(
                (cat) => `
              <button onclick="loadCategorySkatingDetails('${cat.categoryId}')"
                      class="px-2.5 py-1 rounded-lg text-xs font-medium bg-obsidian hover:bg-amber-500/20 text-gray-200 hover:text-amber-300 border border-gray-700 hover:border-amber-500/40 transition">
                ${cat.categoryName} <span class="font-mono text-[10px] text-gray-400">[${cat.competitorsCount} par]</span>
              </button>`
              )
              .join('')}
          </div>
        </div>
      </div>`
    )
    .join('');

  // Auto-open the first category if inspector is empty
  const firstCat = data.tournaments[0]?.categories?.[0]?.categoryId;
  if (firstCat && !window.currentInspectedCategory) {
    loadCategorySkatingDetails(firstCat);
  }
}

async function loadCategorySkatingDetails(categoryId) {
  window.currentInspectedCategory = categoryId;
  const data = await fetchFtsApi(`/api/category/${categoryId}`);
  if (data.error) return;

  const cat = data.category;
  const judges = data.panelJudges || [];
  const results = data.results || [];
  const skatingByDance = data.skatingByDance || {};
  const dancesKeys = Object.keys(skatingByDance);

  const paneEl = document.getElementById('categoryInspectorPane');
  paneEl.innerHTML = `
    <div class="border-b border-gray-800 pb-4 mb-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span class="text-xs font-mono text-amber-400">${cat.dateStart} • ${cat.cityName} • ${cat.tournamentRank}</span>
        <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
          System: ${cat.judgingSystem.toUpperCase()} (${cat.competitorsCount} par / ${cat.roundsCount} rundy)
        </span>
      </div>
      <h3 class="text-xl font-extrabold text-white mt-1">${cat.categoryName} — ${cat.competitionName}</h3>
      <p class="text-xs text-gray-400 mt-0.5">Tańce w kategorii: <strong class="text-indigo-300 font-mono">${cat.dancesList}</strong> • Blok: ${cat.blockName}</p>
    </div>

    <!-- Judging Panel List -->
    <div class="mb-5">
      <h4 class="text-xs font-mono uppercase text-gray-400 mb-2">Komisja Sędziowska (Kliknij sędziego, aby przejść do profilu Biasu):</h4>
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
        ${judges
          .map(
            (j) => `
          <div onclick="openJudgeProfile('${j.judgeId}')" class="p-2 rounded-xl bg-obsidian/80 border border-gray-800 hover:border-amber-500/40 cursor-pointer flex items-center gap-2 text-xs transition">
            <span class="w-6 h-6 rounded-lg bg-amber-500/20 text-amber-300 font-mono font-bold flex items-center justify-center">${j.judgeLetter}</span>
            <div class="truncate">
              <div class="font-bold text-white truncate">${j.fullName}</div>
              <div class="text-[10px] text-gray-400 font-mono">ID:${j.judgeId} • ${j.cityName}</div>
            </div>
          </div>`
          )
          .join('')}
      </div>
    </div>

    <!-- Final Classification Table -->
    <div class="mb-6">
      <h4 class="text-xs font-mono uppercase text-amber-400 mb-2">Oficjalne Wyniki Kategorii i Zdobyte Punkty Rankingowe:</h4>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse text-xs">
          <thead>
            <tr class="border-b border-gray-800 text-gray-400 font-mono text-[11px]">
              <th class="py-2 px-2">M-ce</th>
              <th class="py-2 px-2">Nr</th>
              <th class="py-2 px-2">Para & Unikalne Numery Licencji (ID_M_ID_K)</th>
              <th class="py-2 px-2">Klub</th>
              <th class="py-2 px-2 text-right">Runda</th>
              <th class="py-2 px-2 text-right">Krzyżyki 1/2F</th>
              <th class="py-2 px-2 text-right">Punkty FTS</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-800/60">
            ${results
              .map(
                (r) => `
              <tr class="hover:bg-gray-800/40">
                <td class="py-2 px-2 mono-num font-bold ${r.finalPlace <= 3 ? 'text-amber-400' : 'text-white'}">${r.finalPlace}.</td>
                <td class="py-2 px-2 mono-num text-gray-400">#${r.startNumber}</td>
                <td class="py-2 px-2">
                  <button onclick="openCoupleProfile('${r.coupleKey}')" class="font-bold text-white hover:text-amber-400">${r.coupleName}</button>
                  <div class="text-[10px] font-mono text-indigo-300">ID: ${r.coupleKey}</div>
                </td>
                <td class="py-2 px-2 text-gray-300">${r.clubName}</td>
                <td class="py-2 px-2 text-right font-mono text-xs">${r.maxRoundReached}</td>
                <td class="py-2 px-2 text-right mono-num text-gray-300">${r.totalPreliminaryMarks}</td>
                <td class="py-2 px-2 text-right mono-num font-bold text-emerald-400">+${r.pointsEarned} pkt</td>
              </tr>`
              )
              .join('')}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Detailed Final Skating Tables per Dance -->
    <div class="space-y-4">
      <h4 class="text-xs font-mono uppercase text-indigo-400">Szczegółowe Arkusze Finałowe SKATING (Noty Sędziów A..J oraz Większość 1..1-6):</h4>
      ${dancesKeys
        .map((dCode) => {
          const rows = skatingByDance[dCode] || [];
          return `
            <div class="p-3.5 rounded-xl bg-obsidian/80 border border-gray-800">
              <div class="font-bold text-xs text-amber-300 mb-2 font-mono">TANIEC FINAŁOWY: ${dCode}</div>
              <div class="overflow-x-auto">
                <table class="w-full text-center border-collapse text-[11px] mono-num">
                  <thead>
                    <tr class="border-b border-gray-800 text-gray-400">
                      <th class="py-1 px-1.5 text-left">Para</th>
                      ${judges.map((j) => `<th class="py-1 px-1 text-amber-400" title="${j.fullName}">${j.judgeLetter}</th>`).join('')}
                      <th class="py-1 px-1 bg-gray-800/60 text-gray-300">1</th>
                      <th class="py-1 px-1 bg-gray-800/60 text-gray-300">1-2</th>
                      <th class="py-1 px-1 bg-gray-800/60 text-gray-300">1-3</th>
                      <th class="py-1 px-1 bg-gray-800/60 text-gray-300">1-4</th>
                      <th class="py-1 px-1 bg-gray-800/60 text-gray-300">1-5</th>
                      <th class="py-1 px-1.5 font-bold text-emerald-400">M-ce</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-800/40">
                    ${rows
                      .map(
                        (row) => `
                      <tr>
                        <td class="py-1.5 px-1.5 text-left font-sans font-semibold text-white truncate max-w-[170px]">
                          #${row.startNumber} ${row.coupleName}
                        </td>
                        ${judges
                          .map((j) => {
                            const val = row.marksByLetter[j.judgeLetter] ?? '-';
                            return `<td class="py-1.5 px-1 ${val === 1 ? 'text-amber-400 font-bold' : 'text-gray-300'}">${val}</td>`;
                          })
                          .join('')}
                        <td class="py-1.5 px-1 bg-gray-800/30 text-gray-300">${row.majorityCounts['1']}</td>
                        <td class="py-1.5 px-1 bg-gray-800/30 text-gray-300">${row.majorityCounts['1-2']}</td>
                        <td class="py-1.5 px-1 bg-gray-800/30 text-gray-300">${row.majorityCounts['1-3']}</td>
                        <td class="py-1.5 px-1 bg-gray-800/30 text-gray-300">${row.majorityCounts['1-4']}</td>
                        <td class="py-1.5 px-1 bg-gray-800/30 text-gray-300">${row.majorityCounts['1-5']}</td>
                        <td class="py-1.5 px-1.5 font-bold text-emerald-400">${row.danceCalculatedPlace}</td>
                      </tr>`
                      )
                      .join('')}
                  </tbody>
                </table>
              </div>
            </div>`;
        })
        .join('')}
    </div>
  `;
}

// ==================== TAB 6: JUDGES ANALYTICS & BIAS MATRIX ====================
async function loadJudgeAnalytics(selectedJudgeId = null) {
  const url = selectedJudgeId ? `/api/judges?judgeId=${encodeURIComponent(selectedJudgeId)}` : '/api/judges';
  const data = await fetchFtsApi(url);

  const detail = data.selectedJudgeDetail;
  if (!detail) return;
  const j = detail.judge;
  const judgeSearchInputEl = document.getElementById('judgeSearchInput');
  if (judgeSearchInputEl) {
    judgeSearchInputEl.value = `${j.fullName} [ID: ${j.judgeId}] — ${j.cityName} (${j.regionTag})`;
  }

  document.getElementById('judgeProfileHeader').innerHTML = `
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div>
        <div class="flex items-center gap-2">
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold">
            Adjudicator ID: ${j.judgeId}
          </span>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">
            Okręg: ${j.regionTag} • ${j.wdsfLicense}
          </span>
          ${j.isChairperson ? `<span class="px-2.5 py-0.5 rounded-full text-xs font-mono bg-emerald-500/20 text-emerald-300">Sędzia Główny (Chairperson)</span>` : ''}
        </div>
        <h2 class="text-2xl font-extrabold text-white mt-2">${j.fullName} (${j.cityName})</h2>
        <p class="text-xs text-gray-400 mt-1">Uprawnienia: <strong class="text-gray-200">${j.licenseCategory}</strong></p>
      </div>
      <div class="flex items-center gap-3">
        <div class="bg-obsidian/80 p-3.5 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Zgodność z Panelem (Consensus)</div>
          <div class="text-xl font-extrabold mono-num text-emerald-400">${detail.overallConsensusAgreementPct}%</div>
        </div>
        <div class="bg-obsidian/80 p-3.5 rounded-xl border border-gray-800 text-center">
          <div class="text-[10px] text-gray-400 uppercase">Sędziowanych Kategorii</div>
          <div class="text-xl font-extrabold mono-num text-amber-400">${detail.tournamentsWorked.length}</div>
        </div>
      </div>
    </div>
  `;

  document.getElementById('judgeCouplesMatrixBody').innerHTML = (detail.coupleEvaluations || [])
    .map((ce) => {
      const delta = Number(ce.deltaVsPanel);
      return `
        <tr class="hover:bg-gray-800/50">
          <td class="py-2 px-2">
            <button onclick="openCoupleProfile('${ce.coupleKey}')" class="font-bold text-white hover:text-amber-400 text-left">
              ${ce.coupleName}
            </button>
            <div class="text-[10px] font-mono text-gray-400">${ce.coupleKey}</div>
          </td>
          <td class="py-2 px-2 text-gray-300">${ce.clubName}</td>
          <td class="py-2 px-2 text-right mono-num">${ce.dancesJudged}</td>
          <td class="py-2 px-2 text-right mono-num font-bold text-amber-300">${ce.judgeAvgMark}</td>
          <td class="py-2 px-2 text-right mono-num text-gray-400">${ce.panelConsensusMark}</td>
          <td class="py-2 px-2 text-right mono-num font-bold ${delta <= -0.3 ? 'text-emerald-400' : delta >= 0.3 ? 'text-rose-400' : 'text-gray-300'}">
            ${delta > 0 ? '+' : ''}${ce.deltaVsPanel}
          </td>
          <td class="py-2 px-2 text-right mono-num text-indigo-300">${ce.consensusAgreementPct}%</td>
        </tr>`;
    })
    .join('');

  // Club Affinity Chart
  const clubs = detail.clubAffinity || [];
  const clubCtx = document.getElementById('judgeClubBiasChart').getContext('2d');
  if (judgeClubBiasChartInstance) judgeClubBiasChartInstance.destroy();

  judgeClubBiasChartInstance = new Chart(clubCtx, {
    type: 'bar',
    data: {
      labels: clubs.map((c) => c.clubName.replace('UKS ', '').replace('UKTS ', '')),
      datasets: [
        {
          label: 'Odchylenie Δ (Ujemne = Ocenia klub wyżej niż panel)',
          data: clubs.map((c) => c.clubBiasDelta),
          backgroundColor: clubs.map((c) => (c.clubBiasDelta < 0 ? 'rgba(16, 185, 129, 0.75)' : 'rgba(244, 63, 94, 0.75)')),
          borderRadius: 5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#9CA3AF', font: { size: 9 } }, grid: { color: 'rgba(55,65,81,0.3)' } },
        y: { ticks: { color: '#9CA3AF', font: { size: 10 } }, grid: { color: 'rgba(55,65,81,0.3)' } },
      },
      plugins: {
        legend: { labels: { color: '#D1D5DB', font: { size: 10 } } },
      },
    },
  });

  // Tournaments worked list
  document.getElementById('judgeTournamentsList').innerHTML = (detail.tournamentsWorked || [])
    .map(
      (tw) => `
      <div onclick="openCategorySkating('${tw.categoryId}')" class="p-2.5 rounded-xl bg-obsidian/80 border border-gray-800 hover:border-indigo-500/40 cursor-pointer flex items-center justify-between transition">
        <div>
          <div class="font-bold text-white">${tw.competitionName} (${tw.cityName})</div>
          <div class="text-[11px] text-gray-400">${tw.dateStart} • ${tw.categoryName}</div>
        </div>
        <span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono text-xs font-bold">Litera ${tw.judgeLetter}</span>
      </div>`
    )
    .join('');
}

// Live Sync Trigger (Works both with local FastAPI backend AND directly in browser on GitHub Pages!)
async function triggerLiveFtsSync() {
  const btn = document.getElementById('syncLiveBtn');
  const origHtml = btn.innerHTML;
  btn.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span><span>Pobieranie LIVE z baza.polskitaniec.org...</span>`;
  btn.disabled = true;
  try {
    let backendSynced = false;
    if (window.location.protocol !== 'file:' && !window.location.hostname.includes('github.io')) {
      try {
        const resp = await fetch('/api/sync-live?maxTournaments=5', { method: 'POST' });
        if (resp.ok) {
          const res = await resp.json();
          backendSynced = true;
          await initApplication();
          alert(
            `Synchronizacja z serwerem FTS/PZST zakończona (${res.status})!\nZaktualizowano turniejów: ${res.syncedTournaments || 0}\nKategorii LIVE: ${res.syncedCategories || 0}\nNot sędziowskich: ${res.syncedMarks || 0}`
          );
        }
      } catch (err) {
        // Fallback to direct browser live sync
      }
    }

    if (!backendSynced && typeof runBrowserLiveFtsSync === 'function') {
      const liveReport = await runBrowserLiveFtsSync(5, (progressMsg) => {
        btn.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span><span class="truncate">${progressMsg}</span>`;
      });
      await initApplication();
      alert(
        `Dynamiczna synchronizacja przeglądarkowa (GitHub Pages LIVE) zakończona!\n` +
          `Sprawdzono turniejów w API baza.polskitaniec.org/cal/archive: ${liveReport.totalArchiveTournamentsFound || 0}\n` +
          `Nowo wykryte turnieje: ${liveReport.newTournamentsAdded || 0}\n` +
          `Nowo pobrane kategorie LIVE: ${liveReport.liveCategoriesSynced || 0}\n` +
          `Łącznie par w bazie: ${liveReport.couplesCount || appCatalog.couples.length}`
      );
    }
  } catch (e) {
    console.error('Live sync error:', e);
  } finally {
    btn.innerHTML = origHtml;
    btn.disabled = false;
  }
}

window.addEventListener('DOMContentLoaded', initApplication);
