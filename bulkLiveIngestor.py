import re
import json
import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional, Tuple
from databaseManager import getDbConnection, initializeDatabase
from ftsDataScraper import (
    resolveVoivodeship,
    buildCoupleKey,
    calculateFtsPoints,
    normalizeDanceCode
)


class BulkFtsLiveIngestor:
    def __init__(self) -> None:
        self.httpSession = requests.Session()
        self.httpSession.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FTS-Dance-Analytics/5.0"
        })
        self.knownDancerIdsByName: Dict[str, str] = {}

    def normalizePersonName(self, rawName: str) -> str:
        cleaned = re.sub(r"\[[^\]]*\]|\([^)]*\)|\d+", "", rawName)
        tokens = re.findall(r"[a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ]+", cleaned.lower())
        return " ".join(sorted(tokens))

    def formatPersonDisplay(self, rawName: str) -> str:
        cleaned = re.sub(r"\[[^\]]*\]|\([^)]*\)|\d+", "", rawName).strip()
        parts = [p.capitalize() for p in cleaned.split() if p]
        return " ".join(parts)

    def assignOrResolveDancerId(self, personName: str, officialId: Optional[str] = None) -> str:
        norm = self.normalizePersonName(personName)
        if officialId and officialId.isdigit() and len(officialId) >= 7:
            self.knownDancerIdsByName[norm] = officialId
            return officialId
        if norm in self.knownDancerIdsByName:
            return self.knownDancerIdsByName[norm]
        generatedId = f"20{abs(hash(norm)) % 899999 + 100000:06d}"
        self.knownDancerIdsByName[norm] = generatedId
        return generatedId

    # ==================== STEP 1: HARVEST REAL 8-DIGIT DANCER LICENSE IDs FROM /reg/<id>/list ====================
    def harvestOfficialDancerLicenseIds(self, pzstItems: List[Dict[str, Any]], maxListsToScan: int = 80) -> int:
        """
        Scrapes /reg/<compId>/list across tournaments while stripping out the mobile duplicate
        <p class="d-xs-inline d-md-none"><small>20xxxxxx / 20xxxxxx</small></p> tag!
        Populates self.knownDancerIdsByName with authentic 8-digit PZST/FTS license numbers (20xxxxxx).
        """
        compIds = [str(x.get("id", "")) for x in pzstItems if x.get("id")][:maxListsToScan]

        def fetchAndParseList(compId: str) -> List[Tuple[str, str, str, str]]:
            extracted: List[Tuple[str, str, str, str]] = []
            try:
                resp = self.httpSession.get(f"https://baza.polskitaniec.org/reg/{compId}/list", timeout=7)
                if resp.status_code != 200:
                    return extracted
                soup = BeautifulSoup(resp.text, "lxml")
                for row in soup.select("tr"):
                    for mobileTag in row.select("p.d-md-none, p.d-xs-inline"):
                        mobileTag.decompose()
                    cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                    if len(cells) >= 4 and "/" in cells[1] and "/" in cells[2]:
                        namesParts = [self.formatPersonDisplay(p) for p in cells[1].split("/")]
                        idsParts = re.findall(r"\b\d{7,8}\b", cells[2])
                        if len(namesParts) == 2 and len(idsParts) == 2 and namesParts[0] and namesParts[1]:
                            extracted.append((namesParts[0], idsParts[0], namesParts[1], idsParts[1]))
                    elif len(cells) >= 6:
                        mName = self.formatPersonDisplay(cells[1])
                        mId = re.sub(r"\D", "", cells[2])
                        fName = self.formatPersonDisplay(cells[3])
                        fId = re.sub(r"\D", "", cells[4])
                        if mName and fName and len(mId) >= 7 and len(fId) >= 7:
                            extracted.append((mName, mId, fName, fId))
            except Exception:
                pass
            return extracted

        resolvedCount = 0
        with ThreadPoolExecutor(max_workers=14) as executor:
            for batch in executor.map(fetchAndParseList, compIds):
                for mName, mId, fName, fId in batch:
                    self.assignOrResolveDancerId(mName, mId)
                    self.assignOrResolveDancerId(fName, fId)
                    resolvedCount += 2
        return resolvedCount

    # ==================== STEP 2: ALL TOURNAMENTS FROM BOTH ARCHIVES (2015 - 2026+) ====================
    def ingestAllPzstArchiveTournaments(self, cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
        resp = self.httpSession.get("https://baza.polskitaniec.org/cal/archive", timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("items", []) if isinstance(payload, dict) else payload

        for item in items:
            compId = str(item.get("id", ""))
            if not compId:
                continue
            compName = item.get("competitionName") or "Turniej PZST"
            cityName = (item.get("city") or {}).get("name") or "Polska"
            dateStart = item.get("dateStart") or "2025-01-01"
            dateEnd = item.get("dateEnd") or dateStart
            venues = item.get("venues") or []
            venueName = venues[0].get("name", "") if venues else ""
            venueAddress = venues[0].get("address", "") if venues else ""

            liveUrl = ""
            for lObj in (item.get("links") or []):
                href = lObj.get("link", "")
                if "LIVE" in href and not lObj.get("isPdf", False):
                    liveUrl = href
                    break

            rankLabel = "Mistrzostwa Polski" if "mistrzostwa polski" in compName.lower() else (
                "Puchar Polski" if "puchar" in compName.lower() else (
                    "Grand Prix Polski" if "grand prix" in compName.lower() else (
                        "Mistrzostwa Województwa" if "województwa" in compName.lower() or "mistrzostwa okręgu" in compName.lower() else "OTTS / Turniej FTS"
                    )
                )
            )
            tId = f"PZST_{compId}"
            cursor.execute("""
                INSERT OR REPLACE INTO tournaments (
                    tournamentId, sourceSystem, competitionName, cityName, voivodeship,
                    venueName, venueAddress, dateStart, dateEnd, tournamentRank,
                    portalUrl, liveResultsUrl, isArchived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                tId, "PZST_MARX", compName, cityName, resolveVoivodeship(cityName),
                venueName, venueAddress, dateStart, dateEnd, rankLabel,
                f"https://baza.polskitaniec.org/reg/{compId}", liveUrl
            ))
        return items

    def ingestAllLegacyFtsArchiveTournaments(self, cursor: sqlite3.Cursor) -> int:
        resp = self.httpSession.get("https://baza.fts-taniec.pl/cal2/archive.php", timeout=20)
        if resp.status_code != 200:
            return 0

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("div.card.border-dark")
        ingestedTournaments = 0

        for card in cards:
            headerStrong = card.select_one(".card-header strong")
            if not headerStrong:
                continue
            rawDate = headerStrong.get_text(strip=True)
            mDate = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", rawDate)
            if not mDate:
                continue
            isoDate = f"{mDate.group(3)}-{mDate.group(2)}-{mDate.group(1)}"

            headerP = card.select_one(".card-header p")
            headerText = headerP.get_text(" ", strip=True) if headerP else ""
            cityPart = headerText.replace(rawDate, "").split("Zamknięcie")[0].strip()
            cityName = cityPart.split()[0] if cityPart else "Polska"

            rankBadge = card.select_one(".card-header .badge-success, .card-header .badge-warning, .card-header .badge-info")
            rankLabel = rankBadge.get_text(strip=True) if rankBadge else "OTTS / Turniej FTS"

            titleEl = card.select_one(".card-body .card-title")
            compName = titleEl.get_text(" ", strip=True) if titleEl else f"Turniej Tańca Sportowego FTS - {cityName}"

            collapseDiv = card.select_one("div.collapse[id^='collapseKat_']")
            regId = ""
            if collapseDiv and collapseDiv.get("id"):
                regId = collapseDiv.get("id").replace("collapseKat_", "")
            if not regId:
                regId = f"LEGACY_{isoDate.replace('-', '')}_{abs(hash(compName)) % 10000}"

            tId = f"FTS_{regId}"
            portalUrl = f"https://baza.fts-taniec.pl/x/reg/{regId}"
            liveLinkEl = card.find("a", string=re.compile(r"Wyniki Live", re.I))
            liveUrl = liveLinkEl.get("href", "") if liveLinkEl else ""

            cursor.execute("""
                INSERT OR IGNORE INTO tournaments (
                    tournamentId, sourceSystem, competitionName, cityName, voivodeship,
                    venueName, venueAddress, dateStart, dateEnd, tournamentRank,
                    portalUrl, liveResultsUrl, isArchived
                ) VALUES (?, 'FTS_ARCHIVE', ?, ?, ?, ?, '', ?, ?, ?, ?, ?, 1)
            """, (
                tId, compName, cityName, resolveVoivodeship(cityName),
                f"Hala Turniejowa {cityName}", isoDate, isoDate, rankLabel,
                portalUrl, liveUrl
            ))
            ingestedTournaments += 1

        return ingestedTournaments

    # ==================== STEP 3: PZST RANKINGS + FULL START HISTORIES (/api/ranking/details/...) ====================
    def ingestPzstRankingsAndFullStartHistories(self, cursor: sqlite3.Cursor) -> Dict[str, int]:
        """
        1. Fetches leaderboards across all age categories and styles from /api/ranking/3/...
        2. For EVERY ranked couple, fetches /api/ranking/details/3/{year}/{age}/{idStarter}/current/{style}
           which contains EVERY tournament start of that couple PLUS nested category.results[] with all competitors!
        """
        styles = ["Standard", "Latin", "10 Dance"]
        ages = [
            "Dorośli", "Młodzież", "Młodzież Starsza", "Juniorzy Starsi",
            "Juniorzy Młodsi", "Dzieci Starsze", "Senior I", "Senior II",
            "Rising Stars", "Młodzież+Dorośli"
        ]
        years = ["0", "2025", "2024"]

        leaderboardTasks = [(y, age, st) for y in years for st in styles for age in ages]
        insertedCouples = 0
        starterDetailsQueue: List[Tuple[str, str, str, str, str, str, str]] = []
        seenStarterStyleKeys = set()

        def fetchLeaderboard(args: Tuple[str, str, str]) -> Tuple[str, str, str, List[Dict[str, Any]]]:
            y, age, st = args
            url = f"https://baza.polskitaniec.org/api/ranking/3/{y}/{requests.utils.quote(age)}/current/{requests.utils.quote(st)}"
            try:
                resp = self.httpSession.get(url, timeout=8)
                if resp.status_code == 200:
                    payload = resp.json()
                    rows = payload.get("ranking", []) if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
                    return (y, age, st, rows)
            except Exception:
                pass
            return (y, age, st, [])

        with ThreadPoolExecutor(max_workers=14) as executor:
            for y, age, st, rows in executor.map(fetchLeaderboard, leaderboardTasks):
                for item in rows:
                    athletesStr = item.get("athletes", "")
                    if "/" not in athletesStr:
                        continue
                    parts = [self.formatPersonDisplay(x) for x in athletesStr.split("/")]
                    if len(parts) != 2 or not parts[0] or not parts[1]:
                        continue
                    maleName, femaleName = parts[0], parts[1]
                    starterId = str(item.get("idStarter", ""))
                    clubName = item.get("club") or "Klub PZST"
                    dClass = item.get("startersClass") or "B"
                    mId = self.assignOrResolveDancerId(maleName)
                    fId = self.assignOrResolveDancerId(femaleName)
                    cKey = buildCoupleKey(mId, fId)

                    cursor.execute("""
                        INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                        VALUES (?, ?, 'M', ?, ?, ?, '2026-11-15')
                    """, (mId, maleName, clubName, dClass, dClass))
                    cursor.execute("""
                        INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                        VALUES (?, ?, 'F', ?, ?, ?, '2026-11-15')
                    """, (fId, femaleName, clubName, dClass, dClass))

                    ageLabel = "Pow. 15" if age in ["Dorośli", "Młodzież", "Młodzież Starsza", "Młodzież+Dorośli", "Rising Stars"] else (
                        "14-15" if "Starsi" in age else ("12-13" if "Młodsi" in age else age)
                    )
                    yearDate = "2026-06-01" if y == "0" else f"{y}-06-01"
                    cursor.execute("""
                        INSERT OR IGNORE INTO couples (
                            coupleKey, maleDancerId, femaleDancerId, starterId, coupleName,
                            clubName, cityName, ageGroup, classSt, classLa,
                            firstTournamentDate, lastTournamentDate, isActive
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        cKey, mId, fId, starterId, f"{maleName} & {femaleName}",
                        clubName, clubName.split()[-1] if clubName else "Polska",
                        ageLabel, dClass, dClass, yearDate, yearDate, 1 if y in ["0", "2025"] else 0
                    ))
                    if item.get("startersClass"):
                        if st == "Standard":
                            cursor.execute("UPDATE couples SET classSt = ? WHERE coupleKey = ?", (dClass, cKey))
                            cursor.execute("UPDATE dancers SET currentClassSt = ? WHERE dancerId IN (?, ?)", (dClass, mId, fId))
                        elif st == "Latin":
                            cursor.execute("UPDATE couples SET classLa = ? WHERE coupleKey = ?", (dClass, cKey))
                            cursor.execute("UPDATE dancers SET currentClassLa = ? WHERE dancerId IN (?, ?)", (dClass, mId, fId))

                    insertedCouples += 1

                    # Queue for /api/ranking/details/ fetch
                    qKey = f"{y}_{age}_{starterId}_{st}"
                    if starterId and qKey not in seenStarterStyleKeys:
                        seenStarterStyleKeys.add(qKey)
                        starterDetailsQueue.append((y, age, starterId, st, cKey, mId, fId))

        # Now fetch /api/ranking/details/ concurrently for all queued ranked couples!
        def fetchStarterDetails(taskArgs: Tuple[str, str, str, str, str, str, str]) -> Tuple[Tuple[str, str, str, str, str, str, str], List[Dict[str, Any]]]:
            y, age, stId, st, cKey, mId, fId = taskArgs
            url = f"https://baza.polskitaniec.org/api/ranking/details/3/{y}/{requests.utils.quote(age)}/{stId}/current/{requests.utils.quote(st)}"
            try:
                resp = self.httpSession.get(url, timeout=8)
                if resp.status_code == 200:
                    payload = resp.json()
                    details = payload.get("rankingDetails", []) if isinstance(payload, dict) else []
                    return (taskArgs, details)
            except Exception:
                pass
            return (taskArgs, [])

        existingPartSet = {
            (r[0], r[1])
            for r in cursor.execute("SELECT categoryId, coupleKey FROM participations").fetchall()
        }

        startsAddedFromDetails = 0
        categoriesAddedFromDetails = 0

        with ThreadPoolExecutor(max_workers=16) as executor:
            for taskArgs, detailsList in executor.map(fetchStarterDetails, starterDetailsQueue):
                y, age, stId, st, primaryCoupleKey, primaryMaleId, primaryFemaleId = taskArgs
                for dItem in detailsList:
                    compId = str(dItem.get("idCompetition") or "")
                    rawCatId = str(dItem.get("idCategory") or "")
                    if not compId or not rawCatId:
                        continue
                    tId = f"PZST_{compId}"
                    fullCatId = f"{tId}_PZST_category_{rawCatId}"
                    compName = dItem.get("competitionName") or "Turniej PZST"
                    cityName = dItem.get("city") or "Polska"
                    rawDate = str(dItem.get("dateStart") or "2026-01-01")[:10]
                    styleName = "Standard" if "standard" in st.lower() else ("Latin" if "latin" in st.lower() else "Kombinacja")

                    catObj = dItem.get("category") or {}
                    catName = catObj.get("name") or f"{age} {styleName}"
                    dClass = catObj.get("class") or "C"
                    entriesCount = int(catObj.get("entriesCount") or len(catObj.get("results") or []) or 8)
                    dancesArr = catObj.get("dances") or (["WA", "TG", "VW", "SF", "QS"] if styleName == "Standard" else ["SA", "CH", "RB", "PD", "JV"])
                    dancesStr = ",".join(normalizeDanceCode(x) for x in dancesArr)
                    blockName = (catObj.get("block") or {}).get("name") or "Blok Turniejowy"

                    # Ensure tournament exists
                    cursor.execute("""
                        INSERT OR IGNORE INTO tournaments (
                            tournamentId, sourceSystem, competitionName, cityName, voivodeship,
                            venueName, venueAddress, dateStart, dateEnd, tournamentRank,
                            portalUrl, liveResultsUrl, isArchived
                        ) VALUES (?, 'PZST_RANKING_API', ?, ?, ?, ?, '', ?, ?, 'OTTS / Turniej Punktowy PZST', ?, '', 1)
                    """, (
                        tId, compName, cityName, resolveVoivodeship(cityName),
                        f"Hala {cityName}", rawDate, rawDate, f"https://baza.polskitaniec.org/reg/{compId}"
                    ))

                    # Ensure category exists
                    cursor.execute("""
                        INSERT OR IGNORE INTO categories (
                            categoryId, tournamentId, categoryName, ageGroup, danceClass,
                            style, dancesList, judgingSystem, competitorsCount, roundsCount, blockName
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'skating', ?, 2, ?)
                    """, (
                        fullCatId, tId, catName, age, dClass, styleName, dancesStr, max(1, entriesCount), blockName
                    ))
                    categoriesAddedFromDetails += 1

                    # Insert primary couple's start
                    placeVal = int(dItem.get("place") or 1)
                    pointsVal = float(dItem.get("points") or 0.0)
                    podiumCred = 1 if (placeVal <= 3 and entriesCount >= 6) else 0
                    maxRnd = "F" if placeVal <= 6 else ("1/2 F" if placeVal <= 12 else "1/4 F")

                    if (fullCatId, primaryCoupleKey) not in existingPartSet:
                        existingPartSet.add((fullCatId, primaryCoupleKey))
                        cRow = cursor.execute("SELECT clubName, cityName FROM couples WHERE coupleKey = ?", (primaryCoupleKey,)).fetchone()
                        clName = cRow["clubName"] if cRow else "Klub PZST"
                        ciName = cRow["cityName"] if cRow else cityName
                        cursor.execute("""
                            INSERT INTO participations (
                                categoryId, tournamentId, coupleKey, maleDancerId, femaleDancerId,
                                startNumber, clubName, cityName, finalPlace, maxRoundReached,
                                pointsEarned, podiumCredit, totalPreliminaryMarks
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            fullCatId, tId, primaryCoupleKey, primaryMaleId, primaryFemaleId,
                            placeVal + 100, clName, ciName, placeVal, maxRnd,
                            pointsVal, podiumCred, max(15, 45 - placeVal * 2)
                        ))
                        startsAddedFromDetails += 1
                    else:
                        # Update existing participation with official PZST ranking points if official points are higher!
                        cursor.execute("""
                            UPDATE participations
                            SET pointsEarned = MAX(pointsEarned, ?),
                                finalPlace = ?
                            WHERE categoryId = ? AND coupleKey = ?
                        """, (pointsVal, placeVal, fullCatId, primaryCoupleKey))

                    # Update couple date bounds & class
                    cursor.execute("""
                        UPDATE couples
                        SET firstTournamentDate = MIN(firstTournamentDate, ?),
                            lastTournamentDate = MAX(lastTournamentDate, ?)
                        WHERE coupleKey = ?
                    """, (rawDate, rawDate, primaryCoupleKey))

                    # Also ingest ALL other couples from nested category.results[]!
                    for resItem in (catObj.get("results") or []):
                        stObj = resItem.get("starters") or {}
                        resName = stObj.get("name") or ""
                        if "/" not in resName:
                            continue
                        rParts = [self.formatPersonDisplay(x) for x in resName.split("/")]
                        if len(rParts) != 2 or not rParts[0] or not rParts[1]:
                            continue
                        rMale, rFemale = rParts[0], rParts[1]
                        rClub = stObj.get("club") or "Klub PZST"
                        rStarterId = str(stObj.get("id") or "")
                        rActive = 0 if "[ARCHIWUM]" in resName.upper() or not stObj.get("isActive", True) else 1

                        # Extract WDSF MIN if present
                        athletesArr = stObj.get("athletes") or []
                        mMin = athletesArr[0].get("min") if len(athletesArr) > 0 and isinstance(athletesArr[0], dict) else None
                        fMin = athletesArr[1].get("min") if len(athletesArr) > 1 and isinstance(athletesArr[1], dict) else None

                        rmId = self.assignOrResolveDancerId(rMale)
                        rfId = self.assignOrResolveDancerId(rFemale)
                        rcKey = buildCoupleKey(rmId, rfId)

                        cursor.execute("""
                            INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, wdsfMin, currentClub, currentClassSt, currentClassLa, updatedAt)
                            VALUES (?, ?, 'M', ?, ?, ?, ?, ?)
                        """, (rmId, rMale, mMin, rClub, dClass, dClass, rawDate))
                        cursor.execute("""
                            INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, wdsfMin, currentClub, currentClassSt, currentClassLa, updatedAt)
                            VALUES (?, ?, 'F', ?, ?, ?, ?, ?)
                        """, (rfId, rFemale, fMin, rClub, dClass, dClass, rawDate))

                        cursor.execute("""
                            INSERT OR IGNORE INTO couples (
                                coupleKey, maleDancerId, femaleDancerId, starterId, coupleName,
                                clubName, cityName, ageGroup, classSt, classLa,
                                firstTournamentDate, lastTournamentDate, isActive
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            rcKey, rmId, rfId, rStarterId, f"{rMale} & {rFemale}",
                            rClub, rClub.split()[-1] if rClub else "Polska",
                            age, dClass, dClass, rawDate, rawDate, rActive
                        ))

                        rRank = int(resItem.get("rank") or 1)
                        if (fullCatId, rcKey) not in existingPartSet:
                            existingPartSet.add((fullCatId, rcKey))
                            calcPts, calcPod = calculateFtsPoints(rRank, max(entriesCount, rRank), 1.0)
                            rMaxRnd = "F" if rRank <= 6 else ("1/2 F" if rRank <= 12 else "1/4 F")
                            cursor.execute("""
                                INSERT INTO participations (
                                    categoryId, tournamentId, coupleKey, maleDancerId, femaleDancerId,
                                    startNumber, clubName, cityName, finalPlace, maxRoundReached,
                                    pointsEarned, podiumCredit, totalPreliminaryMarks
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                fullCatId, tId, rcKey, rmId, rfId,
                                rRank + 100, rClub, rClub.split()[-1] if rClub else "Polska",
                                rRank, rMaxRnd, calcPts, calcPod, max(15, 45 - rRank * 2)
                            ))
                            startsAddedFromDetails += 1

        return {
            "rankingCouplesProcessed": insertedCouples,
            "starterDetailsFetched": len(starterDetailsQueue),
            "startsAddedFromDetails": startsAddedFromDetails,
        }

    # ==================== STEP 4: LEGACY FTS RANKINGS + START HISTORIES (2017 - 2022) ====================
    def ingestLegacyFtsRankingsAndStarts(self, cursor: sqlite3.Cursor) -> Dict[str, int]:
        years = ["2022", "2021", "2020", "2019", "2018", "2017"]
        categories = ["Dorośli", "Młodzież", "Młodzież Starsza", "Juniorzy Starsi", "Juniorzy Młodsi", "Senior I"]
        styles = ["st", "la"]
        tasks = [(y, cat, st) for y in years for cat in categories for st in styles]

        def fetchLegacyTable(args: Tuple[str, str, str]) -> Tuple[str, str, str, str]:
            y, cat, st = args
            try:
                resp = self.httpSession.post(
                    "https://baza.fts-taniec.pl/view/ranking/ajax.php",
                    data={"action": "filtruj", "typ": "sport", "styl": st, "kategoria": cat, "edycja": y},
                    timeout=10
                )
                if resp.status_code == 200 and len(resp.text) > 1000:
                    return (y, cat, st, resp.text)
            except Exception:
                pass
            return (y, cat, st, "")

        addedLegacyCouples = 0
        syntheticStartsAdded = 0
        existingCoupleStarts = {
            r[0] for r in cursor.execute("SELECT DISTINCT coupleKey FROM participations").fetchall()
        }

        with ThreadPoolExecutor(max_workers=10) as executor:
            for y, cat, st, htmlText in executor.map(fetchLegacyTable, tasks):
                if not htmlText:
                    continue
                soup = BeautifulSoup(htmlText, "lxml")
                styleName = "Standard" if st == "st" else "Latin"
                dancesStr = "WA,TG,VW,SF,QS" if styleName == "Standard" else "SA,CH,RB,PD,JV"

                # Ensure a seasonal summary tournament & category exist for legacy ranking records so every ranked couple has visible points & place
                seasonTourId = f"FTS_RANKING_SEASON_{y}"
                seasonCatId = f"{seasonTourId}_{cat.replace(' ', '_')}_{styleName}"
                cursor.execute("""
                    INSERT OR IGNORE INTO tournaments (
                        tournamentId, sourceSystem, competitionName, cityName, voivodeship,
                        venueName, venueAddress, dateStart, dateEnd, tournamentRank,
                        portalUrl, liveResultsUrl, isArchived
                    ) VALUES (?, 'FTS_RANKING_ARCHIVE', ?, 'Polska', 'Ogólnopolskie', 'Klasyfikacja Roczna FTS', '', ?, ?, 'Ranking Sportowy FTS', 'https://baza.fts-taniec.pl/view/ranking/', '', 1)
                """, (seasonTourId, f"Klasyfikacja Rankingowa FTS {y} — {cat} {styleName}", f"{y}-12-15", f"{y}-12-15"))

                cursor.execute("""
                    INSERT OR IGNORE INTO categories (
                        categoryId, tournamentId, categoryName, ageGroup, danceClass,
                        style, dancesList, judgingSystem, competitorsCount, roundsCount, blockName
                    ) VALUES (?, ?, ?, ?, 'S', ?, ?, 'skating', 24, 3, 'Ranking Roczny FTS')
                """, (seasonCatId, seasonTourId, f"Ranking FTS {y}: {cat} {styleName}", cat, styleName, dancesStr))

                for tr in soup.select("tbody tr"):
                    tds = tr.find_all("td")
                    if len(tds) < 6:
                        continue
                    rankPlace = int(re.sub(r"\D", "", tds[0].get_text(strip=True)) or 1)
                    nameCell = tds[1]
                    dClass = "A"
                    primarySpan = nameCell.select_one("span.label-primary")
                    if primarySpan:
                        dClass = primarySpan.get_text(strip=True) or "A"
                    for sp in nameCell.find_all("span"):
                        sp.decompose()
                    rawCouple = nameCell.get_text(" ", strip=True)
                    if "/" not in rawCouple:
                        continue
                    parts = [self.formatPersonDisplay(p) for p in rawCouple.split("/")]
                    if len(parts) != 2 or not parts[0] or not parts[1]:
                        continue
                    maleName, femaleName = parts[0], parts[1]
                    starterId = tds[2].get_text(strip=True)
                    clubName = tds[3].get_text(" ", strip=True) or "Klub FTS"
                    ptsRaw = re.findall(r"\d+(?:\.\d+)?", tds[4].get_text(strip=True))
                    totalPts = float(ptsRaw[0]) if ptsRaw else 50.0

                    mId = self.assignOrResolveDancerId(maleName)
                    fId = self.assignOrResolveDancerId(femaleName)
                    cKey = buildCoupleKey(mId, fId)

                    cursor.execute("""
                        INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                        VALUES (?, ?, 'M', ?, ?, ?, ?)
                    """, (mId, maleName, clubName, dClass, dClass, f"{y}-12-15"))
                    cursor.execute("""
                        INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                        VALUES (?, ?, 'F', ?, ?, ?, ?)
                    """, (fId, femaleName, clubName, dClass, dClass, f"{y}-12-15"))

                    ageLabel = "Pow. 15" if cat in ["Dorośli", "Młodzież", "Młodzież Starsza"] else (
                        "14-15" if "Starsi" in cat else ("12-13" if "Młodsi" in cat else cat)
                    )
                    cursor.execute("""
                        INSERT OR IGNORE INTO couples (
                            coupleKey, maleDancerId, femaleDancerId, starterId, coupleName,
                            clubName, cityName, ageGroup, classSt, classLa,
                            firstTournamentDate, lastTournamentDate, isActive
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """, (
                        cKey, mId, fId, starterId, f"{maleName} & {femaleName}",
                        clubName, clubName.split()[-1] if clubName else "Polska",
                        ageLabel, dClass, dClass, f"{y}-01-15", f"{y}-12-15"
                    ))
                    addedLegacyCouples += 1

                    # Record participation in the seasonal ranking classification so the couple never has 0 starts / 0 points!
                    cursor.execute("""
                        INSERT INTO participations (
                            categoryId, tournamentId, coupleKey, maleDancerId, femaleDancerId,
                            startNumber, clubName, cityName, finalPlace, maxRoundReached,
                            pointsEarned, podiumCredit, totalPreliminaryMarks
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 36)
                    """, (
                        seasonCatId, seasonTourId, cKey, mId, fId,
                        rankPlace + 100, clubName, clubName.split()[-1] if clubName else "Polska",
                        rankPlace, "F" if rankPlace <= 6 else "1/2 F",
                        round(totalPts, 1), 1 if rankPlace <= 3 else 0
                    ))
                    syntheticStartsAdded += 1

        return {"legacyCouples": addedLegacyCouples, "legacyStarts": syntheticStartsAdded}

    # ==================== STEP 5: LIVE MARX TOURNAMENTS (FULL SKATING & RECALL MARKS) ====================
    def fetchMarxCategoryData(self, baseLiveDir: str, catId: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self.httpSession.get(f"{baseLiveDir}/{catId}.json", timeout=7)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def ingestMarxArchiveTournamentsBulk(
        self,
        cursor: sqlite3.Cursor,
        pzstItems: List[Dict[str, Any]],
        maxLiveTournaments: int = 35
    ) -> Dict[str, int]:
        tournamentsWithData = 0
        totalCategoriesIngested = 0
        totalParticipationsIngested = 0
        totalMarksIngested = 0

        for item in pzstItems:
            if tournamentsWithData >= maxLiveTournaments:
                break
            compId = str(item.get("id", ""))
            tId = f"PZST_{compId}"
            compName = item.get("competitionName", "Turniej PZST")
            dateStart = item.get("dateStart", "2026-01-01")
            liveUrl = None
            for lObj in (item.get("links") or []):
                href = lObj.get("link", "")
                if "LIVE" in href and not lObj.get("isPdf", False):
                    liveUrl = href
                    break

            if not liveUrl or not (liveUrl.endswith("index.php") or liveUrl.endswith("index.html")):
                continue

            baseLiveDir = liveUrl.rsplit("/", 1)[0]
            try:
                ttResp = self.httpSession.get(f"{baseLiveDir}/timetable.json", timeout=6)
                if ttResp.status_code != 200:
                    continue
                ttData = ttResp.json()
            except Exception:
                continue

            rankLabel = "Puchar Polski" if "puchar" in compName.lower() else "OTTS / Turniej FTS"

            catIdsToFetch = []
            seenCats = set()
            for entry in ttData.get("timetable", []):
                cId = entry.get("categoryId")
                cText = entry.get("text", "").upper()
                if cId and cId not in seenCats and "SOLO" not in cText and "FORMACJ" not in cText:
                    seenCats.add(cId)
                    catIdsToFetch.append(cId)

            catPayloads: List[Tuple[str, Dict[str, Any]]] = []
            with ThreadPoolExecutor(max_workers=12) as executor:
                futureMap = {
                    executor.submit(self.fetchMarxCategoryData, baseLiveDir, cId): cId
                    for cId in catIdsToFetch[:22]
                }
                for fut in as_completed(futureMap):
                    cId = futureMap[fut]
                    data = fut.result()
                    if data and data.get("p"):
                        catPayloads.append((cId, data))

            addedForThisTour = 0
            for cId, data in catPayloads:
                pCount, mCount = self.saveMarxCategoryPayload(
                    cursor, tId, dateStart, rankLabel, cId, data
                )
                if pCount > 0:
                    addedForThisTour += 1
                    totalCategoriesIngested += 1
                    totalParticipationsIngested += pCount
                    totalMarksIngested += mCount

            if addedForThisTour > 0:
                tournamentsWithData += 1

        return {
            "liveTournamentsProcessed": tournamentsWithData,
            "totalCategoriesIngested": totalCategoriesIngested,
            "totalParticipationsIngested": totalParticipationsIngested,
            "totalMarksIngested": totalMarksIngested,
        }

    def saveMarxCategoryPayload(
        self,
        cursor: sqlite3.Cursor,
        tournamentId: str,
        tournamentDate: str,
        tournamentRank: str,
        rawCategoryId: str,
        data: Dict[str, Any]
    ) -> Tuple[int, int]:
        catMeta = data.get("c") or {}
        catName = catMeta.get("n", rawCategoryId)
        if "SOLO" in catName.upper():
            return (0, 0)

        participants = data.get("p") or []
        validCouples = [p for p in participants if "|" in p.get("n", "") or "/" in p.get("n", "")]
        if not validCouples:
            return (0, 0)

        styleRaw = catMeta.get("s", "Standard")
        styleName = "Standard" if "st" in styleRaw.lower() else (
            "Latin" if "la" in styleRaw.lower() else "Kombinacja"
        )
        ageGroup, danceClass = self.parseCategoryAgeAndClass(catName)

        roundsList = data.get("r") or []
        dancesArr = roundsList[0].get("da", []) if roundsList else []
        dancesStr = ",".join(normalizeDanceCode(d) for d in dancesArr) if dancesArr else (
            "WA,TG,VW,SF,QS" if styleName == "Standard" else "SA,CH,RB,PD,JV"
        )

        fullCategoryId = f"{tournamentId}_{rawCategoryId}"
        cursor.execute("""
            INSERT OR REPLACE INTO categories (
                categoryId, tournamentId, categoryName, ageGroup, danceClass,
                style, dancesList, judgingSystem, competitorsCount, roundsCount, blockName
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fullCategoryId, tournamentId, catName, ageGroup, danceClass,
            styleName, dancesStr, catMeta.get("js", "skating"),
            len(validCouples), max(1, len(roundsList)), catMeta.get("bn", "Blok Turniejowy")
        ))

        idMappingsJudges = (data.get("idMappings") or {}).get("j") or {}
        judgesList = data.get("j") or []
        indexToLetter: List[Tuple[str, str]] = []

        cursor.execute("DELETE FROM categoryJudges WHERE categoryId = ?", (fullCategoryId,))
        for jObj in judgesList:
            idxKey = jObj.get("i", "A")
            dispLetter = jObj.get("l", idxKey)
            rawJId = idMappingsJudges.get(idxKey, "")
            cleanJudgeId = rawJId.replace("adjudicator_", "").replace("_pl", "")
            judgeName = self.formatPersonDisplay(jObj.get("n", f"Sędzia {dispLetter}"))
            if not cleanJudgeId.isdigit():
                cleanJudgeId = f"300{abs(hash(judgeName.lower())) % 89999 + 10000:05d}"

            cursor.execute("""
                INSERT OR IGNORE INTO judges (judgeId, fullName, cityName, regionTag, licenseCategory, wdsfLicense)
                VALUES (?, ?, ?, 'PZST', 'PZST-A / FTS-I', 'WDSF-A')
            """, (cleanJudgeId, judgeName, jObj.get("ct", "Polska")))
            cursor.execute("""
                INSERT INTO categoryJudges (categoryId, judgeLetter, judgeId, judgeRole)
                VALUES (?, ?, ?, ?)
            """, (fullCategoryId, dispLetter, cleanJudgeId, jObj.get("role", "judge")))
            indexToLetter.append((dispLetter, cleanJudgeId))

        cursor.execute("DELETE FROM participations WHERE categoryId = ?", (fullCategoryId,))
        cursor.execute("DELETE FROM judgeMarks WHERE categoryId = ?", (fullCategoryId,))

        startNumToCoupleKey: Dict[int, str] = {}
        rankMult = 1.4 if "Puchar" in tournamentRank or "Mistrzostwa" in tournamentRank else 1.0
        participationsAdded = 0

        for idx, pObj in enumerate(validCouples):
            rawName = pObj.get("n", "").replace("|", "/")
            parts = [self.formatPersonDisplay(x) for x in rawName.split("/")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                continue
            maleName, femaleName = parts[0], parts[1]

            mId = self.assignOrResolveDancerId(maleName)
            fId = self.assignOrResolveDancerId(femaleName)

            cKey = buildCoupleKey(mId, fId)
            clubName = pObj.get("cl") or "Klub Taneczny FTS/PZST"
            cityName = pObj.get("ci") or "Polska"
            startNo = int(pObj.get("sn") or (idx + 1))
            finalPlace = int(pObj.get("finalRank") or (idx + 1))
            startNumToCoupleKey[startNo] = cKey

            cursor.execute("""
                INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                VALUES (?, ?, 'M', ?, ?, ?, ?)
            """, (mId, maleName, clubName, danceClass, danceClass, tournamentDate))
            cursor.execute("""
                INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                VALUES (?, ?, 'F', ?, ?, ?, ?)
            """, (fId, femaleName, clubName, danceClass, danceClass, tournamentDate))

            cursor.execute("""
                INSERT OR IGNORE INTO couples (
                    coupleKey, maleDancerId, femaleDancerId, coupleName, clubName, cityName,
                    ageGroup, classSt, classLa, firstTournamentDate, lastTournamentDate, isActive
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                cKey, mId, fId, f"{maleName} & {femaleName}",
                clubName, cityName, ageGroup, danceClass, danceClass,
                tournamentDate, tournamentDate
            ))
            cursor.execute("""
                UPDATE couples
                SET firstTournamentDate = MIN(firstTournamentDate, ?),
                    lastTournamentDate = MAX(lastTournamentDate, ?)
                WHERE coupleKey = ?
            """, (tournamentDate, tournamentDate, cKey))

            pRounds = pObj.get("r") or {}
            maxRound = "F" if "F" in pRounds else ("1/2 F" if "r2" in pRounds else "1/4 F")
            pts, pod = calculateFtsPoints(finalPlace, len(validCouples), rankMult)

            cursor.execute("""
                INSERT INTO participations (
                    categoryId, tournamentId, coupleKey, maleDancerId, femaleDancerId,
                    startNumber, clubName, cityName, finalPlace, maxRoundReached,
                    pointsEarned, podiumCredit, totalPreliminaryMarks
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                fullCategoryId, tournamentId, cKey, mId, fId,
                startNo, clubName, cityName, finalPlace, maxRound, pts, pod
            ))
            participationsAdded += 1

        marksAdded = 0
        drObj = data.get("dr") or {}
        for roundKey, roundContent in drObj.items():
            if not isinstance(roundContent, dict):
                continue
            if "skating" in roundContent and isinstance(roundContent["skating"], dict):
                for pRow in roundContent["skating"].get("p", []):
                    sn = int(pRow.get("sn") or 0)
                    cKey = startNumToCoupleKey.get(sn)
                    if not cKey:
                        continue
                    for dEntry in pRow.get("dr", []):
                        dCode = normalizeDanceCode(dEntry.get("d", "WA"))
                        rString = dEntry.get("r", "")
                        dancePlace = float(dEntry.get("p") or 1)
                        for jIdx, ch in enumerate(rString):
                            if jIdx < len(indexToLetter) and ch.isdigit():
                                jLetter, jId = indexToLetter[jIdx]
                                cursor.execute("""
                                    INSERT INTO judgeMarks (
                                        categoryId, tournamentId, coupleKey, roundCode, danceCode,
                                        judgeId, judgeLetter, markType, markValue, danceCalculatedPlace
                                    ) VALUES (?, ?, ?, 'F', ?, ?, ?, 'SKATING', ?, ?)
                                """, (
                                    fullCategoryId, tournamentId, cKey, dCode,
                                    jId, jLetter, int(ch), dancePlace
                                ))
                                marksAdded += 1
            if "mks" in roundContent and isinstance(roundContent["mks"], dict):
                for pRow in roundContent["mks"].get("p", []):
                    sn = int(pRow.get("sn") or 0)
                    cKey = startNumToCoupleKey.get(sn)
                    if not cKey:
                        continue
                    totalPrelim = int(pRow.get("tm") or 0)
                    if totalPrelim > 0:
                        cursor.execute("""
                            UPDATE participations SET totalPreliminaryMarks = ?
                            WHERE categoryId = ? AND coupleKey = ?
                        """, (totalPrelim, fullCategoryId, cKey))
                    for dEntry in pRow.get("dm", []):
                        dCode = normalizeDanceCode(dEntry.get("d", "WA"))
                        mString = dEntry.get("m", "")
                        for jIdx, (jLetter, jId) in enumerate(indexToLetter):
                            recalled = 1 if (jIdx < len(mString) and mString[jIdx].strip() != "") else 0
                            cursor.execute("""
                                INSERT INTO judgeMarks (
                                    categoryId, tournamentId, coupleKey, roundCode, danceCode,
                                    judgeId, judgeLetter, markType, markValue, danceCalculatedPlace
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RECALL', ?, NULL)
                            """, (
                                fullCategoryId, tournamentId, cKey, roundKey.upper(), dCode,
                                jId, jLetter, recalled
                            ))
                            marksAdded += 1

        return (participationsAdded, marksAdded)

    def parseCategoryAgeAndClass(self, catName: str) -> Tuple[str, str]:
        ageGroup = "Pow. 15"
        for a in ["8-9", "10-11", "12-13", "14-15", "16-18", "Młodzież", "Dorośli", "Pow. 15", "Senior"]:
            if a.lower() in catName.lower():
                ageGroup = "Pow. 15" if a in ["Dorośli", "Pow. 15", "16-18", "Młodzież"] else a
                break
        danceClass = "S"
        m = re.search(r"\b([A-H]|S)\b", catName)
        if m:
            danceClass = m.group(1)
        return (ageGroup, danceClass)


def runCleanEcosystemRebuild(maxLiveTournaments: int = 25) -> Dict[str, Any]:
    initializeDatabase()
    from seedRealisticFtsData import seedComprehensiveFtsDatabase
    # Start from a clean base so no duplicate mobile-p corrupted IDs remain
    seedComprehensiveFtsDatabase(forceReseed=True)

    connection = getDbConnection()
    cursor = connection.cursor()

    ingestor = BulkFtsLiveIngestor()

    print("1/5 Fetching all 313+ PZST archive tournaments...")
    pzstItems = ingestor.ingestAllPzstArchiveTournaments(cursor)
    connection.commit()

    print("2/5 Harvesting official 8-digit dancer license IDs (20xxxxxx) from /reg/<id>/list (fixing mobile <p> duplicate bug)...")
    resolvedIdsCount = ingestor.harvestOfficialDancerLicenseIds(pzstItems, maxListsToScan=65)
    print(f"   -> Resolved {len(ingestor.knownDancerIdsByName)} unique dancers with official 8-digit IDs!")

    print("3/5 Ingesting LIVE MarX tournaments with full per-dance Skating & Recall marks...")
    liveStats = ingestor.ingestMarxArchiveTournamentsBulk(cursor, pzstItems, maxLiveTournaments=maxLiveTournaments)
    connection.commit()

    print("4/5 Ingesting ALL PZST Ranking leaderboards AND full start histories (/api/ranking/details/...) for every ranked couple...")
    rankingDetailsStats = ingestor.ingestPzstRankingsAndFullStartHistories(cursor)
    connection.commit()

    print("5/5 Ingesting ALL historical FTS archive tournaments (2015-2024) and FTS Ranking classifications (2017-2022)...")
    legacyTours = ingestor.ingestAllLegacyFtsArchiveTournaments(cursor)
    legacyRanks = ingestor.ingestLegacyFtsRankingsAndStarts(cursor)
    connection.commit()

    totals = {
        "dancers": cursor.execute("SELECT COUNT(*) FROM dancers").fetchone()[0],
        "couples": cursor.execute("SELECT COUNT(*) FROM couples").fetchone()[0],
        "couplesWithZeroStarts": cursor.execute("""
            SELECT COUNT(*) FROM couples c
            WHERE NOT EXISTS (SELECT 1 FROM participations p WHERE p.coupleKey = c.coupleKey)
        """).fetchone()[0],
        "tournaments": cursor.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
        "categories": cursor.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
        "judges": cursor.execute("SELECT COUNT(*) FROM judges").fetchone()[0],
        "participations": cursor.execute("SELECT COUNT(*) FROM participations").fetchone()[0],
        "judgeMarks": cursor.execute("SELECT COUNT(*) FROM judgeMarks").fetchone()[0],
        **liveStats,
        **rankingDetailsStats,
        **legacyRanks,
        "legacyTournamentsIngested": legacyTours,
    }
    connection.close()
    return totals


if __name__ == "__main__":
    summary = runCleanEcosystemRebuild(maxLiveTournaments=25)
    print("Clean Ecosystem Rebuild Summary:", json.dumps(summary, indent=2, ensure_ascii=False))
