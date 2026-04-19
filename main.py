from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
import config
from routers import chat, webhooks

app = FastAPI(title="Shopping Agent")
app.include_router(chat.router)
app.include_router(webhooks.router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    app.state.mongo = AsyncIOMotorClient(config.MONGODB_URI)


@app.on_event("shutdown")
async def shutdown():
    app.state.mongo.close()


@app.get("/")
async def root():
    return FileResponse("static/index.html")
