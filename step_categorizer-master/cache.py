import json
import sqlite3
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_DB = CACHE_DIR / "cache.sqlite3"
_connection = None

def cache_init(db_path=CACHE_DB):
    """Open DB and create table if necessary. Returns sqlite3.Connection."""
    global _connection
    if _connection:
        return _connection
    connection = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    connection.execute("PRAGMA journal_mode = WAL;")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS characteristics_cache (
            stem TEXT PRIMARY KEY,
            filename TEXT,
            step_mtime REAL,
            data TEXT
        )
    """)
    connection.commit()
    _connection = connection
    return connection

def cache_get_row(connection, stem):
    """Return cache row dict for stem, or None if not found."""
    try:
        current = connection.execute("SELECT data, step_mtime FROM characteristics_cache WHERE stem = ?", (stem,))
        row = current.fetchone()
        if not row:
            return None, None
        data_json, cached_mtime = row
        data = json.loads(data_json) if data_json else None
        return data, float(cached_mtime) if cached_mtime is not None else None
    except Exception as e:
        print(f"Cache read error for {stem}: {e}")
        return None, None

def cache_get_valid(connection, stem, step_mtime):
    """
    Return data dict if cache entry exists and cached_mtime >= step_mtime, else None.
    step_mtime may be None -> treat as not valid.
    """
    if step_mtime is None:
        return None
    try:
        data, cached_mtime = cache_get_row(connection, stem)
        if data is None or cached_mtime is None:
            return None
        if float(cached_mtime) >= float(step_mtime):
            return data
    except Exception as e:
        print(f"cache_get_valid error for {stem}: {e}")
    return None

def cache_save(connection, stem, characteristics, step_mtime):
    """Save/replace a cache row for stem if it's changed. step_mtime should be a float (mtime)."""
    try:
        existing_data, existing_mtime = cache_get_row(connection, stem)
        new_data_json = json.dumps(characteristics, sort_keys=True, default=str)
        if existing_data is not None:
            existing_json = json.dumps(existing_data, sort_keys=True, default=str)
            # if json data matches and cached mtime >= step_mtime, skip write
            if existing_json == new_data_json and existing_mtime is not None and step_mtime is not None and existing_mtime >= float(step_mtime):
                return False

        connection.execute(
            "REPLACE INTO characteristics_cache (stem, filename, step_mtime, data) VALUES (?, ?, ?, ?)",
            (stem, characteristics.get('filename'), float(step_mtime) if step_mtime is not None else None, new_data_json)
        )
        connection.commit()

        return True
    except Exception as e:
        print(f"Cache write error for {stem}: {e}")