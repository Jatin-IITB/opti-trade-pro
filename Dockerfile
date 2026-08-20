FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml Readme.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

RUN useradd --create-home --shell /usr/sbin/nologin optitrade
COPY --from=builder /install /usr/local
COPY --from=builder /app/src /app/src

WORKDIR /app
USER optitrade
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "options_trading.main:app", "--host", "0.0.0.0", "--port", "8000"]
