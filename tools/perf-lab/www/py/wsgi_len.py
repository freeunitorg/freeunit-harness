# py-wsgi-len: a WSGI hello with Content-Length (a known length).
BODY = b"hello\n"
HEADERS = [("Content-Type", "text/plain"), ("Content-Length", str(len(BODY)))]


def application(environ, start_response):
    start_response("200 OK", HEADERS)
    return [BODY]
