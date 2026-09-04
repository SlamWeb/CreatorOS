from creatoros.config import DATABASE_URL
from creatoros.storage import upgrade_database

upgrade_database(DATABASE_URL)

from .app import create_app

app = create_app(DATABASE_URL)


if __name__ == "__main__":
    import uvicorn
    from .server import StudioServer

    # Long-lived observers must not indefinitely delay the executor's shutdown.
    StudioServer(uvicorn.Config(app, host="127.0.0.1", port=8765, timeout_graceful_shutdown=3)).run()
