import math
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from databaseManager import getDbConnection

CLASS_PROMOTION_THRESHOLDS = {
    "E": {"nextClass": "D", "requiredPoints": 100.0, "requiredPodiums": 3},
    "D": {"nextClass": "C", "requiredPoints": 150.0, "requiredPodiums": 5},
    "C": {"nextClass": "B", "requiredPoints": 200.0, "requiredPodiums": 5},
    "B": {"nextClass": "A", "requiredPoints": 250.0, "requiredPodiums": 5},
    "A": {"nextClass": "S", "requiredPoints": 300.0, "requiredPodiums": 6},
    "S": {"nextClass": "S (Master)", "requiredPoints": 500.0, "requiredPodiums": 10},
}

DANCE_NAMES_PL = {
    "WA": "Walc Angielski (WA)",
    "TG": "Tango (TG)",
    "VW": "Walc Wiedeński (VW)",
    "SF": "Slowfox (SF)",
    "QS": "Quickstep (QS)",
    "SA": "Samba (SA)",
    "CH": "Cha-Cha (CH)",
    "RB": "Rumba (RB)",
    "PD": "Paso Doble (PD)",
    "JV": "Jive (JV)"
}


def computeLinearRegressionSlope(values: List[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    meanX = (count - 1) / 2.0
    meanY = sum(values) / float(count)
    numerator = sum((idx - meanX) * (val - meanY) for idx, val in enumerate(values))
    denominator = sum((idx - meanX) ** 2 for idx in range(count))
    return round(numerator / denominator, 3) if denominator != 0 else 0.0


def getDashboardOverview() -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    totals = {
        "dancersCount": cursor.execute("SELECT COUNT(*) FROM dancers").fetchone()[0],
        "couplesCount": cursor.execute("SELECT COUNT(*) FROM couples").fetchone()[0],
        "activeCouplesCount": cursor.execute("SELECT COUNT(*) FROM couples WHERE isActive = 1").fetchone()[0],
        "tournamentsCount": cursor.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
        "categoriesCount": cursor.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
        "judgesCount": cursor.execute("SELECT COUNT(*) FROM judges").fetchone()[0],
        "participationsCount": cursor.execute("SELECT COUNT(*) FROM participations").fetchone()[0],
        "judgeMarksCount": cursor.execute("SELECT COUNT(*) FROM judgeMarks").fetchone()[0],
    }

    topCouplesRows = cursor.execute("""
        SELECT
            c.coupleKey,
            c.coupleName,
            c.maleDancerId,
            c.femaleDancerId,
            c.clubName,
            c.ageGroup,
            c.classSt,
            c.classLa,
            c.isActive,
            COUNT(p.participationId) AS startsCount,
            ROUND(AVG(p.finalPlace), 2) AS avgPlace,
            SUM(CASE WHEN p.finalPlace = 1 THEN 1 ELSE 0 END) AS winsCount,
            SUM(CASE WHEN p.finalPlace <= 3 THEN 1 ELSE 0 END) AS podiumsCount,
            ROUND(SUM(p.pointsEarned), 1) AS totalPoints
        FROM couples c
        JOIN participations p ON p.coupleKey = c.coupleKey
        GROUP BY c.coupleKey
        ORDER BY totalPoints DESC
        LIMIT 12
    """).fetchall()

    topClubsRows = cursor.execute("""
        SELECT
            p.clubName,
            COUNT(DISTINCT p.coupleKey) AS uniqueCouples,
            COUNT(p.participationId) AS totalStarts,
            SUM(CASE WHEN p.finalPlace = 1 THEN 1 ELSE 0 END) AS goldMedals,
            SUM(CASE WHEN p.finalPlace <= 3 THEN 1 ELSE 0 END) AS podiums,
            ROUND(SUM(p.pointsEarned), 1) AS totalClubPoints
        FROM participations p
        GROUP BY p.clubName
        ORDER BY totalClubPoints DESC
    """).fetchall()

    recentTournaments = cursor.execute("""
        SELECT
            t.*,
            COUNT(DISTINCT cat.categoryId) AS categoriesCount,
            COUNT(p.participationId) AS totalEntries
        FROM tournaments t
        LEFT JOIN categories cat ON cat.tournamentId = t.tournamentId
        LEFT JOIN participations p ON p.tournamentId = t.tournamentId
        GROUP BY t.tournamentId
        ORDER BY t.dateStart DESC
    """).fetchall()

    connection.close()
    return {
        "totals": totals,
        "topCouples": [dict(r) for r in topCouplesRows],
        "topClubs": [dict(r) for r in topClubsRows],
        "recentTournaments": [dict(r) for r in recentTournaments],
    }


def getDancerFullAnalytics(dancerId: str) -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    dancerRow = cursor.execute("SELECT * FROM dancers WHERE dancerId = ?", (dancerId,)).fetchone()
    if not dancerRow:
        connection.close()
        return {"error": f"Dancer {dancerId} not found"}

    dancerInfo = dict(dancerRow)

    # All unique couples formed by this dancer (each maleId_femaleId combination is a distinct couple!)
    couplesRows = cursor.execute("""
        SELECT
            c.*,
            dm.fullName AS maleFullName,
            df.fullName AS femaleFullName,
            COUNT(p.participationId) AS startsCount,
            ROUND(AVG(p.finalPlace), 2) AS avgPlace,
            MIN(p.finalPlace) AS bestPlace,
            SUM(CASE WHEN p.finalPlace = 1 THEN 1 ELSE 0 END) AS winsCount,
            SUM(CASE WHEN p.finalPlace <= 3 THEN 1 ELSE 0 END) AS podiumsCount,
            ROUND(SUM(p.pointsEarned), 1) AS totalPoints,
            SUM(p.podiumCredit) AS totalPodiumCredits
        FROM couples c
        JOIN dancers dm ON dm.dancerId = c.maleDancerId
        JOIN dancers df ON df.dancerId = c.femaleDancerId
        LEFT JOIN participations p ON p.coupleKey = c.coupleKey
        WHERE c.maleDancerId = ? OR c.femaleDancerId = ?
        GROUP BY c.coupleKey
        ORDER BY c.firstTournamentDate ASC
    """, (dancerId, dancerId)).fetchall()

    partnershipsHistory = []
    for cRow in couplesRows:
        cDict = dict(cRow)
        partnerId = cDict["femaleDancerId"] if cDict["maleDancerId"] == dancerId else cDict["maleDancerId"]
        partnerName = cDict["femaleFullName"] if cDict["maleDancerId"] == dancerId else cDict["maleFullName"]
        cDict["partnerDancerId"] = partnerId
        cDict["partnerFullName"] = partnerName
        partnershipsHistory.append(cDict)

    # Chronological timeline of all starts across ALL partnerships of this dancer
    allStartsRows = cursor.execute("""
        SELECT
            p.*,
            c.coupleName,
            c.maleDancerId,
            c.femaleDancerId,
            t.competitionName,
            t.cityName AS tournamentCity,
            t.dateStart,
            t.tournamentRank,
            cat.categoryName,
            cat.style,
            cat.danceClass,
            cat.ageGroup,
            cat.competitorsCount
        FROM participations p
        JOIN couples c ON c.coupleKey = p.coupleKey
        JOIN tournaments t ON t.tournamentId = p.tournamentId
        JOIN categories cat ON cat.categoryId = p.categoryId
        WHERE p.maleDancerId = ? OR p.femaleDancerId = ?
        ORDER BY t.dateStart ASC, cat.style ASC
    """, (dancerId, dancerId)).fetchall()

    timelineStarts = []
    percentileValues = []
    for sRow in allStartsRows:
        sDict = dict(sRow)
        compCount = max(1, int(sDict["competitorsCount"] or 1))
        place = int(sDict["finalPlace"])
        percentile = round(((compCount - place) / float(max(1, compCount - 1))) * 100.0, 1) if compCount > 1 else 100.0
        sDict["fieldPercentile"] = percentile
        percentileValues.append(percentile)
        timelineStarts.append(sDict)

    slope = computeLinearRegressionSlope(percentileValues[-8:]) if percentileValues else 0.0
    trendStatus = "Silny trend wzrostowy (Rosnąca forma)" if slope > 1.2 else (
        "Stabilna wysoka forma" if slope >= -1.0 else "Trend spadkowy (Wahania formy)"
    )

    connection.close()
    return {
        "dancer": dancerInfo,
        "partnershipsCount": len(partnershipsHistory),
        "partnershipsHistory": partnershipsHistory,
        "careerStartsCount": len(timelineStarts),
        "careerTrendSlope": slope,
        "careerTrendStatus": trendStatus,
        "timelineStarts": timelineStarts,
    }


def getCoupleDeepAnalytics(coupleKey: str) -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    coupleRow = cursor.execute("""
        SELECT
            c.*,
            dm.fullName AS maleFullName,
            dm.wdsfMin AS maleWdsfMin,
            df.fullName AS femaleFullName,
            df.wdsfMin AS femaleWdsfMin
        FROM couples c
        JOIN dancers dm ON dm.dancerId = c.maleDancerId
        JOIN dancers df ON df.dancerId = c.femaleDancerId
        WHERE c.coupleKey = ?
    """, (coupleKey,)).fetchone()

    if not coupleRow:
        connection.close()
        return {"error": f"Couple {coupleKey} not found"}

    coupleInfo = dict(coupleRow)

    # Also fetch other partnerships of both male and female dancer to show full context
    otherCouplesRows = cursor.execute("""
        SELECT
            c.coupleKey,
            c.coupleName,
            c.maleDancerId,
            c.femaleDancerId,
            c.clubName,
            c.firstTournamentDate,
            c.lastTournamentDate,
            c.isActive,
            COUNT(p.participationId) AS startsCount,
            ROUND(AVG(p.finalPlace), 2) AS avgPlace,
            ROUND(SUM(p.pointsEarned), 1) AS totalPoints
        FROM couples c
        LEFT JOIN participations p ON p.coupleKey = c.coupleKey
        WHERE (c.maleDancerId IN (?, ?) OR c.femaleDancerId IN (?, ?))
        GROUP BY c.coupleKey
        ORDER BY c.firstTournamentDate ASC
    """, (
        coupleInfo["maleDancerId"], coupleInfo["femaleDancerId"],
        coupleInfo["maleDancerId"], coupleInfo["femaleDancerId"]
    )).fetchall()

    startsRows = cursor.execute("""
        SELECT
            p.*,
            t.competitionName,
            t.cityName AS tournamentCity,
            t.voivodeship,
            t.dateStart,
            t.tournamentRank,
            t.sourceSystem,
            cat.categoryName,
            cat.style,
            cat.danceClass,
            cat.ageGroup,
            cat.competitorsCount
        FROM participations p
        JOIN tournaments t ON t.tournamentId = p.tournamentId
        JOIN categories cat ON cat.categoryId = p.categoryId
        WHERE p.coupleKey = ?
        ORDER BY t.dateStart ASC, cat.style ASC
    """, (coupleKey,)).fetchall()

    startsList = []
    placesSeries = []
    percentilesSeries = []
    cumulativePointsSt = 0.0
    cumulativePointsLa = 0.0
    podiumCreditsSt = 0
    podiumCreditsLa = 0

    for sRow in startsRows:
        sDict = dict(sRow)
        compCount = max(1, int(sDict["competitorsCount"] or 1))
        place = int(sDict["finalPlace"])
        percentile = round(((compCount - place) / float(max(1, compCount - 1))) * 100.0, 1) if compCount > 1 else 100.0
        sDict["fieldPercentile"] = percentile

        if sDict["style"] == "Standard":
            cumulativePointsSt = round(cumulativePointsSt + float(sDict["pointsEarned"]), 1)
            podiumCreditsSt += int(sDict["podiumCredit"])
        elif sDict["style"] == "Latin":
            cumulativePointsLa = round(cumulativePointsLa + float(sDict["pointsEarned"]), 1)
            podiumCreditsLa += int(sDict["podiumCredit"])
        else:
            halfPts = round(float(sDict["pointsEarned"]) / 2.0, 1)
            cumulativePointsSt = round(cumulativePointsSt + halfPts, 1)
            cumulativePointsLa = round(cumulativePointsLa + halfPts, 1)
            podiumCreditsSt += int(sDict["podiumCredit"])
            podiumCreditsLa += int(sDict["podiumCredit"])

        sDict["cumulativePointsSt"] = cumulativePointsSt
        sDict["cumulativePointsLa"] = cumulativePointsLa
        placesSeries.append(float(place))
        percentilesSeries.append(percentile)
        startsList.append(sDict)

    # Per-Dance Skating & Recall Analytics (Which dance is their strongest / weakest?)
    danceStatsRows = cursor.execute("""
        SELECT
            jm.danceCode,
            COUNT(CASE WHEN jm.markType = 'SKATING' THEN 1 END) AS skatingMarksCount,
            ROUND(AVG(CASE WHEN jm.markType = 'SKATING' THEN jm.markValue END), 2) AS avgJudgeMark,
            ROUND(AVG(CASE WHEN jm.markType = 'SKATING' THEN jm.danceCalculatedPlace END), 2) AS avgDancePlace,
            SUM(CASE WHEN jm.markType = 'SKATING' AND jm.markValue = 1 THEN 1 ELSE 0 END) AS firstPlaceMarksCount,
            ROUND(
                100.0 * AVG(CASE WHEN jm.markType = 'RECALL' THEN jm.markValue END),
                1
            ) AS prelimRecallPercent
        FROM judgeMarks jm
        WHERE jm.coupleKey = ?
        GROUP BY jm.danceCode
        ORDER BY avgJudgeMark ASC
    """, (coupleKey,)).fetchall()

    perDanceBreakdown = []
    for dRow in danceStatsRows:
        dDict = dict(dRow)
        code = dDict["danceCode"]
        dDict["danceFullName"] = DANCE_NAMES_PL.get(code, code)
        perDanceBreakdown.append(dDict)

    # Judge-by-Judge Scoring & Bias Matrix for this couple
    # Compares each judge's mark for this couple in each dance against the panel average in that same category+dance!
    judgeMatrixRows = cursor.execute("""
        WITH PanelConsensus AS (
            SELECT
                categoryId,
                danceCode,
                AVG(markValue) AS panelAvgMark
            FROM judgeMarks
            WHERE coupleKey = ? AND markType = 'SKATING'
            GROUP BY categoryId, danceCode
        )
        SELECT
            j.judgeId,
            j.fullName AS judgeName,
            j.cityName AS judgeCity,
            j.regionTag,
            j.wdsfLicense,
            COUNT(jm.markId) AS totalSkatingMarksGiven,
            ROUND(AVG(jm.markValue), 2) AS avgMarkGiven,
            ROUND(AVG(pc.panelAvgMark), 2) AS avgPanelConsensus,
            ROUND(AVG(jm.markValue - pc.panelAvgMark), 2) AS biasDelta,
            SUM(CASE WHEN jm.markValue = 1 THEN 1 ELSE 0 END) AS firstPlacesGiven,
            SUM(CASE WHEN jm.markValue <= 3 THEN 1 ELSE 0 END) AS top3MarksGiven
        FROM judgeMarks jm
        JOIN judges j ON j.judgeId = jm.judgeId
        JOIN PanelConsensus pc ON pc.categoryId = jm.categoryId AND pc.danceCode = jm.danceCode
        WHERE jm.coupleKey = ? AND jm.markType = 'SKATING'
        GROUP BY j.judgeId
        ORDER BY biasDelta ASC
    """, (coupleKey, coupleKey)).fetchall()

    judgeEvaluations = []
    for jRow in judgeMatrixRows:
        jDict = dict(jRow)
        delta = float(jDict["biasDelta"] or 0.0)
        # Negative delta means judge gives BETTER (lower number, e.g. 1st instead of 2nd) place than panel consensus
        if delta <= -0.35:
            jDict["interpretation"] = "Bardzo przychylny (Ocenia wyżej od panelu)"
        elif delta >= 0.35:
            jDict["interpretation"] = "Surowy (Ocenia niżej od panelu)"
        else:
            jDict["interpretation"] = "Zgodny z konsensusem panelu"
        judgeEvaluations.append(jDict)

    # Class Promotion Tracker (Ścieżka awansu klasowego FTS/PZST)
    classSt = coupleInfo.get("classSt", "C")
    classLa = coupleInfo.get("classLa", "C")
    promoStSpec = CLASS_PROMOTION_THRESHOLDS.get(classSt, CLASS_PROMOTION_THRESHOLDS["A"])
    promoLaSpec = CLASS_PROMOTION_THRESHOLDS.get(classLa, CLASS_PROMOTION_THRESHOLDS["A"])

    promotionProgress = {
        "Standard": {
            "currentClass": classSt,
            "targetClass": promoStSpec["nextClass"],
            "earnedPoints": cumulativePointsSt,
            "requiredPoints": promoStSpec["requiredPoints"],
            "pointsCompletionPct": min(100.0, round((cumulativePointsSt / promoStSpec["requiredPoints"]) * 100.0, 1)),
            "earnedPodiums": podiumCreditsSt,
            "requiredPodiums": promoStSpec["requiredPodiums"],
            "podiumsCompletionPct": min(100.0, round((podiumCreditsSt / float(promoStSpec["requiredPodiums"])) * 100.0, 1)),
        },
        "Latin": {
            "currentClass": classLa,
            "targetClass": promoLaSpec["nextClass"],
            "earnedPoints": cumulativePointsLa,
            "requiredPoints": promoLaSpec["requiredPoints"],
            "pointsCompletionPct": min(100.0, round((cumulativePointsLa / promoLaSpec["requiredPoints"]) * 100.0, 1)),
            "earnedPodiums": podiumCreditsLa,
            "requiredPodiums": promoLaSpec["requiredPodiums"],
            "podiumsCompletionPct": min(100.0, round((podiumCreditsLa / float(promoLaSpec["requiredPodiums"])) * 100.0, 1)),
        }
    }

    # Form & Trend Metrics
    percentileSlope = computeLinearRegressionSlope(percentilesSeries[-6:])
    placeSlope = computeLinearRegressionSlope(placesSeries[-6:])
    avgPlace = round(sum(placesSeries) / len(placesSeries), 2) if placesSeries else 0.0
    stdDevPlace = round(
        math.sqrt(sum((x - avgPlace) ** 2 for x in placesSeries) / len(placesSeries)), 2
    ) if len(placesSeries) > 1 else 0.0

    if percentileSlope > 1.0:
        trendSummary = "Forma wzrostowa (Coraz wyższe miejsca i percentyl pokonanych par)"
    elif percentileSlope < -1.0:
        trendSummary = "Zniżka formy (Spadek średniego percentyla w ostatnich startach)"
    else:
        trendSummary = "Stabilna dyspozycja turniejowa (Wysoka powtarzalność wyników)"

    connection.close()
    return {
        "couple": coupleInfo,
        "relatedPartnerships": [dict(r) for r in otherCouplesRows],
        "summaryMetrics": {
            "startsCount": len(startsList),
            "avgPlace": avgPlace,
            "bestPlace": int(min(placesSeries)) if placesSeries else 0,
            "winsCount": sum(1 for x in placesSeries if x == 1),
            "podiumsCount": sum(1 for x in placesSeries if x <= 3),
            "finalsCount": sum(1 for x in placesSeries if x <= 6),
            "stabilityIndexStdDev": stdDevPlace,
            "percentileTrendSlope": percentileSlope,
            "placeTrendSlope": placeSlope,
            "trendSummary": trendSummary,
        },
        "promotionProgress": promotionProgress,
        "startsHistory": startsList,
        "perDanceBreakdown": perDanceBreakdown,
        "judgeEvaluations": judgeEvaluations,
    }


def getHeadToHeadComparison(coupleKeyA: str, coupleKeyB: str) -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    analyticsA = getCoupleDeepAnalytics(coupleKeyA)
    analyticsB = getCoupleDeepAnalytics(coupleKeyB)
    if "error" in analyticsA or "error" in analyticsB:
        connection.close()
        return {"error": "One or both couples not found"}

    # Direct encounters in the exact same tournament category
    directRows = cursor.execute("""
        SELECT
            t.tournamentId,
            t.competitionName,
            t.cityName,
            t.dateStart,
            t.tournamentRank,
            cat.categoryId,
            cat.categoryName,
            cat.style,
            cat.competitorsCount,
            pa.finalPlace AS placeA,
            pa.pointsEarned AS pointsA,
            pb.finalPlace AS placeB,
            pb.pointsEarned AS pointsB
        FROM participations pa
        JOIN participations pb ON pb.categoryId = pa.categoryId
        JOIN tournaments t ON t.tournamentId = pa.tournamentId
        JOIN categories cat ON cat.categoryId = pa.categoryId
        WHERE pa.coupleKey = ? AND pb.coupleKey = ?
        ORDER BY t.dateStart DESC
    """, (coupleKeyA, coupleKeyB)).fetchall()

    directEncounters = [dict(r) for r in directRows]
    winsA = sum(1 for r in directEncounters if int(r["placeA"]) < int(r["placeB"]))
    winsB = sum(1 for r in directEncounters if int(r["placeB"]) < int(r["placeA"]))

    # Per-judge direct preference when both couples were in the same final
    sharedJudgeRows = cursor.execute("""
        SELECT
            j.judgeId,
            j.fullName AS judgeName,
            j.cityName AS judgeCity,
            COUNT(*) AS sharedDancesJudged,
            ROUND(AVG(jma.markValue), 2) AS avgMarkA,
            ROUND(AVG(jmb.markValue), 2) AS avgMarkB,
            SUM(CASE WHEN jma.markValue < jmb.markValue THEN 1 ELSE 0 END) AS judgePreferredACount,
            SUM(CASE WHEN jmb.markValue < jma.markValue THEN 1 ELSE 0 END) AS judgePreferredBCount
        FROM judgeMarks jma
        JOIN judgeMarks jmb
            ON jmb.categoryId = jma.categoryId
            AND jmb.roundCode = jma.roundCode
            AND jmb.danceCode = jma.danceCode
            AND jmb.judgeId = jma.judgeId
        JOIN judges j ON j.judgeId = jma.judgeId
        WHERE jma.coupleKey = ?
          AND jmb.coupleKey = ?
          AND jma.markType = 'SKATING'
          AND jmb.markType = 'SKATING'
        GROUP BY j.judgeId
        ORDER BY judgePreferredACount DESC
    """, (coupleKeyA, coupleKeyB)).fetchall()

    connection.close()
    return {
        "coupleA": analyticsA["couple"],
        "coupleB": analyticsB["couple"],
        "metricsA": analyticsA["summaryMetrics"],
        "metricsB": analyticsB["summaryMetrics"],
        "perDanceA": analyticsA["perDanceBreakdown"],
        "perDanceB": analyticsB["perDanceBreakdown"],
        "directEncountersCount": len(directEncounters),
        "directWinsA": winsA,
        "directWinsB": winsB,
        "directEncounters": directEncounters,
        "sharedJudgePreferences": [dict(r) for r in sharedJudgeRows],
    }


def searchTournamentsCatalog(
    searchQuery: str = "",
    voivodeship: str = "",
    danceClass: str = "",
    ageGroup: str = "",
    style: str = "",
    tournamentRank: str = ""
) -> List[Dict[str, Any]]:
    connection = getDbConnection()
    cursor = connection.cursor()

    sql = """
        SELECT DISTINCT
            t.*,
            (SELECT COUNT(*) FROM categories c2 WHERE c2.tournamentId = t.tournamentId) AS categoriesCount,
            (SELECT COUNT(*) FROM participations p2 WHERE p2.tournamentId = t.tournamentId) AS entriesCount
        FROM tournaments t
        LEFT JOIN categories cat ON cat.tournamentId = t.tournamentId
        WHERE 1=1
    """
    params: List[Any] = []

    if searchQuery:
        sql += " AND (LOWER(t.competitionName) LIKE ? OR LOWER(t.cityName) LIKE ?)"
        likeQ = f"%{searchQuery.lower()}%"
        params.extend([likeQ, likeQ])
    if voivodeship:
        sql += " AND LOWER(t.voivodeship) = ?"
        params.append(voivodeship.lower())
    if tournamentRank:
        sql += " AND LOWER(t.tournamentRank) LIKE ?"
        params.append(f"%{tournamentRank.lower()}%")
    if danceClass:
        sql += " AND LOWER(cat.danceClass) = ?"
        params.append(danceClass.lower())
    if ageGroup:
        sql += " AND LOWER(cat.ageGroup) LIKE ?"
        params.append(f"%{ageGroup.lower()}%")
    if style:
        sql += " AND LOWER(cat.style) = ?"
        params.append(style.lower())

    sql += " ORDER BY t.dateStart DESC"
    rows = cursor.execute(sql, params).fetchall()
    result = []

    for r in rows:
        tDict = dict(r)
        cats = cursor.execute("""
            SELECT categoryId, categoryName, ageGroup, danceClass, style, dancesList, competitorsCount, roundsCount, blockName
            FROM categories
            WHERE tournamentId = ?
            ORDER BY categoryName ASC
        """, (tDict["tournamentId"],)).fetchall()
        tDict["categories"] = [dict(c) for c in cats]
        result.append(tDict)

    connection.close()
    return result


def getCategorySkatingAndResults(categoryId: str) -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    catRow = cursor.execute("""
        SELECT cat.*, t.competitionName, t.cityName, t.dateStart, t.tournamentRank, t.sourceSystem, t.liveResultsUrl
        FROM categories cat
        JOIN tournaments t ON t.tournamentId = cat.tournamentId
        WHERE cat.categoryId = ?
    """, (categoryId,)).fetchone()

    if not catRow:
        connection.close()
        return {"error": f"Category {categoryId} not found"}

    catInfo = dict(catRow)

    judgesRows = cursor.execute("""
        SELECT cj.judgeLetter, cj.judgeRole, j.*
        FROM categoryJudges cj
        JOIN judges j ON j.judgeId = cj.judgeId
        WHERE cj.categoryId = ?
        ORDER BY cj.judgeLetter ASC
    """, (categoryId,)).fetchall()
    panelJudges = [dict(j) for j in judgesRows]

    participationsRows = cursor.execute("""
        SELECT
            p.*,
            c.coupleName,
            dm.fullName AS maleFullName,
            df.fullName AS femaleFullName
        FROM participations p
        JOIN couples c ON c.coupleKey = p.coupleKey
        JOIN dancers dm ON dm.dancerId = p.maleDancerId
        JOIN dancers df ON df.dancerId = p.femaleDancerId
        WHERE p.categoryId = ?
        ORDER BY p.finalPlace ASC
    """, (categoryId,)).fetchall()

    resultsList = [dict(p) for p in participationsRows]

    marksRows = cursor.execute("""
        SELECT *
        FROM judgeMarks
        WHERE categoryId = ?
        ORDER BY roundCode ASC, danceCode ASC, coupleKey ASC, judgeLetter ASC
    """, (categoryId,)).fetchall()

    # Group marks into structured Skating Final Grid and Preliminary Recall Grid
    dancesList = [d.strip() for d in (catInfo.get("dancesList") or "").split(",") if d.strip()]
    skatingByDance: Dict[str, List[Dict[str, Any]]] = {d: [] for d in dancesList}
    prelimByCouple: Dict[str, Dict[str, Any]] = {}

    marksByCoupleDance: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for mRow in marksRows:
        m = dict(mRow)
        key = (m["roundCode"], m["coupleKey"], m["danceCode"])
        if key not in marksByCoupleDance:
            marksByCoupleDance[key] = {
                "marksByLetter": {},
                "danceCalculatedPlace": m["danceCalculatedPlace"]
            }
        marksByCoupleDance[key]["marksByLetter"][m["judgeLetter"]] = m["markValue"]

    for pObj in resultsList:
        cKey = pObj["coupleKey"]
        # Build final skating rows per dance for finalists
        if int(pObj["finalPlace"]) <= 6:
            for dCode in dancesList:
                entry = marksByCoupleDance.get(("F", cKey, dCode))
                if entry:
                    marksMap = entry["marksByLetter"]
                    marksVals = [int(v) for v in marksMap.values()]
                    # Compute Skating 1, 1-2, 1-3, 1-4, 1-5, 1-6 majority counts
                    majorityCounts = {
                        f"1-{k}" if k > 1 else "1": sum(1 for v in marksVals if v <= k)
                        for k in range(1, 7)
                    }
                    skatingByDance.setdefault(dCode, []).append({
                        "coupleKey": cKey,
                        "coupleName": pObj["coupleName"],
                        "maleDancerId": pObj["maleDancerId"],
                        "femaleDancerId": pObj["femaleDancerId"],
                        "startNumber": pObj["startNumber"],
                        "finalPlace": pObj["finalPlace"],
                        "danceCalculatedPlace": entry["danceCalculatedPlace"] or pObj["finalPlace"],
                        "marksByLetter": marksMap,
                        "majorityCounts": majorityCounts
                    })

        # Build semifinal recall summary
        prelimDances = {}
        totalRecalls = 0
        for dCode in dancesList:
            entry = marksByCoupleDance.get(("1/2 F", cKey, dCode)) or marksByCoupleDance.get(("R1", cKey, dCode))
            if entry:
                recSum = sum(int(v) for v in entry["marksByLetter"].values())
                totalRecalls += recSum
                prelimDances[dCode] = {
                    "recallSum": recSum,
                    "marksByLetter": entry["marksByLetter"]
                }
        prelimByCouple[cKey] = {
            "coupleKey": cKey,
            "coupleName": pObj["coupleName"],
            "startNumber": pObj["startNumber"],
            "finalPlace": pObj["finalPlace"],
            "totalRecalls": totalRecalls,
            "dances": prelimDances
        }

    connection.close()
    return {
        "category": catInfo,
        "panelJudges": panelJudges,
        "results": resultsList,
        "skatingByDance": skatingByDance,
        "prelimRecalls": list(prelimByCouple.values()),
    }


def getJudgesGlobalAnalytics(selectedJudgeId: Optional[str] = None) -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    judgesRows = cursor.execute("""
        SELECT
            j.*,
            COUNT(DISTINCT cj.categoryId) AS categoriesJudgedCount,
            COUNT(DISTINCT cat.tournamentId) AS tournamentsJudgedCount
        FROM judges j
        LEFT JOIN categoryJudges cj ON cj.judgeId = j.judgeId
        LEFT JOIN categories cat ON cat.categoryId = cj.categoryId
        GROUP BY j.judgeId
        ORDER BY categoriesJudgedCount DESC, j.fullName ASC
    """).fetchall()

    judgesList = [dict(r) for r in judgesRows]
    targetJudgeId = selectedJudgeId or (judgesList[0]["judgeId"] if judgesList else None)

    judgeDetail = None
    if targetJudgeId:
        jRow = cursor.execute("SELECT * FROM judges WHERE judgeId = ?", (targetJudgeId,)).fetchone()
        if jRow:
            # Compute how this judge evaluates every couple compared to the panel consensus
            coupleEvaluations = cursor.execute("""
                WITH PanelConsensus AS (
                    SELECT
                        categoryId,
                        coupleKey,
                        danceCode,
                        AVG(markValue) AS panelAvg
                    FROM judgeMarks
                    WHERE markType = 'SKATING'
                    GROUP BY categoryId, coupleKey, danceCode
                )
                SELECT
                    c.coupleKey,
                    c.coupleName,
                    c.maleDancerId,
                    c.femaleDancerId,
                    c.clubName,
                    c.ageGroup,
                    c.classSt,
                    COUNT(jm.markId) AS dancesJudged,
                    ROUND(AVG(jm.markValue), 2) AS judgeAvgMark,
                    ROUND(AVG(pc.panelAvg), 2) AS panelConsensusMark,
                    ROUND(AVG(jm.markValue - pc.panelAvg), 2) AS deltaVsPanel,
                    SUM(CASE WHEN jm.markValue = 1 THEN 1 ELSE 0 END) AS firstPlaceMarks,
                    ROUND(100.0 * AVG(CASE WHEN ABS(jm.markValue - pc.panelAvg) <= 0.75 THEN 1.0 ELSE 0.0 END), 1) AS consensusAgreementPct
                FROM judgeMarks jm
                JOIN couples c ON c.coupleKey = jm.coupleKey
                JOIN PanelConsensus pc
                    ON pc.categoryId = jm.categoryId
                    AND pc.coupleKey = jm.coupleKey
                    AND pc.danceCode = jm.danceCode
                WHERE jm.judgeId = ? AND jm.markType = 'SKATING'
                GROUP BY c.coupleKey
                ORDER BY deltaVsPanel ASC
            """, (targetJudgeId,)).fetchall()

            # Compute club affinity for this judge
            clubAffinity = cursor.execute("""
                WITH PanelConsensus AS (
                    SELECT
                        categoryId,
                        coupleKey,
                        danceCode,
                        AVG(markValue) AS panelAvg
                    FROM judgeMarks
                    WHERE markType = 'SKATING'
                    GROUP BY categoryId, coupleKey, danceCode
                )
                SELECT
                    c.clubName,
                    COUNT(jm.markId) AS marksCount,
                    ROUND(AVG(jm.markValue), 2) AS judgeAvgPlace,
                    ROUND(AVG(pc.panelAvg), 2) AS panelAvgPlace,
                    ROUND(AVG(jm.markValue - pc.panelAvg), 2) AS clubBiasDelta
                FROM judgeMarks jm
                JOIN couples c ON c.coupleKey = jm.coupleKey
                JOIN PanelConsensus pc
                    ON pc.categoryId = jm.categoryId
                    AND pc.coupleKey = jm.coupleKey
                    AND pc.danceCode = jm.danceCode
                WHERE jm.judgeId = ? AND jm.markType = 'SKATING'
                GROUP BY c.clubName
                ORDER BY clubBiasDelta ASC
            """, (targetJudgeId,)).fetchall()

            tournamentsWorked = cursor.execute("""
                SELECT DISTINCT
                    t.tournamentId,
                    t.competitionName,
                    t.cityName,
                    t.dateStart,
                    t.tournamentRank,
                    cj.judgeLetter,
                    cj.judgeRole,
                    cat.categoryId,
                    cat.categoryName,
                    cat.style
                FROM categoryJudges cj
                JOIN categories cat ON cat.categoryId = cj.categoryId
                JOIN tournaments t ON t.tournamentId = cat.tournamentId
                WHERE cj.judgeId = ?
                ORDER BY t.dateStart DESC
            """, (targetJudgeId,)).fetchall()

            evalList = [dict(r) for r in coupleEvaluations]
            overallConsensus = round(
                sum(float(x["consensusAgreementPct"]) for x in evalList) / len(evalList), 1
            ) if evalList else 100.0

            judgeDetail = {
                "judge": dict(jRow),
                "overallConsensusAgreementPct": overallConsensus,
                "coupleEvaluations": evalList,
                "clubAffinity": [dict(r) for r in clubAffinity],
                "tournamentsWorked": [dict(r) for r in tournamentsWorked],
            }

    connection.close()
    return {
        "judges": judgesList,
        "selectedJudgeDetail": judgeDetail,
    }


def getCoupleJudgeHistoryTimeline(coupleKey: str, judgeId: str) -> Dict[str, Any]:
    connection = getDbConnection()
    cursor = connection.cursor()

    coupleRow = cursor.execute("SELECT * FROM couples WHERE coupleKey = ?", (coupleKey,)).fetchone()
    judgeRow = cursor.execute("SELECT * FROM judges WHERE judgeId = ?", (judgeId,)).fetchone()
    if not coupleRow or not judgeRow:
        connection.close()
        return {"error": "Couple or Judge not found"}

    # Fetch chronological tournament-by-tournament evaluation of this couple by this judge
    # Comparing the judge's average final mark and recall rate against the panel average in each tournament+category!
    tournamentHistoryRows = cursor.execute("""
        WITH PanelCategoryAvg AS (
            SELECT
                categoryId,
                ROUND(AVG(CASE WHEN markType = 'SKATING' THEN markValue END), 2) AS panelSkatingAvg,
                ROUND(100.0 * AVG(CASE WHEN markType = 'RECALL' THEN markValue END), 1) AS panelRecallPct
            FROM judgeMarks
            WHERE coupleKey = ?
            GROUP BY categoryId
        )
        SELECT
            t.tournamentId,
            t.competitionName,
            t.cityName,
            t.dateStart,
            t.tournamentRank,
            cat.categoryId,
            cat.categoryName,
            cat.style,
            cat.competitorsCount,
            p.finalPlace,
            cj.judgeLetter,
            ROUND(AVG(CASE WHEN jm.markType = 'SKATING' THEN jm.markValue END), 2) AS judgeSkatingAvg,
            pca.panelSkatingAvg,
            ROUND(
                AVG(CASE WHEN jm.markType = 'SKATING' THEN jm.markValue END) - pca.panelSkatingAvg,
                2
            ) AS skatingBiasDelta,
            ROUND(100.0 * AVG(CASE WHEN jm.markType = 'RECALL' THEN jm.markValue END), 1) AS judgeRecallPct,
            pca.panelRecallPct
        FROM judgeMarks jm
        JOIN categories cat ON cat.categoryId = jm.categoryId
        JOIN tournaments t ON t.tournamentId = jm.tournamentId
        JOIN participations p ON p.categoryId = jm.categoryId AND p.coupleKey = jm.coupleKey
        JOIN PanelCategoryAvg pca ON pca.categoryId = jm.categoryId
        LEFT JOIN categoryJudges cj ON cj.categoryId = jm.categoryId AND cj.judgeId = jm.judgeId
        WHERE jm.coupleKey = ? AND jm.judgeId = ?
        GROUP BY cat.categoryId
        ORDER BY t.dateStart ASC, cat.style ASC
    """, (coupleKey, coupleKey, judgeId)).fetchall()

    timelineEvents = []
    for r in tournamentHistoryRows:
        ev = dict(r)
        # Fetch per-dance marks given by this judge in this specific category
        danceMarksRows = cursor.execute("""
            WITH DancePanelAvg AS (
                SELECT
                    danceCode,
                    ROUND(AVG(markValue), 2) AS panelDanceAvg
                FROM judgeMarks
                WHERE categoryId = ? AND coupleKey = ? AND markType = 'SKATING'
                GROUP BY danceCode
            )
            SELECT
                jm.danceCode,
                jm.roundCode,
                jm.markType,
                jm.markValue,
                jm.danceCalculatedPlace,
                dpa.panelDanceAvg
            FROM judgeMarks jm
            LEFT JOIN DancePanelAvg dpa ON dpa.danceCode = jm.danceCode
            WHERE jm.categoryId = ? AND jm.coupleKey = ? AND jm.judgeId = ?
            ORDER BY jm.markType DESC, jm.danceCode ASC
        """, (ev["categoryId"], coupleKey, ev["categoryId"], coupleKey, judgeId)).fetchall()
        ev["danceMarks"] = [dict(dm) for dm in danceMarksRows]
        timelineEvents.append(ev)

    # Also compute per-dance historical average from this judge for this couple
    perDanceJudgeRows = cursor.execute("""
        WITH DancePanelOverall AS (
            SELECT
                danceCode,
                ROUND(AVG(markValue), 2) AS panelOverallDanceAvg
            FROM judgeMarks
            WHERE coupleKey = ? AND markType = 'SKATING'
            GROUP BY danceCode
        )
        SELECT
            jm.danceCode,
            COUNT(CASE WHEN jm.markType = 'SKATING' THEN 1 END) AS finalsCount,
            ROUND(AVG(CASE WHEN jm.markType = 'SKATING' THEN jm.markValue END), 2) AS judgeDanceAvg,
            dpo.panelOverallDanceAvg,
            ROUND(100.0 * AVG(CASE WHEN jm.markType = 'RECALL' THEN jm.markValue END), 1) AS judgeRecallPct
        FROM judgeMarks jm
        LEFT JOIN DancePanelOverall dpo ON dpo.danceCode = jm.danceCode
        WHERE jm.coupleKey = ? AND jm.judgeId = ?
        GROUP BY jm.danceCode
        ORDER BY jm.danceCode ASC
    """, (coupleKey, coupleKey, judgeId)).fetchall()

    connection.close()
    return {
        "couple": dict(coupleRow),
        "judge": dict(judgeRow),
        "timelineEvents": timelineEvents,
        "perDanceSummary": [dict(r) for r in perDanceJudgeRows],
    }

