# py-asgi-len: a raw ASGI hello with content-length (a known length). No framework.
BODY = b"hello\n"
START = {
    "type": "http.response.start",
    "status": 200,
    "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(BODY)).encode())],
}
END = {"type": "http.response.body", "body": BODY}


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
    await send(END)
