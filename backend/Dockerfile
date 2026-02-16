# Backend: Go API, Crawler, Notifier
FROM golang:1.25-alpine AS builder

WORKDIR /build

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /app/api ./cmd/api && \
    CGO_ENABLED=0 go build -o /app/crawler ./cmd/crawler && \
    CGO_ENABLED=0 go build -o /app/notifier ./cmd/notifier

FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata postgresql-client

WORKDIR /app

COPY --from=builder /app/api /app/crawler /app/notifier .
COPY migrations ./migrations
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN addgroup -S app && adduser -S -D -h /home/app -s /sbin/nologin -G app app && \
    mkdir -p /home/app /tmp && \
    chmod +x /docker-entrypoint.sh && \
    chown -R app:app /app /home/app /tmp

ENV HOME=/home/app
ENV TMPDIR=/tmp

USER app:app

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["./api"]
