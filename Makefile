-include $(PWD)/.env
export

.PHONY: format
format:
	ruff format $(PWD)

.PHONY: lint
lint:
	ruff check $(PWD) --fix

.PHONY: run
run:
	-wg-quick up wg-tor 2>/dev/null || true
	python ./puller_forwarder.py

VER := 34

.PHONY: release
release: format lint
	docker build --platform linux/amd64 . -f Dockerfile -t c1rno/private:latest
	docker tag c1rno/private:latest c1rno/private:tg$(VER)
	docker push c1rno/private:tg$(VER)
