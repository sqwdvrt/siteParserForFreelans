package redis

// MapCarrier реализует propagation.TextMapCarrier над map[string]string.
// Используется для inject/extract W3C traceparent в Redis JSON-payload.
type MapCarrier map[string]string

func (c MapCarrier) Get(key string) string {
	return c[key]
}

func (c MapCarrier) Set(key, val string) {
	c[key] = val
}

func (c MapCarrier) Keys() []string {
	keys := make([]string, 0, len(c))
	for k := range c {
		keys = append(keys, k)
	}
	return keys
}
