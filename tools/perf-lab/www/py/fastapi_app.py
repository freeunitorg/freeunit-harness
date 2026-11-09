# py-fastapi: one FastAPI route that returns a small JSON object.
# async def: the route runs on the event loop. A plain def would add a thread
# pool hop per request (measured locally: 2244 rps sync, 7524 async, c=6).
# Needs the venv in the lab image ("home": "/opt/pyvenv").
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "hello"}
