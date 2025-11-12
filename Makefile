include $(PWD)/.env

.PHONY: fmt
fmt:
	ruff format $(PWD)

.PHONY: lint
lint:
	ruff check $(PWD) --fix

.PHONY: run
run:
	@TG_API_ID=$(TG_API_ID)\
		TG_API_HASH=$(TG_API_HASH)\
		SESSION=$(SESSION)\
		GEMINI_API_KEY=$(GEMINI_API_KEY)\
		CALENDAR_ID=$(CALENDAR_ID)\
		python ./puller_forwarder.py

.PHONY: session
session:
	@TG_API_ID=$(TG_API_ID)\
		TG_API_HASH=$(TG_API_HASH)\
		node ./puller_forwarder/store_tg_session.mjs


IMAGE = tg_puller_forwarder

.PHONY: docker_run
docker_run:
	docker build . -t $(IMAGE)
	docker run -d\
		--name $(IMAGE)\
		--restart always\
		-v $(PWD)/.env:/opt/app/.env\
		-v $(PWD)/credentials.json:/opt/app/credentials.json\
		$(IMAGE) sh -c "cd /opt/app && make run"

.PHONY: docker_stop
docker_stop:
	docker stop $(IMAGE) || true
	docker rm $(IMAGE)

.PHONY: release
release: fmt lint
	docker build . -t c1rno/private:tg5
	docker push c1rno/private:tg5
	# tar --exclude='./.git' \
	# 	-czvf /tmp/app.tar.gz .
	# rsync -avz --progress /tmp/app.tar.gz cryptopeer.trade:/root
	# and on host: rm -rf /root/telegram_utils/* && tar -xzvf /root/app.tar.gz -C /root/telegram_utils
