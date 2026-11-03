"""HTTP client for HomeMate: the framework sees only what any API client sees."""

import httpx


class HomeMateClient:
    """Calls /chat, /health, /devices and the test-only /admin/reset."""

    def __init__(self, base_url: str, timeout_s: float = 600) -> None:
        """Open one HTTP connection pool to HomeMate."""
        self.http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def chat(self, session_id: str, message: str, seed: int | None = None) -> tuple[int, object]:
        """Send one message; return the status code and the JSON body (or raw text if not JSON)."""
        payload = {"session_id": session_id, "message": message}
        if seed is not None:
            payload["seed"] = seed
        response = self.http.post("/chat", json=payload)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    def health(self) -> dict:
        """Return version, model and knowledge-base version."""
        response = self.http.get("/health")
        response.raise_for_status()
        return response.json()

    def devices(self) -> dict:
        """Return the current state of every device."""
        response = self.http.get("/devices")
        response.raise_for_status()
        return response.json()["devices"]

    def reset(self) -> None:
        """Reset devices and sessions (needs ENABLE_TEST_ADMIN=1 on the server)."""
        self.http.post("/admin/reset").raise_for_status()
