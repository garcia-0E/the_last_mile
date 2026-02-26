from sanic import Sanic
from sanic.response import text

app = Sanic("TheLastMileAPI")


@app.get("/")
async def health_check(request):
    return text("Hello World")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, auto_reload=True)