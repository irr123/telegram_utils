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
	# wg-quick up wg-tor
	python ./puller_forwarder.py

.PHONY: release
release: format lint
	docker build --platform linux/amd64 . -f Dockerfile -t c1rno/private:latest
	docker tag c1rno/private:latest c1rno/private:tg26
	docker push c1rno/private:tg26
