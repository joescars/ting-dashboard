FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TING_DATA_DIR=/data PORT=8097
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --create-home ting \
 && mkdir -p /data && chown ting:ting /data

ARG TING_VERSION=dev
ENV TING_VERSION=${TING_VERSION}

LABEL org.opencontainers.image.title="Ting Dashboard" \
      org.opencontainers.image.description="Self-hosted web dashboard for a Whisker Labs Ting electrical fire sensor" \
      org.opencontainers.image.source="https://github.com/simplytoast1/ting-dashboard" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${TING_VERSION}"

COPY VERSION ./
COPY app ./app
COPY static ./static
COPY THIRD_PARTY_LICENSE_ha-whisker-ting ./

USER ting
EXPOSE 8097
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8097/healthz', timeout=4)" || exit 1
CMD ["python", "-m", "app.main"]
