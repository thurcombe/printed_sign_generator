FROM alpine:3.22

RUN apk add --no-cache openscad font-urw-base35 fontconfig python3 py3-pip \
 && fc-cache -f

COPY requirements.txt /tmp/req-trace.txt
COPY web/requirements.txt /tmp/req-web.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/req-trace.txt -r /tmp/req-web.txt \
 && rm /tmp/req-*.txt

WORKDIR /app
COPY custom_sign.scad icon.svg trace_silhouette.py ./
COPY web ./web

RUN adduser -D -H sign && chown -R sign:sign /app
USER sign
ENV SIGN_PYTHON=/opt/venv/bin/python \
    HOME=/tmp \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8000/healthz || exit 1
CMD ["/opt/venv/bin/uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
