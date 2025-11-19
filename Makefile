-include $(PWD)/.env
export

.PHONY: fmt
fmt:
	ruff format $(PWD)

.PHONY: lint
lint:
	ruff check $(PWD) --fix

.PHONY: run
run:
	@if command -v torsocks >/dev/null 2>&1; then \
		echo "Using torsocks..."; \
		torsocks python ./puller_forwarder.py; \
	else \
		echo "torsocks not found, running directly..."; \
		python ./puller_forwarder.py; \
	fi

.PHONY: session
session:
	node ./puller_forwarder/store_tg_session.mjs

.PHONY: release
release: fmt lint
	docker build . -f Dockerfile -t c1rno/private:latest
	docker tag c1rno/private:latest c1rno/private:tg12
	docker push c1rno/private:tg12
