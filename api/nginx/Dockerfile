FROM docker.io/library/nginx:1.27-alpine

ARG SERVER_NAME=169.58.152.33

RUN apk add --no-cache openssl \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -nodes -days 365 \
        -newkey rsa:2048 \
        -keyout /etc/nginx/certs/marketstore.key \
        -out /etc/nginx/certs/marketstore.crt \
        -subj "/CN=${SERVER_NAME}" \
        -addext "subjectAltName=IP:${SERVER_NAME}"

COPY default.conf /etc/nginx/conf.d/default.conf
