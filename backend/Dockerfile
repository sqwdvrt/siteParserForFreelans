# Backend: Go API, Crawler, Notifier
FROM golang:1.25.9-alpine3.22@sha256:2c16ac01b3d038ca2ed421d66cea489e3cb670c251b4f8bbcfad2ebfb75f884c AS builder

WORKDIR /build

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o /app/api ./cmd/api && \
    CGO_ENABLED=0 go build -o /app/crawler ./cmd/crawler && \
    CGO_ENABLED=0 go build -o /app/notifier ./cmd/notifier

FROM alpine:3.22@sha256:55ae5d250caebc548793f321534bc6a8ef1d116f334f18f4ada1b2daad3251b2

RUN apk add --no-cache ca-certificates tzdata postgresql16-client curl

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
