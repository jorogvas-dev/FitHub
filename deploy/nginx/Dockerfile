FROM nginx:1.30.6-alpine
RUN apk add --no-cache openssl
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY proxy.conf /etc/nginx/fithub-proxy.conf
COPY 15-local-certificate.sh /docker-entrypoint.d/15-local-certificate.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/15-local-certificate.sh \
    && chmod 755 /docker-entrypoint.d/15-local-certificate.sh
