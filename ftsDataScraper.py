import re
import json
import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from databaseManager import getDbConnection, initializeDatabase

CITY_VOIVODESHIP_MAP = {
    "Słupsk": "Pomorskie",
    "Pruszcz Gdański": "Pomorskie",
    "Gdańsk": "Pomorskie",
    "Gdynia": "Pomorskie",
    "Elbląg": "Warmińsko-Mazurskie",
    "Olsztyn": "Warmińsko-Mazurskie",
    "Warszawa": "Mazowieckie",
    "Radom": "Mazowieckie",
    "Kraków": "Małopolskie",
    "Wrocław": "Dolnośląskie",
    "Kiełczów": "Dolnośląskie",
    "Poznań": "Wielkopolskie",
    "Konin": "Wielkopolskie",
    "Katowice": "Śląskie",
    "Gliwice": "Śląskie",
    "Łódź": "Łódzkie",
    "Szczecin": "Zachodniopomorskie",
    "Lublin": "Lubelskie",
    "Rzeszów": "Podkarpackie",
    "Białystok": "Podlaskie",
    "Bydgoszcz": "Kujawsko-Pomorskie",
    "Toruń": "Kujawsko-Pomorskie",
    "Zielona Góra": "Lubuskie",
    "Opole": "Opolskie",
    "Kielce": "Świętokrzyskie"
}


def resolveVoivodeship(cityName: Optional[str]) -> str:
    if not cityName:
        return "Ogólnopolskie"
    for keyCity, voivodeship in CITY_VOIVODESHIP_MAP.items():
        if keyCity.lower() in cityName.lower():
            return voivodeship
    return "Mazowieckie"


def buildCoupleKey(maleId: str, femaleId: str) -> str:
    return f"{maleId}_{femaleId}"


def calculateFtsPoints(finalPlace: int, competitorsCount: int, rankMultiplier: float = 1.0) -> Tuple[float, int]:
    """
    Calculates official PZST / FTS Class Points (Punkty Klasowe 'Pkt. kl.') and Podium Credits ('M1'):
    - Base points = max(0, competitorsCount - finalPlace) + 5  (if finalPlace < competitorsCount, else 0)
    - Podium bonus (only when competitorsCount >= 6): 1st = +15 (M1), 2nd = +10 (M1), 3rd = +5 (M1)
    - Multiplied by class multiplier (typically 1.0)
    """
    if competitorsCount <= 1 or finalPlace >= competitorsCount:
        return (0.0, 0)
    beatenCouples = competitorsCount - finalPlace
    basePoints = float(beatenCouples + 5)
    podiumBonus = 0.0
    podiumCredit = 0
    if competitorsCount >= 6 and finalPlace <= 3:
        podiumBonus = {1: 15.0, 2: 10.0, 3: 5.0}.get(finalPlace, 0.0)
        podiumCredit = 1
    totalClassPoints = round((basePoints + podiumBonus) * rankMultiplier, 2)
    return (totalClassPoints, podiumCredit)


def normalizeDanceCode(rawDanceCode: str) -> str:
    cleaned = rawDanceCode.replace("d-", "").upper().strip()
    mapping = {
        "WA": "WA", "WALTZ": "WA", "SW": "WA",
        "TG": "TG", "TANGO": "TG", "TA": "TG",
        "VW": "VW", "VIENNESE": "VW",
        "SF": "SF", "SLOWFOX": "SF", "FOXTROT": "SF",
        "QS": "QS", "QUICKSTEP": "QS",
        "SA": "SA", "SAMBA": "SA", "SB": "SA",
        "CH": "CH", "CHA": "CH", "CC": "CH",
        "RB": "RB", "RUMBA": "RB", "RU": "RB",
        "PD": "PD", "PASO": "PD", "PB": "PD",
        "JV": "JV", "JIVE": "JV", "JI": "JV"
    }
    return mapping.get(cleaned, cleaned)


