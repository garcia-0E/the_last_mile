# Use an official Python runtime as a parent image
FROM python:3.12-slim

# set work directory
WORKDIR /app

# install dependencies
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy application code
COPY server/ .

# expose port
EXPOSE 8080

# entrypoint
ENTRYPOINT ["python", "server.py"]
