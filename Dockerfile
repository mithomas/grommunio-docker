# syntax=docker/dockerfile:1.6
ARG DEBIAN_RELEASE=trixie-slim
FROM debian:${DEBIAN_RELEASE}

ARG DEBIAN_FRONTEND=noninteractive
ARG GROMMUNIO_REPO_URL=https://download.grommunio.com/community/Debian_13/
ARG GROMMUNIO_REPO_SUITE=Debian_13

# 1. Install prerequisites and configure Grommunio repository
COPY rootfs/usr/local/share/ca-certificates/ /usr/local/share/ca-certificates/
COPY rootfs/usr/share/keyrings/ /usr/share/keyrings/

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gnupg \
    && update-ca-certificates \
    && mkdir -p /usr/share/keyrings /etc/apt/sources.list.d \
    && if [ ! -f /usr/share/keyrings/download.grommunio.com.gpg ]; then \
         curl -fsSL https://download.grommunio.com/RPM-GPG-KEY-grommunio | gpg --dearmor -o /usr/share/keyrings/download.grommunio.com.gpg; \
       fi \
    && echo "deb [signed-by=/usr/share/keyrings/download.grommunio.com.gpg] ${GROMMUNIO_REPO_URL} ${GROMMUNIO_REPO_SUITE} main" > /etc/apt/sources.list.d/grommunio.list

# 2. Install Grommunio and supporting daemons
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      gromox \
      grommunio-web \
      grommunio-sync \
      grommunio-admin-api \
      grommunio-admin-web \
      grommunio-admin-common \
      grommunio-admin-api-mod-vts \
      nginx-module-vts \
      grommunio-common \
      grommunio-dbconf \
      grommunio-error-pages \
      nginx \
      php-fpm \
      php-cli \
      php-mysql \
      php-ldap \
      php-curl \
      php-intl \
      php-xml \
      php-mbstring \
      php-apcu \
      uwsgi \
      uwsgi-plugin-python3 \
      python3-cryptography \
      redis-server \
      rspamd \
      exim4-daemon-heavy \
      supervisor \
      mariadb-client \
      sqlite3 \
      ldap-utils \
      netcat-openbsd \
      gettext-base

# 3. Ensure system users and group memberships exist
RUN getent group gromoxcf >/dev/null || groupadd -r gromoxcf && \
    getent group gromox >/dev/null || groupadd -r gromox && \
    getent passwd gromox >/dev/null || useradd -r -g gromox -G gromoxcf -d /var/lib/gromox -s /usr/sbin/nologin gromox && \
    getent group grommunio >/dev/null || groupadd -r grommunio && \
    getent passwd grommunio >/dev/null || useradd -r -g grommunio -G gromox,gromoxcf -d /var/lib/grommunio-admin-common -s /usr/sbin/nologin grommunio && \
    usermod -aG Debian-exim,adm grommunio 2>/dev/null || true && \
    if [ -d /etc/grommunio-sync ]; then chmod 0755 /etc/grommunio-sync && usermod -aG grosync www-data 2>/dev/null || true; fi && \
    usermod -aG gromox www-data 2>/dev/null || true && \
    getent group _rspamd >/dev/null || groupadd -r _rspamd && \
    getent passwd _rspamd >/dev/null || useradd -r -g _rspamd -d /var/lib/rspamd -s /usr/sbin/nologin _rspamd

