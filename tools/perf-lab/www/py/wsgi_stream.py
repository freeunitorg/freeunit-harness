# py-wsgi-stream: a WSGI hello in two parts with no Content-Length.
# The length is unknown, so Unit sends the response chunked.
HEADERS = [("Content-Type", "text/plain")]


def application(environ, start_response):
    start_response("200 OK", HEADERS)
    yield b"hel"
    yield b"lo\n"
