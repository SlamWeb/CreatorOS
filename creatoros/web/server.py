import uvicorn


class StudioServer(uvicorn.Server):
    """Close read-only observers before Uvicorn waits for active HTTP requests."""

    async def shutdown(self, sockets=None):
        self.config.app.state.stop_observers = True
        await super().shutdown(sockets=sockets)