class FtsDataSyncManager:
    def __init__(self) -> None:
        self.httpSession = requests.Session()
        self.httpSession.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FTS-Dance-Analytics/2.0"
        })

    def fetchPzstArchiveList(self, limitItems: int = 40) -> List[Dict[str, Any]]:
        response = self.httpSession.get("https://baza.polskitaniec.org/cal/archive", timeout=12)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else payload
        return items[:limitItems]

    def fetchTournamentStartListIds(self, portalBaseUrl: str, tournamentId: str) -> Dict[str, Tuple[str, str, str]]:
        """
        Scrapes /reg/<id>/list or /x/reg/<id>/list to extract 8-digit dancer license IDs (20xxxxxx)
        mapped by normalized couple name -> (maleId, femaleId, clubName).
        """
        listUrl = f"{portalBaseUrl.rstrip('/')}/{tournamentId}/list"
        nameToIdsMap: Dict[str, Tuple[str, str, str]] = {}
        try:
            response = self.httpSession.get(listUrl, timeout=10)
            if response.status_code != 200:
                return nameToIdsMap
            soup = BeautifulSoup(response.text, "html.parser")
            for row in soup.select("tr"):
                for mobileP in row.select("p.d-md-none, p.d-xs-inline"):
                    mobileP.decompose()
                cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                if len(cells) >= 4 and "/" in cells[1] and "/" in cells[2]:
                    namesParts = [re.sub(r"\d{6,8}", "", p).strip() for p in cells[1].split("/") if p.strip()]
                    idsParts = re.findall(r"\b\d{7,8}\b", cells[2])
                    clubText = cells[3].strip()
                    if len(namesParts) == 2 and len(idsParts) == 2:
                        normKey = self.normalizeCoupleNameKey(f"{namesParts[0]} / {namesParts[1]}")
                        nameToIdsMap[normKey] = (idsParts[0], idsParts[1], clubText)
                elif len(cells) >= 6:
                    maleName, maleId = cells[1].strip(), re.sub(r"\D", "", cells[2])
                    femaleName, femaleId = cells[3].strip(), re.sub(r"\D", "", cells[4])
                    clubText = cells[5].strip()
                    if maleId and femaleId:
                        normKey = self.normalizeCoupleNameKey(f"{maleName} / {femaleName}")
                        nameToIdsMap[normKey] = (maleId, femaleId, clubText)
        except Exception:
            pass
        return nameToIdsMap

    def normalizeCoupleNameKey(self, rawName: str) -> str:
        tokens = re.findall(r"[a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ]+", rawName.lower())
        return "_".join(sorted(tokens))

    def syncFromLiveSources(self, maxTournaments: int = 8) -> Dict[str, Any]:
        connection = getDbConnection()
        cursor = connection.cursor()
        syncedTournaments = 0
        syncedCategories = 0
        syncedMarks = 0

        try:
            archiveItems = self.fetchPzstArchiveList(limitItems=maxTournaments * 2)
            for item in archiveItems:
                if syncedTournaments >= maxTournaments:
                    break
                compId = str(item.get("id", ""))
                compName = item.get("competitionName", "Turniej PZST/FTS")
                cityObj = item.get("city") or {}
                cityName = cityObj.get("name", "Polska")
                dateStart = item.get("dateStart", "2026-01-01")
                dateEnd = item.get("dateEnd", dateStart)
                venues = item.get("venues") or []
                venueName = venues[0].get("name", "") if venues else ""
                venueAddress = venues[0].get("address", "") if venues else ""
                links = item.get("links") or []

                liveUrl = None
                for linkObj in links:
                    href = linkObj.get("link", "")
                    if "LIVE" in href and not linkObj.get("isPdf", False):
                        liveUrl = href
                        break

                rankLabel = "Puchar Polski" if "puchar" in compName.lower() else (
                    "Mistrzostwa Polski" if "mistrzostwa polski" in compName.lower() else "OTTS / Grand Prix"
                )
                portalUrl = f"https://baza.polskitaniec.org/reg/{compId}"

                cursor.execute("""
                    INSERT OR REPLACE INTO tournaments (
                        tournamentId, sourceSystem, competitionName, cityName, voivodeship,
                        venueName, venueAddress, dateStart, dateEnd, tournamentRank,
                        portalUrl, liveResultsUrl, isArchived
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (
                    f"PZST_{compId}", "PZST_MARX", compName, cityName, resolveVoivodeship(cityName),
                    venueName, venueAddress, dateStart, dateEnd, rankLabel,
                    portalUrl, liveUrl or ""
                ))
                syncedTournaments += 1

                if liveUrl and liveUrl.endswith("index.php"):
                    baseLiveDir = liveUrl.rsplit("/", 1)[0]
                    ttUrl = f"{baseLiveDir}/timetable.json"
                    ttResp = self.httpSession.get(ttUrl, timeout=8)
                    if ttResp.status_code == 200:
                        idMap = self.fetchTournamentStartListIds("https://baza.polskitaniec.org/reg", compId)
                        ttData = ttResp.json()
                        seenCats = set()
                        for entry in ttData.get("timetable", []):
                            catId = entry.get("categoryId")
                            if catId and catId not in seenCats and "SOLO" not in entry.get("text", "").upper():
                                seenCats.add(catId)
                                if len(seenCats) <= 4:
                                    cCount, mCount = self.ingestMarxCategoryJson(
                                        cursor, f"PZST_{compId}", dateStart, baseLiveDir, catId, idMap
                                    )
                                    syncedCategories += cCount
                                    syncedMarks += mCount

            connection.commit()
        except Exception as err:
            connection.rollback()
            connection.close()
            return {"status": "partial_or_offline", "error": str(err), "syncedTournaments": syncedTournaments}

        connection.close()
        return {
            "status": "ok",
            "syncedTournaments": syncedTournaments,
            "syncedCategories": syncedCategories,
            "syncedMarks": syncedMarks
        }

    def ingestMarxCategoryJson(
        self,
        cursor: sqlite3.Cursor,
        tournamentId: str,
        tournamentDate: str,
        baseLiveDir: str,
        categoryId: str,
        nameToIdsMap: Dict[str, Tuple[str, str, str]]
    ) -> Tuple[int, int]:
        catUrl = f"{baseLiveDir}/{categoryId}.json"
        resp = self.httpSession.get(catUrl, timeout=8)
        if resp.status_code != 200:
            return (0, 0)
        data = resp.json()
        catMeta = data.get("c", {})
        catName = catMeta.get("n", categoryId)
        styleRaw = catMeta.get("s", "Standard")
        styleName = "Standard" if "standard" in styleRaw.lower() else (
            "Latin" if "latin" in styleRaw.lower() else "Kombinacja"
        )
        participants = data.get("p", [])
        if not participants:
            return (0, 0)

        ageGroup, danceClass = self.parseAgeAndClass(catName)
        roundsList = data.get("r", [])
        dancesArr = roundsList[0].get("da", []) if roundsList else []
        dancesStr = ",".join(normalizeDanceCode(d) for d in dancesArr) if dancesArr else (
            "WA,TG,VW,SF,QS" if styleName == "Standard" else "SA,CH,RB,PD,JV"
        )

        fullCategoryId = f"{tournamentId}_{categoryId}"
        cursor.execute("""
            INSERT OR REPLACE INTO categories (
                categoryId, tournamentId, categoryName, ageGroup, danceClass,
                style, dancesList, judgingSystem, competitorsCount, roundsCount, blockName
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fullCategoryId, tournamentId, catName, ageGroup, danceClass,
            styleName, dancesStr, catMeta.get("js", "skating"),
            len(participants), len(roundsList), catMeta.get("bn", "Blok Główny")
        ))

        idMappingsJudges = (data.get("idMappings") or {}).get("j") or {}
        judgesList = data.get("j", [])
        letterToJudgeId: Dict[str, str] = {}
        indexToLetter: List[Tuple[str, str]] = []

        for jObj in judgesList:
            idxKey = jObj.get("i", "A")
            dispLetter = jObj.get("l", idxKey)
            rawJudgeId = idMappingsJudges.get(idxKey, f"ADJ_{dispLetter}_{ re.sub(r'[^A-Za-z]', '', jObj.get('n', '')) }")
            cleanJudgeId = rawJudgeId.replace("adjudicator_", "").replace("_pl", "")
            if not cleanJudgeId.isdigit():
                cleanJudgeId = f"300{abs(hash(jObj.get('n', dispLetter))) % 100000:05d}"
            judgeName = jObj.get("n", f"Sędzia {dispLetter}")
            cursor.execute("""
                INSERT OR IGNORE INTO judges (judgeId, fullName, cityName, regionTag, licenseCategory, wdsfLicense)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cleanJudgeId, judgeName, "Polska", "PZST", "PZST-A / FTS-I", "WDSF-A"))
            cursor.execute("""
                INSERT INTO categoryJudges (categoryId, judgeLetter, judgeId, judgeRole)
                VALUES (?, ?, ?, ?)
            """, (fullCategoryId, dispLetter, cleanJudgeId, jObj.get("role", "judge")))
            letterToJudgeId[dispLetter] = cleanJudgeId
            indexToLetter.append((dispLetter, cleanJudgeId))

        startNumToCoupleKey: Dict[int, str] = {}
        for pObj in participants:
            rawCoupleName = pObj.get("n", "").replace("|", "/")
            if "/" not in rawCoupleName:
                continue
            parts = [x.strip() for x in rawCoupleName.split("/")]
            maleName, femaleName = parts[0], parts[1]
            normKey = self.normalizeCoupleNameKey(rawCoupleName)
            if normKey in nameToIdsMap:
                maleId, femaleId, _ = nameToIdsMap[normKey]
            else:
                maleId = f"20{abs(hash(maleName.lower())) % 900000 + 100000:06d}"
                femaleId = f"20{abs(hash(femaleName.lower())) % 900000 + 100000:06d}"

            coupleKey = buildCoupleKey(maleId, femaleId)
            clubName = pObj.get("cl", "Klub Taneczny")
            cityName = pObj.get("ci", "")
            startNo = int(pObj.get("sn", 0))
            finalPlace = int(pObj.get("finalRank") or len(participants))
            startNumToCoupleKey[startNo] = coupleKey

            cursor.execute("""
                INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                VALUES (?, ?, 'M', ?, ?, ?, ?)
            """, (maleId, maleName, clubName, danceClass, danceClass, tournamentDate))
            cursor.execute("""
                INSERT OR IGNORE INTO dancers (dancerId, fullName, gender, currentClub, currentClassSt, currentClassLa, updatedAt)
                VALUES (?, ?, 'F', ?, ?, ?, ?)
            """, (femaleId, femaleName, clubName, danceClass, danceClass, tournamentDate))

            cursor.execute("""
                INSERT OR IGNORE INTO couples (
                    coupleKey, maleDancerId, femaleDancerId, coupleName, clubName, cityName,
                    ageGroup, classSt, classLa, firstTournamentDate, lastTournamentDate, isActive
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                coupleKey, maleId, femaleId, f"{maleName} & {femaleName}",
                clubName, cityName, ageGroup, danceClass, danceClass,
                tournamentDate, tournamentDate
            ))

            pRounds = pObj.get("r") or {}
            maxRound = "F" if "F" in pRounds else ("1/2 F" if "r2" in pRounds else "1/4 F")
            pts, pod = calculateFtsPoints(finalPlace, len(participants), 1.2)
            cursor.execute("""
                INSERT INTO participations (
                    categoryId, tournamentId, coupleKey, maleDancerId, femaleDancerId,
                    startNumber, clubName, cityName, finalPlace, maxRoundReached,
                    pointsEarned, podiumCredit, totalPreliminaryMarks
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                fullCategoryId, tournamentId, coupleKey, maleId, femaleId,
                startNo, clubName, cityName, finalPlace, maxRound, pts, pod
            ))

        marksInserted = 0
        drObj = data.get("dr") or {}
        for roundKey, roundContent in drObj.items():
            if not isinstance(roundContent, dict):
                continue
            if "skating" in roundContent and isinstance(roundContent["skating"], dict):
                for pRow in roundContent["skating"].get("p", []):
                    sn = int(pRow.get("sn", 0))
                    cKey = startNumToCoupleKey.get(sn)
                    if not cKey:
                        continue
                    for dEntry in pRow.get("dr", []):
                        dCode = normalizeDanceCode(dEntry.get("d", "WA"))
                        rString = dEntry.get("r", "")
                        dancePlace = float(dEntry.get("p", 0))
                        for idx, ch in enumerate(rString):
                            if idx < len(indexToLetter) and ch.isdigit():
                                jLetter, jId = indexToLetter[idx]
                                cursor.execute("""
                                    INSERT INTO judgeMarks (
                                        categoryId, tournamentId, coupleKey, roundCode, danceCode,
                                        judgeId, judgeLetter, markType, markValue, danceCalculatedPlace
                                    ) VALUES (?, ?, ?, 'F', ?, ?, ?, 'SKATING', ?, ?)
                                """, (
                                    fullCategoryId, tournamentId, cKey, dCode,
                                    jId, jLetter, int(ch), dancePlace
                                ))
                                marksInserted += 1
            if "mks" in roundContent and isinstance(roundContent["mks"], dict):
                for pRow in roundContent["mks"].get("p", []):
                    sn = int(pRow.get("sn", 0))
                    cKey = startNumToCoupleKey.get(sn)
                    if not cKey:
                        continue
                    for dEntry in pRow.get("dm", []):
                        dCode = normalizeDanceCode(dEntry.get("d", "WA"))
                        mString = dEntry.get("m", "")
                        for idx, (jLetter, jId) in enumerate(indexToLetter):
                            recalled = 1 if (idx < len(mString) and mString[idx].strip() != "") else 0
                            cursor.execute("""
                                INSERT INTO judgeMarks (
                                    categoryId, tournamentId, coupleKey, roundCode, danceCode,
                                    judgeId, judgeLetter, markType, markValue, danceCalculatedPlace
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RECALL', ?, NULL)
                            """, (
                                fullCategoryId, tournamentId, cKey, roundKey.upper(), dCode,
                                jId, jLetter, recalled
                            ))
                            marksInserted += 1

        return (1, marksInserted)

    def parseAgeAndClass(self, categoryTitle: str) -> Tuple[str, str]:
        ageGroup = "Dorośli (Pow. 15)"
        for candidateAge in ["10-11", "12-13", "14-15", "16-18", "Młodzież", "Dorośli", "Pow. 15", "Senior"]:
            if candidateAge.lower() in categoryTitle.lower():
                ageGroup = "Pow. 15" if candidateAge in ["Dorośli", "Pow. 15", "16-18", "Młodzież"] else candidateAge
                break
        danceClass = "Open / S"
        classMatch = re.search(r"\b([A-G]|S)\b", categoryTitle)
        if classMatch:
            danceClass = classMatch.group(1)
        return (ageGroup, danceClass)
