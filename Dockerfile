# syntax=docker/dockerfile:1
# Stage 1: build a headless OpenSCAD (no Qt, no OpenGL) from source.
FROM alpine:3.22 AS openscad-build
RUN apk add --no-cache build-base cmake samurai bison flex \
      boost-dev cgal-dev gmp-dev mpfr-dev eigen-dev double-conversion-dev \
      glib-dev harfbuzz-dev freetype-dev fontconfig-dev libxml2-dev libzip-dev
ARG OPENSCAD_VERSION=2021.01
ARG OPENSCAD_SHA512=8deaa26bf4c295c12da38f323d2b2e6f827851337f5bc1cc9c79afc083c9f913c19a263086e6e853bf2c8434c1ccc705ea22ddb02dc99d39bb1e5e03fc58d128
WORKDIR /build
RUN wget -q https://files.openscad.org/openscad-${OPENSCAD_VERSION}.src.tar.gz \
 && echo "${OPENSCAD_SHA512}  openscad-${OPENSCAD_VERSION}.src.tar.gz" | sha512sum -c - \
 && tar xzf openscad-${OPENSCAD_VERSION}.src.tar.gz && rm openscad-${OPENSCAD_VERSION}.src.tar.gz
COPY docker/patches/*.patch /build/patches/
WORKDIR /build/openscad-${OPENSCAD_VERSION}
# Alpine's patches (two CVE fixes, current CGAL and Boost) plus our fix for the no-OpenGL build.
RUN for p in /build/patches/*.patch; do patch -p1 < "$p"; done
RUN cmake -G Ninja -B out \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
      -DHEADLESS=ON -DNULLGL=ON -DEXPERIMENTAL=OFF -DVERSION=${OPENSCAD_VERSION} -Wno-dev \
      -DCMAKE_DISABLE_FIND_PACKAGE_Cairo=ON \
 && cmake --build out -j"$(nproc)" \
 && strip out/openscad \
 && ./out/openscad --version
# The 2021.01 cmake build has no install target; the single binary is all we need.
# (share/openscad holds bundled fonts, colour schemes and MCAD, none of which the sign uses.)

# Stage 2: runtime.
FROM alpine:3.22
ARG OPENSCAD_VERSION=2021.01
# Only what the headless openscad binary links (see ldd), plus fonts, potrace and Python.
RUN apk add --no-cache \
      boost1.84-filesystem boost1.84-program_options \
      gmp libgmpxx mpfr4 double-conversion glib harfbuzz freetype fontconfig libxml2 libzip libstdc++ \
      font-urw-base35 potrace python3 \
 && fc-cache -f
COPY --from=openscad-build /build/openscad-${OPENSCAD_VERSION}/out/openscad /usr/bin/openscad

COPY requirements.txt /tmp/req-trace.txt
COPY web/requirements.txt /tmp/req-web.txt
RUN apk add --no-cache py3-pip \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/req-trace.txt -r /tmp/req-web.txt \
 && /opt/venv/bin/pip uninstall -y pip setuptools \
 && apk del py3-pip \
 && rm -rf /tmp/req-*.txt /root/.cache \
 && find /opt/venv /usr/lib/python3* -name '__pycache__' -prune -exec rm -rf {} + \
 && rm -rf /usr/lib/python3*/ensurepip /usr/lib/python3*/idlelib /usr/lib/python3*/lib2to3 \
           /usr/lib/python3*/turtledemo /usr/lib/python3*/pydoc_data /usr/lib/python3*/tkinter

WORKDIR /app
COPY custom_sign.scad icon.svg trace_silhouette.py ./
COPY web ./web
RUN adduser -D -H sign && chown -R sign:sign /app
USER sign
ENV SIGN_PYTHON=/opt/venv/bin/python HOME=/tmp PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
