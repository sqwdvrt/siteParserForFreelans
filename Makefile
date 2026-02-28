.PHONY: test-all full-check

test-all:
	bash ./scripts/full_check.sh

full-check: test-all
