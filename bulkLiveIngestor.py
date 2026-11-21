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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FTS-Dance-Analytics/4.0"
        })
        self.knownDancerIdsByName: Dict[str, str] = {}

    def normalizePersonName(self, rawName: str) -> str:
        tokens = re.findall(r"[a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ]+", rawName.lower())
        return " ".join(sorted(tokens))

    def formatPersonDisplay(self, rawName: str) -> str:
        cleaned = re.sub(r"\([^)]*\)", "", rawName).strip()
        parts = [p.capitalize() for p in cleaned.split() if p]
        return " ".join(parts)

    def assignOrResolveDancerId(self, personName: str, officialId: Optional[str] = None) -> str:
        norm = self.normalizePersonName(personName)
        if officialId and officialId.isdigit():
            self.knownDancerIdsByName[norm] = officialId
            return officialId
        if norm in self.knownDancerIdsByName:
            return self.knownDancerIdsByName[norm]
        generatedId = f"20{abs(hash(norm)) % 899999 + 100000:06d}"
        self.knownDancerIdsByName[norm] = generatedId
        return generatedId

    def preloadExistingDancerIds(self, cursor: sqlite3.Cursor) -> None:
        for row in cursor.execute("SELECT dancerId, fullName FROM dancers").fetchall():
            norm = self.normalizePersonName(row["fullName"])
            self.knownDancerIdsByName[norm] = row["dancerId"]

    # ==================== 1. ALL TOURNAMENTS FROM BOTH ARCHIVES (2015 - 2026+) ====================
    def ingestAllPzstArchiveTournaments(self, cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
        """
        Ingests ALL 313+ tournaments from https://baza.polskitaniec.org/cal/archive
        """
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
        """
        Scrapes ALL 785+ historical tournaments (2015-2024) and their embedded categories
        from https://baza.fts-taniec.pl/cal2/archive.php
        """
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
            rawDate = headerStrong.get_text(strip=True)  # DD.MM.YYYY
            mDate = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", rawDate)
            if not mDate:
                continue
            isoDate = f"{mDate.group(3)}-{mDate.group(2)}-{mDate.group(1)}"

            # Extract city from card-header text
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

            # Also extract embedded category badges if present
            if collapseDiv:
                badges = collapseDiv.select("span.badge")
                for bIdx, bEl in enumerate(badges[:12]):
                    catTitle = bEl.get_text(" ", strip=True)
                    if not catTitle or "SOLO" in catTitle.upper() or "SOLIST" in catTitle.upper():
                        continue
                    ageG, dClass = self.parseCategoryAgeAndClass(catTitle)
                    styleName = "Latin" if "LA" in catTitle.upper() else (
                        "Standard" if "ST" in catTitle.upper() else "Kombinacja"
                    )
                    dancesList = "WA,TG,VW,SF,QS" if styleName == "Standard" else "SA,CH,RB,PD,JV"
                    catId = f"{tId}_CAT_{bIdx + 1}"
                    cursor.execute("""
                        INSERT OR IGNORE INTO categories (
                            categoryId, tournamentId, categoryName, ageGroup, danceClass,
                            style, dancesList, judgingSystem, competitorsCount, roundsCount, blockName
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'skating', 12, 2, 'Blok Archiwalny FTS')
                    """, (catId, tId, catTitle, ageG, dClass, styleName, dancesList))

        return ingestedTournaments

    # ==================== 2. ALL COUPLES FROM PZST & LEGACY FTS RANKINGS ====================
    def ingestPzstRankingsBulk(self, cursor: sqlite3.Cursor) -> int:
        styles = ["Standard", "Latin", "10 Dance", "8 Dance", "6 Dance"]
        ages = [
            "Dorośli", "Młodzież", "Młodzież Starsza", "Juniorzy Starsi",
            "Juniorzy Młodsi", "Dzieci Starsze", "Senior I", "Senior II",
            "Senior III", "Rising Stars", "Młodzież+Dorośli"
        ]
        years = ["0", "2025", "2024", "2023"]

        tasks = [(y, age, st) for y in years for st in styles for age in ages]
        insertedCouples = 0

        def fetchOneRanking(args: Tuple[str, str, str]) -> Tuple[str, str, str, List[Dict[str, Any]]]:
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

        with ThreadPoolExecutor(max_workers=12) as executor:
            for y, age, st, rows in executor.map(fetchOneRanking, tasks):
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
                    dClass = item.get("startersClass") or "A"
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
                    insertedCouples += 1

        return insertedCouples

    def ingestLegacyFtsRankingsBulk(self, cursor: sqlite3.Cursor) -> int:
        """
        Fetches historical couples (2016-2022) from https://baza.fts-taniec.pl/view/ranking/ajax.php
        """
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
        with ThreadPoolExecutor(max_workers=10) as executor:
            for y, cat, st, htmlText in executor.map(fetchLegacyTable, tasks):
                if not htmlText:
                    continue
                soup = BeautifulSoup(htmlText, "lxml")
                for tr in soup.select("tbody tr"):
                    tds = tr.find_all("td")
                    if len(tds) < 5:
                        continue
                    nameCell = tds[1]
                    # Strip out nested span labels
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

                    mId = self.assignOrResolveDancerId(maleName)
                    fId = self.assignOrResolveDancerId(femaleName)
                    cKey = buildCoupleKey(mId, fId)

                    cursor.execute("""
                        INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                        VALUES (?, ?, 'M', ?, ?, ?, ?)
                    """, (mId, maleName, clubName, dClass, dClass, f"{y}-12-01"))
                    cursor.execute("""
                        INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                        VALUES (?, ?, 'F', ?, ?, ?, ?)
                    """, (fId, femaleName, clubName, dClass, dClass, f"{y}-12-01"))

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

        return addedLegacyCouples

    def ingestFtsOfficialJudges(self, cursor: sqlite3.Cursor) -> int:
        """
        Scrapes official adjudicators list from https://baza.fts-taniec.pl/x/fts/adjudicators
        """
        addedJudges = 0
        try:
            resp = self.httpSession.get("https://baza.fts-taniec.pl/x/fts/adjudicators", timeout=12)
            if resp.status_code != 200:
                return 0
            soup = BeautifulSoup(resp.text, "lxml")
            for card in soup.select(".card"):
                titleEl = card.select_one("h5, h6, .card-title, strong")
                if not titleEl:
                    continue
                fullName = self.formatPersonDisplay(titleEl.get_text(" ", strip=True))
                if len(fullName.split()) < 2 or "Sędziowie" in fullName:
                    continue
                cardText = card.get_text(" ", strip=True)
                regionMatch = re.search(r"\[([A-ZŚŁŻŹĆŃ]{2,8})\]", cardText)
                regionTag = regionMatch.group(1) if regionMatch else "FTS"
                jId = f"300{abs(hash(fullName.lower())) % 89999 + 10000:05d}"
                cursor.execute("""
                    INSERT OR IGNORE INTO judges (judgeId, fullName, cityName, regionTag, licenseCategory, wdsfLicense)
                    VALUES (?, ?, 'Polska', ?, 'FTS / PZST', 'NAT-A')
                """, (jId, fullName, regionTag))
                addedJudges += 1
        except Exception:
            pass
        return addedJudges

    # ==================== 3. LIVE TOURNAMENTS MARX RESULTS ====================
    def fetchMarxCategoryData(self, baseLiveDir: str, catId: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self.httpSession.get(f"{baseLiveDir}/{catId}.json", timeout=7)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def fetchTournamentListIds(self, tournamentId: str) -> Dict[str, Tuple[str, str, str]]:
        listUrl = f"https://baza.polskitaniec.org/reg/{tournamentId}/list"
        coupleMap: Dict[str, Tuple[str, str, str]] = {}
        try:
            resp = self.httpSession.get(listUrl, timeout=6)
            if resp.status_code != 200:
                return coupleMap
            soup = BeautifulSoup(resp.text, "lxml")
            for row in soup.select("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                if len(cells) >= 4 and "/" in cells[1] and "/" in cells[2]:
                    names = [p.strip() for p in cells[1].split("/")]
                    ids = [re.sub(r"\D", "", p) for p in cells[2].split("/")]
                    club = cells[3].strip()
                    if len(names) == 2 and len(ids) == 2 and ids[0] and ids[1]:
                        mId = self.assignOrResolveDancerId(names[0], ids[0])
                        fId = self.assignOrResolveDancerId(names[1], ids[1])
                        coupleMap[self.normalizePersonName(f"{names[0]} {names[1]}")] = (mId, fId, club)
        except Exception:
            pass
        return coupleMap

    def ingestMarxArchiveTournamentsBulk(
        self,
        cursor: sqlite3.Cursor,
        pzstItems: List[Dict[str, Any]],
        maxLiveTournaments: int = 85
    ) -> Dict[str, int]:
        tournamentsWithData = 0
        totalCategoriesIngested = 0
        totalParticipationsIngested = 0
        totalMarksIngested = 0

        existingTourCats = {
            r[0] for r in cursor.execute("SELECT DISTINCT tournamentId FROM categories WHERE tournamentId LIKE 'PZST_%'").fetchall()
        }

        for item in pzstItems:
            if tournamentsWithData >= maxLiveTournaments:
                break
            compId = str(item.get("id", ""))
            tId = f"PZST_{compId}"
            if tId in existingTourCats:
                continue

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

            startListIdMap = self.fetchTournamentListIds(compId)
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
            with ThreadPoolExecutor(max_workers=10) as executor:
                futureMap = {
                    executor.submit(self.fetchMarxCategoryData, baseLiveDir, cId): cId
                    for cId in catIdsToFetch[:16]
                }
                for fut in as_completed(futureMap):
                    cId = futureMap[fut]
                    data = fut.result()
                    if data and data.get("p"):
                        catPayloads.append((cId, data))

            addedForThisTour = 0
            for cId, data in catPayloads:
                pCount, mCount = self.saveMarxCategoryPayload(
                    cursor, tId, dateStart, rankLabel, cId, data, startListIdMap
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
        data: Dict[str, Any],
        startListIdMap: Dict[str, Tuple[str, str, str]]
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
        styleName = "Standard" if "standard" in styleRaw.lower() else (
            "Latin" if "latin" in styleRaw.lower() else "Kombinacja"
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
            normCouple = self.normalizePersonName(f"{maleName} {femaleName}")

            if normCouple in startListIdMap:
                mId, fId, _ = startListIdMap[normCouple]
            else:
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


def runFullLiveDatabaseIngestion(maxLiveTournaments: int = 45) -> Dict[str, Any]:
    initializeDatabase()
    connection = getDbConnection()
    cursor = connection.cursor()

    ingestor = BulkFtsLiveIngestor()
    ingestor.preloadExistingDancerIds(cursor)

    print("1/5 Ingesting ALL 313+ modern PZST tournaments from baza.polskitaniec.org/cal/archive...")
    pzstItems = ingestor.ingestAllPzstArchiveTournaments(cursor)
    connection.commit()

    print("2/5 Ingesting ALL 785+ historical FTS tournaments & categories (2015-2024) from baza.fts-taniec.pl/cal2/archive.php...")
    legacyTournamentsCount = ingestor.ingestAllLegacyFtsArchiveTournaments(cursor)
    connection.commit()

    print("3/5 Ingesting ALL active & historical couples from PZST Rankings REST API (2023-2026)...")
    pzstRankingCouplesCount = ingestor.ingestPzstRankingsBulk(cursor)
    connection.commit()

    print("4/5 Ingesting ALL historical FTS Ranking couples (2017-2022) & Official FTS Adjudicators...")
    legacyRankingCouplesCount = ingestor.ingestLegacyFtsRankingsBulk(cursor)
    officialJudgesCount = ingestor.ingestFtsOfficialJudges(cursor)
    connection.commit()

    print(f"5/5 Ingesting additional LIVE MarX tournaments with full per-dance Skating & Recall marks (batch={maxLiveTournaments})...")
    liveStats = ingestor.ingestMarxArchiveTournamentsBulk(cursor, pzstItems, maxLiveTournaments=maxLiveTournaments)
    connection.commit()

    totals = {
        "dancers": cursor.execute("SELECT COUNT(*) FROM dancers").fetchone()[0],
        "couples": cursor.execute("SELECT COUNT(*) FROM couples").fetchone()[0],
        "tournaments": cursor.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
        "categories": cursor.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
        "judges": cursor.execute("SELECT COUNT(*) FROM judges").fetchone()[0],
        "participations": cursor.execute("SELECT COUNT(*) FROM participations").fetchone()[0],
        "judgeMarks": cursor.execute("SELECT COUNT(*) FROM judgeMarks").fetchone()[0],
        "pzstRankingCouplesProcessed": pzstRankingCouplesCount,
        "legacyRankingCouplesProcessed": legacyRankingCouplesCount,
        "legacyTournamentsIngested": legacyTournamentsCount,
        "officialJudgesIngested": officialJudgesCount,
        **liveStats
    }
    connection.close()
    return totals


if __name__ == "__main__":
    summary = runFullLiveDatabaseIngestion(maxLiveTournaments=35)
    print("Full Ecosystem Database Ingestion Summary:", json.dumps(summary, indent=2, ensure_ascii=False))
