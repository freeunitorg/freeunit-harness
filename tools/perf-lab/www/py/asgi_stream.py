# py-asgi-stream: a raw ASGI hello in two body messages (more_body) with no
# content-length. The length is unknown, so Unit sends the response chunked.
START = {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]}
PART1 = {"type": "http.response.body", "body": b"hel", "more_body": True}
PART2 = {"type": "http.response.body", "body": b"lo\n", "more_body": False}


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            m = await receive()
            if m["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif m["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    await send(START)
    await send(PART1)
    await send(PART2)
