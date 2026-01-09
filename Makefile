IMAGE_NAME := caution-provisioner-setup

.PHONY: build clean rebuild run

build:
	docker build -t $(IMAGE_NAME) .

clean:
	docker rmi $(IMAGE_NAME) 2>/dev/null || true

rebuild: clean build

run:
	docker run --rm \
		--env-file .env \
		-v "$(PWD)/out:/out" \
		$(IMAGE_NAME)
