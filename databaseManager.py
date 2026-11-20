import sqlite3
import json
import os
from typing import Optional, List, Dict, Any

DB_PATH = os.environ.get("FTS_DB_PATH", os.path.join(os.path.dirname(__file__), "ftsAnalytics.db"))


def getDbConnection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def initializeDatabase() -> None:
    connection = getDbConnection()
    cursor = connection.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS dancers (
        dancerId TEXT PRIMARY KEY,
        fullName TEXT NOT NULL,
        firstName TEXT,
        lastName TEXT,
        gender TEXT,
        wdsfMin TEXT,
        currentClub TEXT,
        currentClassSt TEXT,
        currentClassLa TEXT,
        updatedAt TEXT
    );

    CREATE TABLE IF NOT EXISTS couples (
        coupleKey TEXT PRIMARY KEY,
        maleDancerId TEXT NOT NULL,
        femaleDancerId TEXT NOT NULL,
        starterId TEXT,
        coupleName TEXT NOT NULL,
        clubName TEXT,
        cityName TEXT,
        ageGroup TEXT,
        classSt TEXT,
        classLa TEXT,
        firstTournamentDate TEXT,
        lastTournamentDate TEXT,
        isActive INTEGER DEFAULT 1,
        FOREIGN KEY (maleDancerId) REFERENCES dancers(dancerId),
        FOREIGN KEY (femaleDancerId) REFERENCES dancers(dancerId)
    );

    CREATE TABLE IF NOT EXISTS tournaments (
        tournamentId TEXT PRIMARY KEY,
        sourceSystem TEXT NOT NULL,
        competitionName TEXT NOT NULL,
        cityName TEXT,
        voivodeship TEXT,
        venueName TEXT,
        venueAddress TEXT,
        dateStart TEXT NOT NULL,
        dateEnd TEXT,
        tournamentRank TEXT,
        portalUrl TEXT,
        liveResultsUrl TEXT,
        isArchived INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS categories (
        categoryId TEXT PRIMARY KEY,
        tournamentId TEXT NOT NULL,
        categoryName TEXT NOT NULL,
        ageGroup TEXT,
        danceClass TEXT,
        style TEXT,
        dancesList TEXT,
        judgingSystem TEXT,
        competitorsCount INTEGER DEFAULT 0,
        roundsCount INTEGER DEFAULT 1,
        blockName TEXT,
        FOREIGN KEY (tournamentId) REFERENCES tournaments(tournamentId)
    );

    CREATE TABLE IF NOT EXISTS judges (
        judgeId TEXT PRIMARY KEY,
        fullName TEXT NOT NULL,
        cityName TEXT,
        regionTag TEXT,
        licenseCategory TEXT,
        wdsfLicense TEXT,
        isChairperson INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS categoryJudges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        categoryId TEXT NOT NULL,
        judgeLetter TEXT NOT NULL,
        judgeId TEXT NOT NULL,
        judgeRole TEXT DEFAULT 'judge',
        FOREIGN KEY (categoryId) REFERENCES categories(categoryId),
        FOREIGN KEY (judgeId) REFERENCES judges(judgeId)
    );

    CREATE TABLE IF NOT EXISTS participations (
        participationId INTEGER PRIMARY KEY AUTOINCREMENT,
        categoryId TEXT NOT NULL,
        tournamentId TEXT NOT NULL,
        coupleKey TEXT NOT NULL,
        maleDancerId TEXT NOT NULL,
        femaleDancerId TEXT NOT NULL,
        startNumber INTEGER,
        clubName TEXT,
        cityName TEXT,
        finalPlace INTEGER NOT NULL,
        maxRoundReached TEXT,
        pointsEarned REAL DEFAULT 0,
        podiumCredit INTEGER DEFAULT 0,
        totalPreliminaryMarks INTEGER DEFAULT 0,
        FOREIGN KEY (categoryId) REFERENCES categories(categoryId),
        FOREIGN KEY (tournamentId) REFERENCES tournaments(tournamentId),
        FOREIGN KEY (coupleKey) REFERENCES couples(coupleKey)
    );

    CREATE TABLE IF NOT EXISTS judgeMarks (
        markId INTEGER PRIMARY KEY AUTOINCREMENT,
        categoryId TEXT NOT NULL,
        tournamentId TEXT NOT NULL,
        coupleKey TEXT NOT NULL,
        roundCode TEXT NOT NULL,
        danceCode TEXT NOT NULL,
        judgeId TEXT NOT NULL,
        judgeLetter TEXT NOT NULL,
        markType TEXT NOT NULL,
        markValue INTEGER NOT NULL,
        danceCalculatedPlace REAL,
        FOREIGN KEY (categoryId) REFERENCES categories(categoryId),
        FOREIGN KEY (coupleKey) REFERENCES couples(coupleKey),
        FOREIGN KEY (judgeId) REFERENCES judges(judgeId)
    );

    CREATE INDEX IF NOT EXISTS idx_couples_male ON couples(maleDancerId);
    CREATE INDEX IF NOT EXISTS idx_couples_female ON couples(femaleDancerId);
    CREATE INDEX IF NOT EXISTS idx_participations_couple ON participations(coupleKey);
    CREATE INDEX IF NOT EXISTS idx_participations_tournament ON participations(tournamentId);
    CREATE INDEX IF NOT EXISTS idx_participations_category ON participations(categoryId);
    CREATE INDEX IF NOT EXISTS idx_judgemarks_couple ON judgeMarks(coupleKey);
    CREATE INDEX IF NOT EXISTS idx_judgemarks_judge ON judgeMarks(judgeId);
    CREATE INDEX IF NOT EXISTS idx_judgemarks_category ON judgeMarks(categoryId);
    """)

    connection.commit()
    connection.close()
