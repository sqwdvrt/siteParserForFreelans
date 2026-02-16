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
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["./api"]
