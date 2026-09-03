from creatoros.config import DATABASE_URL
from creatoros.storage import upgrade_database

upgrade_database(DATABASE_URL)

from .app import create_app

app = create_app(DATABASE_URL)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
