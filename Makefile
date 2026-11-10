SHELL := /bin/bash
.DEFAULT_GOAL := help
BUILD := build
SDK ?= $(shell xcrun --sdk macosx --show-sdk-path)
export MIRRA_MACOS_SDK := $(SDK)
HOST_FLAGS := $(shell python3 scripts/deployment.py --flags --sdk "$(SDK)")

.PHONY: help build endpoint proxy start doctor devices test media keystore interface certificate decoder native support features app package
help:
	@printf '%s\n' 'make build           Build the macOS adapters.' 'make endpoint        Build the arm64 endpoint; signing occurs during backend setup.' 'make proxy       Forward the configured USB backend SSH connection.' 'make support  Prepare normal launches of the original Mirroring app for up to 30 minutes.' 'make native          Start the original app with the custom launch command.' 'make doctor          Inspect configured prerequisites.' 'make features Inspect notification settings and the iPhone-app catalog.' 'make test            Run regression tests.'

build: $(BUILD)/media.dylib $(BUILD)/keystore.dylib $(BUILD)/inspector

$(BUILD)/sockets.dylib: src/mirroring/sockets.c $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -Wall -Wextra -Werror "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/launch.dylib: src/mirroring/launch.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/marker.dylib: tests/marker.c $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -Wall -Wextra -Werror "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/capabilities: src/tools/capabilities.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"

features: $(BUILD)/capabilities
	python3 scripts/features.py

package: app
	python3 scripts/package.py

app: build endpoint $(BUILD)/sockets.dylib $(BUILD)/launch.dylib $(BUILD)/decoder-route.dylib
	python3 scripts/bundle.py

$(BUILD)/interface: tests/interface.m src/app/app.m src/app/wizard.m src/app/wizard.h src/app/interface.m src/app/interface.h src/app/logs.m src/app/logs.h src/app/alerts.m src/app/alerts.h src/app/authorization.m src/app/authorization.h src/app/channel.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework AppKit tests/interface.m src/app/wizard.m src/app/interface.m src/app/logs.m src/app/alerts.m src/app/authorization.m -o "$@"

interface: $(BUILD)/interface
	python3 scripts/checkinterface.py

$(BUILD)/certificateprobe: tests/certificate.m src/phone/endpoint.m src/phone/keystore-symbols.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Security "$<" -o "$@"

certificate: $(BUILD)/certificateprobe
	$(BUILD)/certificateprobe

$(BUILD)/.dir:
	mkdir -p "$(@D)"
	touch "$@"

$(BUILD)/media.dylib: src/mirroring/media.m src/mirroring/awdl-service.m src/mirroring/awdl-service.h src/mirroring/profile.m src/mirroring/compatibility.m src/mirroring/profile.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Network $(filter %.m,$^) -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/inspector: src/tools/inspect.m src/mirroring/compatibility.m src/mirroring/profile.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Network $(filter %.m,$^) -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/mediaprobe: tests/media.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"

$(BUILD)/mismatch.dylib: tests/mismatch.c $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -Wall -Wextra -Werror -lobjc "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/keystore.dylib: src/mirroring/keystore.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -O1 -F "$(SDK)/System/Library/PrivateFrameworks" -framework Foundation -framework AppleKeyStore -framework MobileActivationMacOS -L "$(SDK)/usr/lib/swift" -lswiftCore "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/keyprobe: tests/keystore.m src/mirroring/keystore.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -O1 -F "$(SDK)/System/Library/PrivateFrameworks" -framework Foundation -framework AppleKeyStore -framework MobileActivationMacOS -L "$(SDK)/usr/lib/swift" -lswiftCore "$<" -o "$@"

$(BUILD)/endpointprobe: tests/endpoint.m src/phone/endpoint.m src/phone/keystore-symbols.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Security "$<" -o "$@"

endpoint: | $(BUILD)/.dir
	python3 scripts/endpoint.py

# Device self-check uses the production loader and the full iPhoneOS SDK.
$(BUILD)/endpoint-check: tests/endpoint-check.m src/phone/endpoint.m src/phone/keystore-symbols.h | $(BUILD)/.dir
	xcrun --sdk iphoneos clang -target arm64-apple-ios15.0 -isysroot "$(shell xcrun --sdk iphoneos --show-sdk-path)" -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Security "$<" -o "$@"

$(BUILD)/symbols: tests/symbols.m src/phone/endpoint.m src/phone/keystore-symbols.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Security "$<" -o "$@"

.PHONY: symbols
symbols: $(BUILD)/symbols $(BUILD)/endpointprobe
	$(BUILD)/symbols
	$(BUILD)/endpointprobe

proxy:
	python3 scripts/proxy.py

start:
	python3 scripts/backend.py

doctor:
	python3 scripts/doctor.py

devices:
	python3 scripts/doctor.py --devices

test:
	python3 -m unittest discover -s tests -v

media: awdl-service $(BUILD)/media.dylib $(BUILD)/inspector $(BUILD)/mediaprobe $(BUILD)/mismatch.dylib
	python3 tests/checkmedia.py

$(BUILD)/awdl-service: tests/awdl-service.m src/mirroring/awdl-service.m src/mirroring/awdl-service.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"

.PHONY: awdl-service
awdl-service: $(BUILD)/awdl-service
	$(BUILD)/awdl-service

keystore: $(BUILD)/keyprobe $(BUILD)/endpointprobe
	python3 tests/checkkeystore.py
	$(BUILD)/endpointprobe

native: build $(BUILD)/sockets.dylib $(BUILD)/decoder-route.dylib
	python3 scripts/native.py

support: build $(BUILD)/sockets.dylib $(BUILD)/launch.dylib $(BUILD)/decoder-route.dylib
	python3 scripts/native.py --launch-services

$(BUILD)/decoder-route.dylib: src/mirroring/decoder-route.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -dynamiclib -fobjc-arc -Wall -Wextra -Werror -F "$(SDK)/System/Library/PrivateFrameworks" -framework Foundation -framework VideoToolbox -framework CoreMedia -framework CoreVideo -framework VideoProcessing "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/decoderprobe: tests/decoder.m src/mirroring/decoder-route.m $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -Wall -Wextra -Werror -framework Foundation -framework VideoToolbox -framework CoreMedia -framework CoreVideo "$<" -o "$@"

decoder: $(BUILD)/decoderprobe
	$(BUILD)/decoderprobe

$(BUILD)/password: src/app/password.m src/app/channel.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -Wall -Wextra -Werror -framework AppKit src/app/password.m -o "$@"


$(BUILD)/authorization: tests/authorization.m src/app/authorization.m src/app/authorization.h src/app/channel.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework AppKit tests/authorization.m src/app/authorization.m -o "$@"

authorization: $(BUILD)/authorization $(BUILD)/password
	$(BUILD)/authorization $(BUILD)/password

.PHONY: FORCE
FORCE:

$(BUILD)/.host-target: FORCE | $(BUILD)/.dir
	python3 scripts/deployment.py --stamp "$@" --sdk "$(SDK)"

$(BUILD)/compatibilityprobe: tests/compatibility.m src/mirroring/compatibility.m src/mirroring/profile.h $(BUILD)/.host-target | $(BUILD)/.dir
	xcrun clang $(HOST_FLAGS) -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Network $(filter %.m,$^) -o "$@"

.PHONY: compatibility
compatibility: $(BUILD)/compatibilityprobe
	$(BUILD)/compatibilityprobe
