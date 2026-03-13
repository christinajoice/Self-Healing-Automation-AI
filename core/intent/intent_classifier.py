class IntentClassifier:
    @staticmethod
    def classify(action: str, target: str, data: str):
        text = f"{target} {data}".lower()

        if action != "validate":
            return "locator"

        if any(word in text for word in ["url", "page", "redirect", "land", "navigate"]):
            return "url"

        if any(word in text for word in ["message", "success", "error", "alert", "flash"]):
            return "message"

        if any(word in text for word in ["button", "icon", "field", "link"]):
            return "locator"

        return "unknown"
