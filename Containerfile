FROM registry.access.redhat.com/ubi10/ubi-minimal:latest

RUN microdnf install -y python3 python3-pip && \
    microdnf clean all

WORKDIR /app

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY src/server.py .

ENV MOCK_SCHEMA_PATH=/config/schema.json
ENV MOCK_FIXTURES_PATH=/config/fixtures.json
ENV MOCK_STRATEGY=fixtures
ENV MOCK_TRANSPORT=streamable-http
ENV MOCK_PORT=8080
ENV MOCK_LLM_MODEL=claude-haiku-4-5-20251001

EXPOSE ${MOCK_PORT}

USER 65532:65532

ENTRYPOINT ["python3", "server.py"]
CMD []
