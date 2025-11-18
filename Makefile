include $(PWD)/.env
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

IMAGE = tg_puller_forwarder

.PHONY: docker_run
docker_run:
	docker run -d\
		--name $(IMAGE)\
		--restart always\
		-v $(PWD)/.env:/opt/app/.env\
		-v $(PWD)/credentials.json:/opt/app/credentials.json\
		c1rno/private:tg6 sh -c "\
			echo 'TorAddress 172.17.0.1' > /etc/tor/torsocks.conf &&\
			echo 'TorPort 9050' >> /etc/tor/torsocks.conf &&\
			echo 'AllowOutboundLocalhost 1' >> /etc/tor/torsocks.conf &&\
			make run"

.PHONY: docker_stop
docker_stop:
	docker rm -f $(IMAGE) || true

.PHONY: release
release: fmt lint
	docker build . -f Dockerfile -t c1rno/private:tg6
	docker push c1rno/private:tg6
	# tar --exclude='./.git' \
	# 	-czvf /tmp/app.tar.gz .
	# rsync -avz --progress /tmp/app.tar.gz 95.142.47.115:/root
	# and on host: rm -rf /root/telegram_utils/* && tar -xzvf /root/app.tar.gz -C /root/telegram_utils
