from sanic import Sanic
from sanic.response import text, json as json_response
from sanic.request import Request
from enhancer import normalize_dataframe, load_to_bigquery, publish_file_message



app = Sanic("TheLastMileAPI")


@app.post("/health")
async def health_check(request: Request):
    body = request.json
    # Process the request body as needed
    if 'file' in body:
        file_info = body['file']
        load_to_bigquery(normalize_dataframe(file_info))
        message_id = publish_file_message(body)
        return json_response({"status": "processed", "message_id": message_id})
    return text("Hello World from the Built image!")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, auto_reload=True)