from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import router as api_v1_router
from app.core.config import settings
from app.core.database import engine
from app.models.schema import Base

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix=settings.API_V1_STR)
from fastapi.responses import FileResponse
import os

# This tells FastAPI: "When someone visits the home page (/), send them my index.html file"
@app.get("/")
async def serve_frontend():
    # Adjust this path based on where your frontend folder is located relative to main.py
    frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend/index.html")
    return FileResponse(frontend_path)