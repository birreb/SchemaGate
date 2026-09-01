from fastapi import FastAPI

from schemagate import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="SchemaGate", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
