from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routers.predictions import router as predictions_router

app = FastAPI(
    title="Football AI Predictor",
    description="API de prédictions de matchs de football pour les 5 grands championnats européens.",
    version="1.0.0",
)

app.include_router(predictions_router, tags=["Predictions"])
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")
