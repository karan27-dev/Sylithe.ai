from fastapi import FastAPI

from sylithe import __version__
from sylithe.api import dashboard, routes
from sylithe.api.deps import AppState, build_state


def create_app(state: AppState | None = None) -> FastAPI:
    app = FastAPI(title="Sylithe AI", version=__version__)
    app.state.sylithe = state or build_state()
    app.include_router(routes.router)
    app.include_router(dashboard.router)
    return app


def main() -> None:  # `python -m sylithe.main` / console entry
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
