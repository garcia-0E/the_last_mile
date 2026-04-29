# Use an official Python runtime as a parent image
FROM python:3.12-slim

# set work directory
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# install dependencies
COPY server/requirements.txt ./
RUN uv add -r requirements.txt

# copy application code
COPY server/ .

# expose port
EXPOSE 8080

# entrypoint
ENTRYPOINT ["uv", "run", "python", "server.py"]
