FROM nginx:1.27-alpine

RUN apk add --no-cache openssl \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -nodes -days 365 \
        -newkey rsa:2048 \
        -keyout /etc/nginx/certs/marketstore.key \
        -out /etc/nginx/certs/marketstore.crt \
        -subj "/CN=169.58.152.33" \
        -addext "subjectAltName=IP:169.58.152.33"

COPY default.conf /etc/nginx/conf.d/default.conf
