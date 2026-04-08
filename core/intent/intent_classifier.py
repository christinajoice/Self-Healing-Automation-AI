class IntentClassifier:
    @staticmethod
    def classify(action: str, target: str, data: str):
        text = f"{target} {data}".lower()
        target_lower = target.lower()

        if action != "validate":
            return "locator"

        # ── url_contains ─────────────────────────────────────────────────
        # validate, url contains, /explorer/tin   → checks data substring in URL
        # validate, page url, specialty           → same
        # Also upgrades the plain "url" intent when data is provided so the
        # old `assert page.url` becomes `assert data in page.url`.
        if any(word in text for word in ["url", "page", "redirect", "land", "navigate"]):
            return "url_contains"

        # ── message ──────────────────────────────────────────────────────
        if any(word in text for word in ["message", "success", "error", "alert", "flash"]):
            return "message"

        # ── column_values ─────────────────────────────────────────────────
        if "column" in text:
            return "column_values"

        # ── map_data_match ────────────────────────────────────────────────
        # validate, map data,                → cross-ref map features vs any table column
        # validate, map gradient,            → same + validate legend-driven color
        # validate, map gap data,            → same (gap is just the column name)
        if "map" in target_lower and any(
            w in target_lower for w in ["data", "gap", "gradient", "color", "colour", "value", "score"]
        ):
            return "map_data_match"

        # ── map_loaded ────────────────────────────────────────────────────
        # validate, map loaded,              → checks map canvas/container visible
        # validate, map,                     → same
        # validate, map markers,             → checks markers exist
        if "map" in target_lower:
            return "map_loaded"

        # ── count ─────────────────────────────────────────────────────────
        # validate, providers count, 3       → checks ≥3 provider elements visible
        # validate, markers count, 1         → checks ≥1 marker visible
        if "count" in target_lower:
            return "count"

        # ── db_data ───────────────────────────────────────────────────────
        # validate, db data, gaps_summary        → run SQL, compare to UI table
        # validate, database, provider_summary   → same
        if any(word in target_lower for word in ["db", "database", "sql", "query"]):
            return "db_data"

        # ── locator (default) ─────────────────────────────────────────────
        if any(word in text for word in ["button", "icon", "field", "link"]):
            return "locator"

        return "locator"
