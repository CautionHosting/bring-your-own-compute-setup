IMAGE_NAME := caution-managed-on-prem-aws-provisioner
REGISTRY := codeberg.org/caution
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo "latest")
OCI_TAR := build/oci/$(IMAGE_NAME).tar

.PHONY: build clean rebuild run publish

build:
	./build.sh
	docker load < $(OCI_TAR)

clean:
	rm -rf build/
	docker rmi $(IMAGE_NAME) 2>/dev/null || true

rebuild: clean build

run: build
	docker run --rm \
		--env-file .env \
		-v "$(PWD)/out:/out" \
		$(IMAGE_NAME)

publish: build
	docker load < $(OCI_TAR)
	docker tag $(IMAGE_NAME) $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
	docker tag $(IMAGE_NAME) $(REGISTRY)/$(IMAGE_NAME):latest
	docker push $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
	docker push $(REGISTRY)/$(IMAGE_NAME):latest
