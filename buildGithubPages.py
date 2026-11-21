import os
import json
import shutil
from databaseManager import getDbConnection, initializeDatabase
from seedRealisticFtsData import seedComprehensiveFtsDatabase
from analyticsEngine import (
    getDashboardOverview,
    getDancerFullAnalytics,
    getCoupleDeepAnalytics,
    searchTournamentsCatalog,
    getCategorySkatingAndResults,
    getJudgesGlobalAnalytics,
    getCoupleJudgeHistoryTimeline
)


def buildStaticGithubPagesBundle() -> None:
    initializeDatabase()
    seedComprehensiveFtsDatabase(forceReseed=False)

    connection = getDbConnection()
    cursor = connection.cursor()

    dancers = [dict(r) for r in cursor.execute("""
        SELECT
            d.*,
            (SELECT COUNT(*) FROM couples c WHERE c.maleDancerId = d.dancerId OR c.femaleDancerId = d.dancerId) AS partnershipsCount,
            (SELECT COUNT(*) FROM participations p WHERE p.maleDancerId = d.dancerId OR p.femaleDancerId = d.dancerId) AS totalStarts
        FROM dancers d
        ORDER BY d.fullName ASC
    """).fetchall()]

    couples = [dict(r) for r in cursor.execute("""
        SELECT
            c.*,
            dm.fullName AS maleFullName,
            df.fullName AS femaleFullName,
            COUNT(p.participationId) AS startsCount,
            ROUND(AVG(p.finalPlace), 2) AS avgPlace,
            ROUND(SUM(p.pointsEarned), 1) AS totalPoints
        FROM couples c
        JOIN dancers dm ON dm.dancerId = c.maleDancerId
        JOIN dancers df ON df.dancerId = c.femaleDancerId
        LEFT JOIN participations p ON p.coupleKey = c.coupleKey
        GROUP BY c.coupleKey
        ORDER BY c.isActive DESC, totalPoints DESC
    """).fetchall()]

    categoriesIds = [
        r["categoryId"]
        for r in cursor.execute("SELECT DISTINCT categoryId FROM participations").fetchall()
    ]
    judgesIds = [r["judgeId"] for r in cursor.execute("SELECT judgeId FROM judges").fetchall()]

    judgesCatalog = [dict(r) for r in cursor.execute("""
        SELECT
            j.*,
            COUNT(DISTINCT cj.categoryId) AS categoriesJudgedCount,
            COUNT(DISTINCT cat.tournamentId) AS tournamentsJudgedCount
        FROM judges j
        LEFT JOIN categoryJudges cj ON cj.judgeId = j.judgeId
        LEFT JOIN categories cat ON cat.categoryId = cj.categoryId
        GROUP BY j.judgeId
        ORDER BY categoriesJudgedCount DESC, j.fullName ASC
    """).fetchall()]

    tournamentsCatalog = [dict(r) for r in cursor.execute("""
        SELECT
            t.*,
            (SELECT COUNT(*) FROM categories c2 WHERE c2.tournamentId = t.tournamentId) AS categoriesCount,
            (SELECT COUNT(*) FROM participations p2 WHERE p2.tournamentId = t.tournamentId) AS entriesCount
        FROM tournaments t
        ORDER BY t.dateStart DESC
    """).fetchall()]

    allParticipations = [dict(r) for r in cursor.execute("""
        SELECT
            p.categoryId,
            p.tournamentId,
            p.coupleKey,
            p.finalPlace,
            p.pointsEarned,
            t.competitionName,
            t.cityName,
            t.dateStart,
            t.tournamentRank,
            cat.categoryName,
            cat.style,
            cat.competitorsCount
        FROM participations p
        JOIN tournaments t ON t.tournamentId = p.tournamentId
        JOIN categories cat ON cat.categoryId = p.categoryId
        ORDER BY t.dateStart DESC
    """).fetchall()]

    allSkatingMarks = [dict(r) for r in cursor.execute("""
        SELECT
            jm.categoryId,
            jm.roundCode,
            jm.danceCode,
            jm.judgeId,
            jm.coupleKey,
            jm.markValue,
            j.fullName AS judgeName,
            j.cityName AS judgeCity
        FROM judgeMarks jm
        JOIN judges j ON j.judgeId = jm.judgeId
        WHERE jm.markType = 'SKATING'
    """).fetchall()]

    validCoupleJudgePairs = cursor.execute("""
        SELECT DISTINCT coupleKey, judgeId FROM judgeMarks
    """).fetchall()

    connection.close()

    print(f"Building compact static bundle for {len(dancers)} dancers, {len(couples)} couples, {len(categoriesIds)} categories...")
    dancersAnalyticsMap = {d["dancerId"]: getDancerFullAnalytics(d["dancerId"]) for d in dancers}
    couplesAnalyticsMap = {c["coupleKey"]: getCoupleDeepAnalytics(c["coupleKey"]) for c in couples}
    categoriesDetailsMap = {catId: getCategorySkatingAndResults(catId) for catId in categoriesIds}
    judgesAnalyticsMap = {jId: getJudgesGlobalAnalytics(selectedJudgeId=jId) for jId in judgesIds}

    coupleJudgeHistoryMap = {}
    for row in validCoupleJudgePairs:
        cKey, jId = row["coupleKey"], row["judgeId"]
        hist = getCoupleJudgeHistoryTimeline(cKey, jId)
        if hist and not hist.get("error") and hist.get("timelineEvents"):
            # Strip duplicate couple/judge metadata inside each entry to keep bundle ultra-compact
            coupleJudgeHistoryMap[f"{cKey}__{jId}"] = {
                "timelineEvents": hist["timelineEvents"],
                "perDanceSummary": hist["perDanceSummary"],
            }

    mainStaticBundle = {
        "overview": getDashboardOverview(),
        "catalog": {
            "dancers": dancers,
            "couples": couples,
            "judges": judgesCatalog,
            "tournaments": tournamentsCatalog
        },
        "dancersMap": dancersAnalyticsMap,
        "couplesMap": couplesAnalyticsMap,
        "tournamentsAll": searchTournamentsCatalog(),
        "categoriesMap": categoriesDetailsMap,
        "judgesDefault": getJudgesGlobalAnalytics(selectedJudgeId=None),
        "judgesMap": judgesAnalyticsMap,
        "coupleJudgeHistoryMap": {},
        "allParticipations": allParticipations,
        "allSkatingMarks": allSkatingMarks,
    }

    baseDir = os.path.dirname(__file__)
    staticDir = os.path.join(baseDir, "static")
    docsDir = os.path.join(baseDir, "docs")
    os.makedirs(staticDir, exist_ok=True)
    os.makedirs(docsDir, exist_ok=True)

    mainBundleJs = "window.FTS_STATIC_BUNDLE=" + json.dumps(mainStaticBundle, ensure_ascii=False, separators=(",", ":")) + ";\n"
    historyBundleJs = (
        "if(window.FTS_STATIC_BUNDLE){window.FTS_STATIC_BUNDLE.coupleJudgeHistoryMap="
        + json.dumps(coupleJudgeHistoryMap, ensure_ascii=False, separators=(",", ":"))
        + ";}\n"
    )

    with open(os.path.join(staticDir, "ftsStaticData.js"), "w", encoding="utf-8") as fStatic:
        fStatic.write(mainBundleJs)
    with open(os.path.join(staticDir, "ftsStaticHistory.js"), "w", encoding="utf-8") as fHist:
        fHist.write(historyBundleJs)

    for targetFolder in [baseDir, docsDir]:
        shutil.copyfile(os.path.join(staticDir, "ftsStaticData.js"), os.path.join(targetFolder, "ftsStaticData.js"))
        shutil.copyfile(os.path.join(staticDir, "ftsStaticHistory.js"), os.path.join(targetFolder, "ftsStaticHistory.js"))
        shutil.copyfile(os.path.join(staticDir, "liveBrowserSync.js"), os.path.join(targetFolder, "liveBrowserSync.js"))
        shutil.copyfile(os.path.join(staticDir, "appClient.js"), os.path.join(targetFolder, "appClient.js"))
        with open(os.path.join(staticDir, "index.html"), "r", encoding="utf-8") as fHtmlIn:
            ghPagesHtml = fHtmlIn.read().replace('src="/static/', 'src="./')
        with open(os.path.join(targetFolder, "index.html"), "w", encoding="utf-8") as fHtmlOut:
            fHtmlOut.write(ghPagesHtml)
        with open(os.path.join(targetFolder, ".nojekyll"), "w", encoding="utf-8") as fNoJekyll:
            fNoJekyll.write("")


if __name__ == "__main__":
    buildStaticGithubPagesBundle()
    print("Compact static GitHub Pages bundle generated successfully.")
