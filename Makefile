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
		GEMINI_API_KEY=$(GEMINI_API_KEY)\
		CALENDAR_ID=$(CALENDAR_ID)\
		python ./puller_forwarder.py

.PHONY: session
session:
	@TG_API_ID=$(TG_API_ID)\
		TG_API_HASH=$(TG_API_HASH)\
		python ./store_tg_session.py


IMAGE = tg_puller_forwarder

.PHONY: docker_run
docker_run:
	docker build . -t $(IMAGE)
	docker run -d\
		--name $(IMAGE)\
		--restart always\
		-v $(PWD)/.env:/opt/app/.env\
		-v $(PWD)/my.session:/opt/app/my.session\
		-v $(PWD)/credentials.json:/opt/app/credentials.json\
		$(IMAGE) sh -c "cd /opt/app && make run"

.PHONY: docker_stop
docker_stop:
	docker stop $(IMAGE) || true
	docker rm $(IMAGE)
