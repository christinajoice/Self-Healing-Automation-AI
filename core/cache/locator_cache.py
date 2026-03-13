import json
import os

CACHE_FILE = "core/cache/locators.json"


class LocatorCache:
    def __init__(self):
        self.cache_file = CACHE_FILE
        if not os.path.exists(self.cache_file):
            with open(self.cache_file, "w") as f:
                json.dump({}, f)
        self._load_cache()

    def _load_cache(self):
        with open(self.cache_file, "r") as f:
            self.cache = json.load(f)

    def _save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f, indent=4)

    # --- Existing single locator get/set ---
    def get(self, app_url: str, element_name: str):
        return self.cache.get(app_url, {}).get(element_name, None)

    def set(self, app_url: str, element_name: str, locator_meta):
        if app_url not in self.cache:
            self.cache[app_url] = {}
        # Wrap in list if it's not already a list
        if isinstance(locator_meta, dict):
            locator_meta = [locator_meta]
        self.cache[app_url][element_name] = locator_meta
        self._save_cache()

    # --- Step 5: Multiple locator candidates ---
    def get_all(self, app_url: str, element_name: str):
        """
        Returns a list of locator candidates for the element,
        or None if not found
        """
        return self.cache.get(app_url, {}).get(element_name, None)

    def append_candidate(self, app_url: str, element_name: str, locator_meta):
        """
        Add a new candidate to existing cache entry
        """
        if app_url not in self.cache:
            self.cache[app_url] = {}
        if element_name not in self.cache[app_url]:
            self.cache[app_url][element_name] = []
        self.cache[app_url][element_name].append(locator_meta)
        self._save_cache()
