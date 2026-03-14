# Backend: Go API, Crawler, Notifier
FROM golang:1.25-alpine@sha256:f6751d823c26342f9506c03797d2527668d095b0a15f1862cddb4d927a7a4ced AS builder

WORKDIR /build

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /app/api ./cmd/api && \
    CGO_ENABLED=0 go build -o /app/crawler ./cmd/crawler && \
    CGO_ENABLED=0 go build -o /app/notifier ./cmd/notifier

FROM alpine:3.20@sha256:a4f4213abb84c497377b8544c81b3564f313746700372ec4fe84653e4fb03805

RUN apk add --no-cache ca-certificates tzdata postgresql-client

WORKDIR /app

COPY --from=builder /app/api /app/crawler /app/notifier .
COPY migrations ./migrations
COPY scripts ./scripts
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN addgroup -S app && adduser -S -D -h /home/app -s /sbin/nologin -G app app && \
    mkdir -p /home/app /tmp && \
    chmod +x /docker-entrypoint.sh /app/scripts/railway-start.sh && \
    chown -R app:app /app /home/app /tmp

ENV HOME=/home/app
ENV TMPDIR=/tmp

USER app:app

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["./api"]
