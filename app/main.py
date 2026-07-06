from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.web.routes import router

app = FastAPI(title="Morti News Digest")
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.include_router(router)
