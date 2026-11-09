SHELL := /bin/bash
.DEFAULT_GOAL := help
BUILD := build
SDK := $(shell xcrun --sdk macosx --show-sdk-path)

.PHONY: help build endpoint proxy start doctor devices test media keystore interface certificate decoder native support features app package
help:
	@printf '%s\n' 'make build           Build the macOS adapters.' 'make endpoint        Build the arm64 endpoint; signing occurs during backend setup.' 'make proxy       Forward the configured USB backend SSH connection.' 'make support  Prepare normal launches of the original Mirroring app for up to 30 minutes.' 'make native          Start the original app with the custom launch command.' 'make doctor          Inspect configured prerequisites.' 'make features Inspect notification settings and the iPhone-app catalog.' 'make test            Run regression tests.'

build: $(BUILD)/media.dylib $(BUILD)/keystore.dylib $(BUILD)/inspector

$(BUILD)/sockets.dylib: src/mirroring/sockets.c | $(BUILD)/.dir
	xcrun clang -dynamiclib -Wall -Wextra -Werror "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/launch.dylib: src/mirroring/launch.m | $(BUILD)/.dir
	xcrun clang -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/marker.dylib: tests/marker.c | $(BUILD)/.dir
	xcrun clang -dynamiclib -Wall -Wextra -Werror "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/capabilities: src/tools/capabilities.m | $(BUILD)/.dir
	xcrun clang -fobjc-arc -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"

features: $(BUILD)/capabilities
	python3 scripts/features.py

package: app
	python3 scripts/package.py

app: build endpoint $(BUILD)/sockets.dylib $(BUILD)/launch.dylib $(BUILD)/decoder-route.dylib
	python3 scripts/bundle.py

$(BUILD)/interface: tests/interface.m src/app/app.m src/app/wizard.m src/app/wizard.h src/app/interface.m src/app/interface.h src/app/logs.m src/app/logs.h src/app/alerts.m src/app/alerts.h src/app/authorization.m src/app/authorization.h src/app/channel.h | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework AppKit tests/interface.m src/app/wizard.m src/app/interface.m src/app/logs.m src/app/alerts.m src/app/authorization.m -o "$@"

interface: $(BUILD)/interface
	python3 scripts/checkinterface.py

$(BUILD)/certificateprobe: tests/certificate.m src/phone/endpoint.m | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Security "$<" -o "$@"

certificate: $(BUILD)/certificateprobe
	$(BUILD)/certificateprobe

$(BUILD)/.dir:
	mkdir -p "$(@D)"
	touch "$@"

$(BUILD)/media.dylib: src/mirroring/media.m src/mirroring/profile.m src/mirroring/compatibility.m src/mirroring/profile.h | $(BUILD)/.dir
	xcrun clang -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Network $(filter %.m,$^) -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/inspector: src/tools/inspect.m src/mirroring/compatibility.m src/mirroring/profile.h | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Network $(filter %.m,$^) -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/mediaprobe: tests/media.m | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation "$<" -o "$@"

$(BUILD)/mismatch.dylib: tests/mismatch.c | $(BUILD)/.dir
	xcrun clang -dynamiclib -Wall -Wextra -Werror -lobjc "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/keystore.dylib: src/mirroring/keystore.m | $(BUILD)/.dir
	xcrun clang -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -O1 -F "$(SDK)/System/Library/PrivateFrameworks" -framework Foundation -framework AppleKeyStore -framework MobileActivationMacOS -L "$(SDK)/usr/lib/swift" -lswiftCore "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/keyprobe: tests/keystore.m src/mirroring/keystore.m | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -O1 -F "$(SDK)/System/Library/PrivateFrameworks" -framework Foundation -framework AppleKeyStore -framework MobileActivationMacOS -L "$(SDK)/usr/lib/swift" -lswiftCore "$<" -o "$@"

$(BUILD)/endpointprobe: tests/endpoint.m src/phone/endpoint.m | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Foundation -framework Security "$<" -o "$@"

endpoint: | $(BUILD)/.dir
	python3 scripts/endpoint.py

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

media: $(BUILD)/media.dylib $(BUILD)/inspector $(BUILD)/mediaprobe $(BUILD)/mismatch.dylib
	python3 tests/checkmedia.py

keystore: $(BUILD)/keyprobe $(BUILD)/endpointprobe
	python3 tests/checkkeystore.py
	$(BUILD)/endpointprobe

native: build $(BUILD)/sockets.dylib $(BUILD)/decoder-route.dylib
	python3 scripts/native.py

support: build $(BUILD)/sockets.dylib $(BUILD)/launch.dylib $(BUILD)/decoder-route.dylib
	python3 scripts/native.py --launch-services

$(BUILD)/decoder-route.dylib: src/mirroring/decoder-route.m | $(BUILD)/.dir
	xcrun clang -dynamiclib -fobjc-arc -Wall -Wextra -Werror -F "$(SDK)/System/Library/PrivateFrameworks" -framework Foundation -framework VideoToolbox -framework CoreMedia -framework CoreVideo -framework VideoProcessing "$<" -o "$@"
	codesign --force --sign - "$@"

$(BUILD)/decoderprobe: tests/decoder.m src/mirroring/decoder-route.m | $(BUILD)/.dir
	xcrun clang -fobjc-arc -Wall -Wextra -Werror -framework Foundation -framework VideoToolbox -framework CoreMedia -framework CoreVideo "$<" -o "$@"

decoder: $(BUILD)/decoderprobe
	$(BUILD)/decoderprobe

$(BUILD)/password: src/app/password.m src/app/channel.h | $(BUILD)/.dir
	xcrun clang -fobjc-arc -Wall -Wextra -Werror -framework AppKit src/app/password.m -o "$@"


$(BUILD)/authorization: tests/authorization.m src/app/authorization.m src/app/authorization.h src/app/channel.h | $(BUILD)/.dir
	xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework AppKit tests/authorization.m src/app/authorization.m -o "$@"

authorization: $(BUILD)/authorization $(BUILD)/password
	$(BUILD)/authorization $(BUILD)/password
