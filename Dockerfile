FROM golang:1.25-alpine AS builder

WORKDIR /build/backend

COPY backend/go.mod backend/go.sum ./
RUN go mod download

COPY backend/. .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/api ./cmd/api

FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata postgresql-client

WORKDIR /app

COPY --from=builder /out/api /app/api
COPY --from=builder /build/backend/migrations /app/migrations
COPY --from=builder /build/backend/scripts/docker-entrypoint.sh /docker-entrypoint.sh

RUN addgroup -S app && adduser -S -D -h /home/app -s /sbin/nologin -G app app && \
    mkdir -p /home/app /tmp && \
    chmod +x /docker-entrypoint.sh && \
    chown -R app:app /app /home/app /tmp

ENV HOME=/home/app
ENV TMPDIR=/tmp
ENV PORT=8080

USER app:app

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["/bin/sh", "-c", "API_ADDR=:${PORT:-8080} /app/api"]
