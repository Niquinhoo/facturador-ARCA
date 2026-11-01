FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 calma \
    && useradd --uid 10001 --gid calma --create-home --shell /usr/sbin/nologin calma

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY superbot ./superbot
COPY run_superbot.py healthcheck.py ./

RUN mkdir -p /data/documents \
    && chown -R calma:calma /app /data

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["python", "run_superbot.py"]