# 4. Service configuration tuning and cleanups
RUN rm -f /etc/nginx/sites-enabled/* /etc/nginx/sites-available/* /etc/nginx/conf.d/grommunio.conf \
    && if [ ! -f /usr/sbin/php-fpm ]; then ln -s /usr/sbin/php-fpm* /usr/sbin/php-fpm; fi \
    && if [ -f /etc/grommunio-web/config.php ]; then sed -i 's|:8080/api/v1/|:8081/api/v1/|g' /etc/grommunio-web/config.php; fi \
    && if [ -f /etc/rspamd/worker-controller.inc ]; then sed -i 's|^password = .*|# password|' /etc/rspamd/worker-controller.inc; fi

# 5. Patch grommunio-admin-api for RFC 9112 hop-by-hop header compliance and file-based logging:
# Strips hop-by-hop headers ('transfer-encoding', 'content-length', 'connection', 'content-encoding')
# when proxying upstream responses in vhostStatus and rspamdProxy. This prevents uWSGI from sending
# both Content-Length and Transfer-Encoding simultaneously on chunked responses, which causes
# Nginx 1.25+ reverse proxy to abort with 502 Bad Gateway.
# Allows 'file' format in schema for containerized file-based log monitoring.
# Pre-compiles modified bytecode so python does not attempt to write .pyc files to the read-only rootfs.
RUN python3 -c 'p = "/usr/share/grommunio-admin-api/endpoints/system/misc.py"; s = open(p).read(); old = "return res.raw.read(), res.status_code, res.headers.items()"; new = "excluded = {\"content-encoding\", \"content-length\", \"transfer-encoding\", \"connection\"}\n    headers = [(k, v) for k, v in res.headers.items() if k.lower() not in excluded]\n    return res.content, res.status_code, headers"; assert s.count(old) == 2, f"Expected 2 occurrences of target string, found {s.count(old)}"; open(p, "w").write(s.replace(old, new))' \
    && sed -i 's/enum: \[journald\]/enum: [journald, file]/' /usr/share/grommunio-admin-api/res/config.yaml \
    && python3 -m compileall /usr/share/grommunio-admin-api/endpoints/system/misc.py

# 6. Create persistent and mountpoint directories
RUN mkdir -p /var/lib/gromox /var/lib/redis /var/lib/rspamd /var/lib/grommunio-admin-common /var/lib/grommunio-admin-api /var/lib/grommunio-web \
    /var/lib/grommunio-web/session /var/lib/grommunio-web/tmp /var/lib/php/sessions \
    /run/gromox /run/php /run/redis /run/rspamd /run/supervisor /run/grommunio-admin-api \
    /var/log/gromox /var/log/supervisor /var/log/nginx /var/log/grommunio-sync /var/log/redis /var/log/rspamd \
    /var/lib/nginx/body /var/lib/nginx/fastcgi /var/lib/nginx/proxy /var/lib/nginx/scgi /var/lib/nginx/uwsgi \
    /var/spool/exim4 /var/log/exim4 \
    /etc/gromox.dist /etc/grommunio-admin-api.dist /etc/grommunio-admin-common.dist /etc/exim4.dist /etc/php.dist \
    && if [ -d /etc/grommunio-admin-api ]; then cp -a /etc/grommunio-admin-api/. /etc/grommunio-admin-api.dist/; fi \
    && if [ -d /etc/grommunio-admin-common ]; then cp -a /etc/grommunio-admin-common/. /etc/grommunio-admin-common.dist/; fi \
    && if [ -d /etc/exim4 ]; then cp -a /etc/exim4/. /etc/exim4.dist/; fi

# 7. Copy filesystem overlay and apply modular configurations
COPY --chmod=0755 rootfs/ /

RUN for POOL_DIR in $(find /etc/php -type d -name "pool.d" 2>/dev/null); do \
      cp /etc/php.dist/zz-grommunio-pool.conf "${POOL_DIR}/zz-grommunio.conf"; \
    done \
    && for INI_DIR in $(find /etc/php -type d -name "conf.d" 2>/dev/null); do \
      cp /etc/php.dist/99-grommunio.ini "${INI_DIR}/99-grommunio.ini"; \
    done \
    && sed -i 's/\r$//' /entrypoint.sh /usr/local/bin/* 2>/dev/null || true \
    && chmod +x /entrypoint.sh /usr/local/bin/* 2>/dev/null || true \
    && python3 -m compileall -q /usr/share/grommunio-admin-api/

ENV TZ=UTC

EXPOSE 8080 8081 11334 2525

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD /usr/local/bin/container-healthcheck.sh

ENTRYPOINT ["/entrypoint.sh"]
