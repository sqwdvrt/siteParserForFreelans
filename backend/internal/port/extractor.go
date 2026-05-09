package port

import "github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"

type Extractor interface {
	ExtractList(html []byte) ([]string, error)
	ExtractDetail(html []byte, url string) (*domain.Job, error)
}
