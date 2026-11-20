/**
 * FTS & PZST Browser-Side Dynamic Live Sync Engine (Runs directly in the browser on GitHub Pages!)
 * Fetches live tournament calendar, new tournaments, timetables, categories, participants,
 * and Skating/Recall marks from https://baza.polskitaniec.org and merges them dynamically into memory.
 */

const CORS_FETCH_STRATEGIES = [
  (url) => url, // 1. Direct fetch (works natively on /cal/archive and /api/ranking/...)
  (url) => `https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`,
  (url) => `https://corsproxy.io/?${encodeURIComponent(url)}`,
];

async function fetchLiveJsonWithFallback(targetUrl, timeoutMs = 9000) {
  for (const buildUrl of CORS_FETCH_STRATEGIES) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const resp = await fetch(buildUrl(targetUrl), { signal: controller.signal });
      clearTimeout(timer);
      if (resp.ok) {
        return await resp.json();
      }
    } catch (e) {
      // Try next strategy
    }
  }
  return null;
}

function browserNormalizeDanceCode(rawCode) {
  const cleaned = String(rawCode || '').replace('d-', '').toUpperCase().trim();
  const map = {
    WA: 'WA', WALTZ: 'WA', SW: 'WA',
    TG: 'TG', TANGO: 'TG', TA: 'TG',
    VW: 'VW', VIENNESE: 'VW',
    SF: 'SF', SLOWFOX: 'SF', FOXTROT: 'SF',
    QS: 'QS', QUICKSTEP: 'QS',
    SA: 'SA', SAMBA: 'SA', SB: 'SA',
    CH: 'CH', CHA: 'CH', CC: 'CH',
    RB: 'RB', RUMBA: 'RB', RU: 'RB',
    PD: 'PD', PASO: 'PD', PB: 'PD',
    JV: 'JV', JIVE: 'JV', JI: 'JV',
  };
  return map[cleaned] || cleaned;
}

function browserHashId(prefix, textStr) {
  let hash = 0;
  const s = String(textStr || '').toLowerCase().trim();
  for (let i = 0; i < s.length; i++) {
    hash = (hash * 31 + s.charCodeAt(i)) % 899999;
  }
  return `${prefix}${Math.abs(hash) + 100000}`;
}

