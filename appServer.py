import os
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from databaseManager import getDbConnection, initializeDatabase
from seedRealisticFtsData import seedComprehensiveFtsDatabase
from ftsDataScraper import FtsDataSyncManager
from analyticsEngine import (
    getDashboardOverview,
    getDancerFullAnalytics,
    getCoupleDeepAnalytics,
    getHeadToHeadComparison,
    searchTournamentsCatalog,
    getCategorySkatingAndResults,
    getJudgesGlobalAnalytics,
    getCoupleJudgeHistoryTimeline
)

app = FastAPI(
    title="FTS & PZST DanceSport Intelligence Suite",
    description="System Analityki Par Tanecznych, Turniejów, Historii Partnerstw i Sędziowania (Skating System)",
    version="2.4.0"
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def onServerStartup() -> None:
    initializeDatabase()
    seedComprehensiveFtsDatabase(forceReseed=False)


@app.get("/", response_class=HTMLResponse)
def serveWebApplication() -> HTMLResponse:
    indexHtmlPath = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(indexHtmlPath):
        with open(indexHtmlPath, "r", encoding="utf-8") as fileHandle:
            return HTMLResponse(content=fileHandle.read())
    return HTMLResponse(content="<h1>FTS Analytics Loading...</h1>")


@app.get("/api/overview")
def apiGetOverview() -> JSONResponse:
    return JSONResponse(content=getDashboardOverview())


@app.get("/api/catalog")
def apiGetCatalog() -> JSONResponse:
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

    judges = [dict(r) for r in cursor.execute("""
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

    tournaments = [dict(r) for r in cursor.execute("""
        SELECT
            t.*,
            (SELECT COUNT(*) FROM categories c2 WHERE c2.tournamentId = t.tournamentId) AS categoriesCount,
            (SELECT COUNT(*) FROM participations p2 WHERE p2.tournamentId = t.tournamentId) AS entriesCount
        FROM tournaments t
        ORDER BY t.dateStart DESC
    """).fetchall()]

    connection.close()
    return JSONResponse(content={
        "dancers": dancers,
        "couples": couples,
        "judges": judges,
        "tournaments": tournaments
    })


@app.get("/api/dancer/{dancerId}")
def apiGetDancerAnalytics(dancerId: str) -> JSONResponse:
    return JSONResponse(content=getDancerFullAnalytics(dancerId))


@app.get("/api/couple/{coupleKey}")
def apiGetCoupleAnalytics(coupleKey: str) -> JSONResponse:
    return JSONResponse(content=getCoupleDeepAnalytics(coupleKey))


@app.get("/api/couple/{coupleKey}/judge/{judgeId}")
def apiGetCoupleJudgeHistory(coupleKey: str, judgeId: str) -> JSONResponse:
    return JSONResponse(content=getCoupleJudgeHistoryTimeline(coupleKey, judgeId))


@app.get("/api/head-to-head")
def apiGetHeadToHead(
    coupleA: str = Query(..., description="First couple key (maleId_femaleId)"),
    coupleB: str = Query(..., description="Second couple key (maleId_femaleId)")
) -> JSONResponse:
    return JSONResponse(content=getHeadToHeadComparison(coupleA, coupleB))


@app.get("/api/tournaments")
def apiSearchTournaments(
    q: str = Query(""),
    voivodeship: str = Query(""),
    danceClass: str = Query(""),
    ageGroup: str = Query(""),
    style: str = Query(""),
    rank: str = Query("")
) -> JSONResponse:
    results = searchTournamentsCatalog(
        searchQuery=q,
        voivodeship=voivodeship,
        danceClass=danceClass,
        ageGroup=ageGroup,
        style=style,
        tournamentRank=rank
    )
    return JSONResponse(content={"tournaments": results})


@app.get("/api/category/{categoryId}")
def apiGetCategoryDetails(categoryId: str) -> JSONResponse:
    return JSONResponse(content=getCategorySkatingAndResults(categoryId))


@app.get("/api/judges")
def apiGetJudgesAnalytics(judgeId: Optional[str] = Query(None)) -> JSONResponse:
    return JSONResponse(content=getJudgesGlobalAnalytics(selectedJudgeId=judgeId))


@app.post("/api/sync-live")
def apiTriggerLiveSync(maxTournaments: int = Query(5)) -> JSONResponse:
    syncManager = FtsDataSyncManager()
    syncReport = syncManager.syncFromLiveSources(maxTournaments=maxTournaments)
    return JSONResponse(content=syncReport)
