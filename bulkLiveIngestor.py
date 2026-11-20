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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FTS-Dance-Analytics/3.0"
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

    def fetchTournamentListIds(self, tournamentId: str, isLegacy: bool = False) -> Dict[str, Tuple[str, str, str]]:
        baseUrl = "https://baza.fts-taniec.pl/x/reg" if isLegacy else "https://baza.polskitaniec.org/reg"
        listUrl = f"{baseUrl}/{tournamentId}/list"
        coupleMap: Dict[str, Tuple[str, str, str]] = {}
        try:
            resp = self.httpSession.get(listUrl, timeout=8)
            if resp.status_code != 200:
                return coupleMap
            soup = BeautifulSoup(resp.text, "html.parser")
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
                elif len(cells) >= 6:
                    mName, mRawId = cells[1].strip(), re.sub(r"\D", "", cells[2])
                    fName, fRawId = cells[3].strip(), re.sub(r"\D", "", cells[4])
                    club = cells[5].strip()
                    if mRawId and fRawId:
                        mId = self.assignOrResolveDancerId(mName, mRawId)
                        fId = self.assignOrResolveDancerId(fName, fRawId)
                        coupleMap[self.normalizePersonName(f"{mName} {fName}")] = (mId, fId, club)
        except Exception:
            pass
        return coupleMap

    def ingestPzstRankingsBulk(self, cursor: sqlite3.Cursor) -> int:
        """
        Fetches real couples and dancer IDs from https://baza.polskitaniec.org/api/ranking/...
        across all age categories and styles!
        """
        styles = ["Standard", "Latin", "10 Dance"]
        ages = ["Dorośli", "Młodzież", "Juniorzy Starsi", "Juniorzy Młodsi", "Młodzież Starsza", "Seniorzy I"]
        insertedCouples = 0

        for style in styles:
            for age in ages:
                url = f"https://baza.polskitaniec.org/api/ranking/3/0/{requests.utils.quote(age)}/current/{requests.utils.quote(style)}"
                try:
                    resp = self.httpSession.get(url, timeout=8)
                    if resp.status_code != 200:
                        continue
                    rows = resp.json()
                    if not isinstance(rows, list):
                        continue
                    for item in rows:
                        athletesStr = item.get("athletes", "")
                        if "/" not in athletesStr:
                            continue
                        parts = [self.formatPersonDisplay(x) for x in athletesStr.split("/")]
                        if len(parts) != 2 or not parts[0] or not parts[1]:
                            continue
                        maleName, femaleName = parts[0], parts[1]
                        starterId = str(item.get("idStarter", ""))
                        clubName = item.get("club", "Klub PZST")
                        mId = self.assignOrResolveDancerId(maleName)
                        fId = self.assignOrResolveDancerId(femaleName)
                        cKey = buildCoupleKey(mId, fId)

                        cursor.execute("""
                            INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                            VALUES (?, ?, 'M', ?, 'A', 'A', '2026-11-15')
                        """, (mId, maleName, clubName))
                        cursor.execute("""
                            INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                            VALUES (?, ?, 'F', ?, 'A', 'A', '2026-11-15')
                        """, (fId, femaleName, clubName))

                        ageLabel = "Pow. 15" if age in ["Dorośli", "Młodzież", "Młodzież Starsza"] else (
                            "14-15" if "Starsi" in age else ("12-13" if "Młodsi" in age else age)
                        )
                        cursor.execute("""
                            INSERT OR IGNORE INTO couples (
                                coupleKey, maleDancerId, femaleDancerId, starterId, coupleName,
                                clubName, cityName, ageGroup, classSt, classLa,
                                firstTournamentDate, lastTournamentDate, isActive
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'A', 'A', '2025-01-01', '2026-11-15', 1)
                        """, (
                            cKey, mId, fId, starterId, f"{maleName} & {femaleName}",
                            clubName, clubName.split()[-1] if clubName else "Polska", ageLabel
                        ))
                        insertedCouples += 1
                except Exception:
                    continue
        return insertedCouples

    def fetchMarxCategoryData(self, baseLiveDir: str, catId: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self.httpSession.get(f"{baseLiveDir}/{catId}.json", timeout=8)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def ingestMarxArchiveTournamentsBulk(self, cursor: sqlite3.Cursor, maxLiveTournaments: int = 22) -> Dict[str, int]:
        resp = self.httpSession.get("https://baza.polskitaniec.org/cal/archive", timeout=12)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("items", []) if isinstance(payload, dict) else payload

        liveTournamentsProcessed = 0
        totalCategoriesIngested = 0
        totalParticipationsIngested = 0
        totalMarksIngested = 0

        for item in items:
            if liveTournamentsProcessed >= maxLiveTournaments:
                break
            compId = str(item.get("id", ""))
            compName = item.get("competitionName", "Turniej PZST")
            cityName = (item.get("city") or {}).get("name", "Polska")
            dateStart = item.get("dateStart", "2026-01-01")
            dateEnd = item.get("dateEnd", dateStart)
            venues = item.get("venues") or []
            venueName = venues[0].get("name", "") if venues else ""
            venueAddress = venues[0].get("address", "") if venues else ""

            liveUrl = None
            for lObj in (item.get("links") or []):
                href = lObj.get("link", "")
                if "LIVE" in href and not lObj.get("isPdf", False):
                    liveUrl = href
                    break

            if not liveUrl or not liveUrl.endswith("index.php"):
                continue

            baseLiveDir = liveUrl.rsplit("/", 1)[0]
            try:
                ttResp = self.httpSession.get(f"{baseLiveDir}/timetable.json", timeout=7)
                if ttResp.status_code != 200:
                    continue
                ttData = ttResp.json()
            except Exception:
                continue

            # Prefetch official 8-digit dancer license IDs from /reg/<compId>/list
            startListIdMap = self.fetchTournamentListIds(compId, isLegacy=False)

            tId = f"PZST_{compId}"
            rankLabel = "Mistrzostwa Polski" if "mistrzostwa polski" in compName.lower() else (
                "Puchar Polski" if "puchar" in compName.lower() else (
                    "Grand Prix Polski" if "grand prix" in compName.lower() else "OTTS / Turniej FTS"
                )
            )
            cursor.execute("""
                INSERT OR REPLACE INTO tournaments (
                    tournamentId, sourceSystem, competitionName, cityName, voivodeship,
                    venueName, venueAddress, dateStart, dateEnd, tournamentRank,
                    portalUrl, liveResultsUrl, isArchived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                tId, "PZST_MARX_LIVE", compName, cityName, resolveVoivodeship(cityName),
                venueName, venueAddress, dateStart, dateEnd, rankLabel,
                f"https://baza.polskitaniec.org/reg/{compId}", liveUrl
            ))

            # Collect all non-solo couple categories from timetable.json
            catIdsToFetch = []
            seenCats = set()
            for entry in ttData.get("timetable", []):
                cId = entry.get("categoryId")
                cText = entry.get("text", "").upper()
                if cId and cId not in seenCats and "SOLO" not in cText and "FORMACJ" not in cText:
                    seenCats.add(cId)
                    catIdsToFetch.append(cId)

            # Fetch category JSONs concurrently for speed
            catPayloads: List[Tuple[str, Dict[str, Any]]] = []
            with ThreadPoolExecutor(max_workers=8) as executor:
                futureMap = {
                    executor.submit(self.fetchMarxCategoryData, baseLiveDir, cId): cId
                    for cId in catIdsToFetch[:18]
                }
                for fut in as_completed(futureMap):
                    cId = futureMap[fut]
                    data = fut.result()
                    if data and data.get("p"):
                        catPayloads.append((cId, data))

            if not catPayloads:
                continue

            liveTournamentsProcessed += 1
            for cId, data in catPayloads:
                pCount, mCount = self.saveMarxCategoryPayload(
                    cursor, tId, dateStart, rankLabel, cId, data, startListIdMap
                )
                if pCount > 0:
                    totalCategoriesIngested += 1
                    totalParticipationsIngested += pCount
                    totalMarksIngested += mCount

        return {
            "liveTournamentsProcessed": liveTournamentsProcessed,
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

        # Judges
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

        # Participants
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
            # Update couple date bounds
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

        # Detailed Marks (Skating & Recalls)
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


def runFullLiveDatabaseIngestion(maxLiveTournaments: int = 22) -> Dict[str, Any]:
    initializeDatabase()
    connection = getDbConnection()
    cursor = connection.cursor()

    ingestor = BulkFtsLiveIngestor()
    print("1/2 Fetching real couples from PZST Rankings REST API...")
    rankingCouplesCount = ingestor.ingestPzstRankingsBulk(cursor)
    connection.commit()

    print(f"2/2 Fetching real LIVE tournaments, start lists, categories, and Skating/Recall marks (up to {maxLiveTournaments} tournaments)...")
    liveStats = ingestor.ingestMarxArchiveTournamentsBulk(cursor, maxLiveTournaments=maxLiveTournaments)
    connection.commit()

    totals = {
        "dancers": cursor.execute("SELECT COUNT(*) FROM dancers").fetchone()[0],
        "couples": cursor.execute("SELECT COUNT(*) FROM couples").fetchone()[0],
        "tournaments": cursor.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
        "categories": cursor.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
        "judges": cursor.execute("SELECT COUNT(*) FROM judges").fetchone()[0],
        "participations": cursor.execute("SELECT COUNT(*) FROM participations").fetchone()[0],
        "judgeMarks": cursor.execute("SELECT COUNT(*) FROM judgeMarks").fetchone()[0],
        "rankingCouplesAdded": rankingCouplesCount,
        **liveStats
    }
    connection.close()
    return totals


if __name__ == "__main__":
    summary = runFullLiveDatabaseIngestion(maxLiveTournaments=22)
    print("Full Live Database Ingestion Summary:", json.dumps(summary, indent=2, ensure_ascii=False))