async function runBrowserLiveFtsSync(maxLiveTournamentsToInspect = 6, onProgressCallback = null) {
  const b = window.FTS_STATIC_BUNDLE;
  if (!b) return { status: 'no_bundle' };

  if (onProgressCallback) onProgressCallback('Łączenie na żywo z baza.polskitaniec.org/cal/archive...');

  const archiveData = await fetchLiveJsonWithFallback('https://baza.polskitaniec.org/cal/archive', 10000);
  if (!archiveData) {
    return { status: 'offline_fallback', message: 'Brak bezpośredniego połączenia z API PZST — użyto bazy w pamięci.' };
  }

  const items = Array.isArray(archiveData) ? archiveData : archiveData.items || [];
  const existingTournamentsMap = {};
  (b.tournamentsAll || []).forEach((t) => {
    existingTournamentsMap[t.tournamentId] = t;
  });

  // Build name -> existing dancerId lookup so we preserve 8-digit license IDs (20xxxxxx)
  const nameToDancerId = {};
  (b.catalog?.dancers || []).forEach((d) => {
    nameToDancerId[d.fullName.toLowerCase().trim()] = d.dancerId;
  });

  let newTournamentsAdded = 0;
  let liveCategoriesSynced = 0;
  let liveTournamentsChecked = 0;

  for (const item of items) {
    const compId = String(item.id || '');
    if (!compId) continue;
    const tId = `PZST_${compId}`;
    const compName = item.competitionName || 'Turniej PZST/FTS';
    const cityName = item.city?.name || 'Polska';
    const dateStart = item.dateStart || '2026-01-01';
    const dateEnd = item.dateEnd || dateStart;
    const venueName = item.venues?.[0]?.name || '';
    const venueAddress = item.venues?.[0]?.address || '';

    let liveUrl = '';
    for (const lObj of item.links || []) {
      if (lObj.link && lObj.link.includes('LIVE') && !lObj.isPdf) {
        liveUrl = lObj.link;
        break;
      }
    }

    const rankLabel = compName.toLowerCase().includes('mistrzostwa polski')
      ? 'Mistrzostwa Polski'
      : compName.toLowerCase().includes('puchar')
      ? 'Puchar Polski'
      : compName.toLowerCase().includes('grand prix')
      ? 'Grand Prix Polski'
      : 'OTTS / Turniej FTS';

    if (!existingTournamentsMap[tId]) {
      const newTourObj = {
        tournamentId: tId,
        sourceSystem: 'PZST_MARX_LIVE',
        competitionName: compName,
        cityName,
        voivodeship: 'Polska',
        venueName,
        venueAddress,
        dateStart,
        dateEnd,
        tournamentRank: rankLabel,
        portalUrl: `https://baza.polskitaniec.org/reg/${compId}`,
        liveResultsUrl: liveUrl,
        isArchived: 1,
        categoriesCount: 0,
        entriesCount: 0,
        categories: [],
      };
      b.tournamentsAll.unshift(newTourObj);
      b.catalog.tournaments.unshift(newTourObj);
      existingTournamentsMap[tId] = newTourObj;
      newTournamentsAdded += 1;
    }

    // If this tournament has a LIVE MarX URL and we haven't checked maxLiveTournamentsToInspect yet, pull its live timetable.json!
    const tourRef = existingTournamentsMap[tId];
    if (
      liveUrl &&
      liveUrl.endsWith('index.php') &&
      liveTournamentsChecked < maxLiveTournamentsToInspect &&
      (!tourRef.categories || tourRef.categories.length === 0)
    ) {
      liveTournamentsChecked += 1;
      const baseLiveDir = liveUrl.substring(0, liveUrl.lastIndexOf('/'));
      if (onProgressCallback) {
        onProgressCallback(`Pobieranie wyników LIVE: ${cityName} (${dateStart})...`);
      }
      const ttData = await fetchLiveJsonWithFallback(`${baseLiveDir}/timetable.json`, 7000);
      if (ttData && Array.isArray(ttData.timetable)) {
        const seenCatIds = new Set();
        for (const entry of ttData.timetable) {
          const rawCatId = entry.categoryId;
          const textUpper = String(entry.text || '').toUpperCase();
          if (rawCatId && !seenCatIds.has(rawCatId) && !textUpper.includes('SOLO') && seenCatIds.size < 6) {
            seenCatIds.add(rawCatId);
            const catData = await fetchLiveJsonWithFallback(`${baseLiveDir}/${rawCatId}.json`, 7000);
            if (catData && Array.isArray(catData.p) && catData.p.length > 0) {
              mergeLiveCategoryIntoBundle(b, tourRef, rawCatId, catData, nameToDancerId);
              liveCategoriesSynced += 1;
            }
          }
        }
      }
    }
  }

  // Update overview KPI counts
  b.overview.totals.tournamentsCount = b.tournamentsAll.length;
  b.overview.totals.couplesCount = b.catalog.couples.length;
  b.overview.totals.dancersCount = b.catalog.dancers.length;

  return {
    status: 'live_synced',
    totalArchiveTournamentsFound: items.length,
    newTournamentsAdded,
    liveCategoriesSynced,
    dancersCount: b.catalog.dancers.length,
    couplesCount: b.catalog.couples.length,
  };
}

