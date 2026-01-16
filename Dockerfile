FROM python:3.12-alpine

RUN apk add --no-cache gnupg bash

RUN pip install --no-cache-dir boto3

WORKDIR /app

COPY setup.py /app/
COPY teardown.py /app/
COPY entrypoint.sh /app/
COPY caution-public-key.asc /app/

RUN chmod +x /app/entrypoint.sh /app/setup.py /app/teardown.py

RUN mkdir /out

ENTRYPOINT ["/app/entrypoint.sh"]
