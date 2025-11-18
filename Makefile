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
	docker build . -f Dockerfile -t c1rno/private:tg8
	docker push c1rno/private:tg8
	# tar --exclude='./.git' \
	# 	-czvf /tmp/app.tar.gz .
	# rsync -avz --progress /tmp/app.tar.gz 95.142.47.115:/root
	# and on host: rm -rf /root/telegram_utils/* && tar -xzvf /root/app.tar.gz -C /root/telegram_utils