function mergeLiveCategoryIntoBundle(b, tourObj, rawCatId, catData, nameToDancerId) {
  const fullCatId = `${tourObj.tournamentId}_${rawCatId}`;
  if (b.categoriesMap[fullCatId]) return;

  const catMeta = catData.c || {};
  const catName = catMeta.n || rawCatId;
  const styleRaw = String(catMeta.s || 'Standard').toLowerCase();
  const styleName = styleRaw.includes('standard') ? 'Standard' : styleRaw.includes('latin') ? 'Latin' : 'Kombinacja';
  const validCouples = (catData.p || []).filter((p) => String(p.n || '').includes('|') || String(p.n || '').includes('/'));
  if (validCouples.length === 0) return;

  const roundsList = catData.r || [];
  const dancesArr = (roundsList[0]?.da || []).map(browserNormalizeDanceCode);
  const dancesListStr = dancesArr.length > 0 ? dancesArr.join(',') : styleName === 'Standard' ? 'WA,TG,VW,SF,QS' : 'SA,CH,RB,PD,JV';

  const catSummaryObj = {
    categoryId: fullCatId,
    tournamentId: tourObj.tournamentId,
    categoryName: catName,
    ageGroup: 'Pow. 15',
    danceClass: 'Open',
    style: styleName,
    dancesList: dancesListStr,
    judgingSystem: catMeta.js || 'skating',
    competitorsCount: validCouples.length,
    roundsCount: Math.max(1, roundsList.length),
    blockName: catMeta.bn || 'Blok LIVE',
    competitionName: tourObj.competitionName,
    cityName: tourObj.cityName,
    dateStart: tourObj.dateStart,
    tournamentRank: tourObj.tournamentRank,
    sourceSystem: 'PZST_MARX_LIVE',
  };

  tourObj.categories = tourObj.categories || [];
  tourObj.categories.push(catSummaryObj);
  tourObj.categoriesCount = tourObj.categories.length;

  const idMappingsJudges = catData.idMappings?.j || {};
  const panelJudges = (catData.j || []).map((jObj) => {
    const idxKey = jObj.i || 'A';
    const dispLetter = jObj.l || idxKey;
    const rawId = String(idMappingsJudges[idxKey] || '').replace('adjudicator_', '').replace('_pl', '');
    const cleanId = /^\d+$/.test(rawId) ? rawId : browserHashId('30', jObj.n || dispLetter);
    return {
      judgeId: cleanId,
      judgeLetter: dispLetter,
      judgeRole: jObj.role || 'judge',
      fullName: jObj.n || `Sędzia ${dispLetter}`,
      cityName: jObj.ct || 'Polska',
      regionTag: 'PZST',
      licenseCategory: 'PZST-A',
    };
  });

  const resultsList = [];
  validCouples.forEach((pObj, idx) => {
    const parts = String(pObj.n || '')
      .replace('|', '/')
      .split('/')
      .map((s) => s.trim());
    if (parts.length !== 2) return;
    const maleName = parts[0];
    const femaleName = parts[1];
    const mId = nameToDancerId[maleName.toLowerCase()] || browserHashId('20', maleName);
    const fId = nameToDancerId[femaleName.toLowerCase()] || browserHashId('20', femaleName);
    nameToDancerId[maleName.toLowerCase()] = mId;
    nameToDancerId[femaleName.toLowerCase()] = fId;

    const cKey = `${mId}_${fId}`;
    const finalPlace = Number(pObj.finalRank || idx + 1);
    const pts = Math.max(2, (validCouples.length - finalPlace) * 2 + (finalPlace <= 3 ? 10 : 0));

    resultsList.push({
      categoryId: fullCatId,
      tournamentId: tourObj.tournamentId,
      coupleKey: cKey,
      coupleName: `${maleName} & ${femaleName}`,
      maleDancerId: mId,
      femaleDancerId: fId,
      startNumber: Number(pObj.sn || idx + 1),
      clubName: pObj.cl || 'Klub PZST',
      cityName: pObj.ci || 'Polska',
      finalPlace,
      maxRoundReached: finalPlace <= 6 ? 'F' : '1/2 F',
      pointsEarned: pts,
      totalPreliminaryMarks: 0,
    });

    if (!b.couplesMap[cKey]) {
      const newCoupleMeta = {
        coupleKey: cKey,
        maleDancerId: mId,
        femaleDancerId: fId,
        maleFullName: maleName,
        femaleFullName: femaleName,
        coupleName: `${maleName} & ${femaleName}`,
        clubName: pObj.cl || 'Klub PZST',
        cityName: pObj.ci || 'Polska',
        ageGroup: 'Pow. 15',
        classSt: 'A',
        classLa: 'A',
        firstTournamentDate: tourObj.dateStart,
        lastTournamentDate: tourObj.dateStart,
        isActive: 1,
        startsCount: 1,
        avgPlace: finalPlace,
        totalPoints: pts,
      };
      b.catalog.couples.push(newCoupleMeta);
    }
  });

  b.categoriesMap[fullCatId] = {
    category: catSummaryObj,
    panelJudges,
    results: resultsList,
    skatingByDance: {},
    prelimRecalls: [],
  };
}
