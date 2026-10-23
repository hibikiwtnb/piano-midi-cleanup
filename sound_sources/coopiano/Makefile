CXX ?= c++
CXXFLAGS ?= -O2

ifeq ($(shell uname -s),Darwin)
HOST_FLAGS = -Wno-deprecated-declarations
HOST_LIBS = -framework CoreMIDI -framework AudioToolbox -framework CoreFoundation -framework CoreAudio
PLAYBACK_LIBRARY = native/libcoopiano.dylib
PLAYBACK_FLAGS = -dynamiclib
else
PLAYBACK_LIBRARY = native/libcoopiano.so
PLAYBACK_FLAGS = -shared -fPIC
endif

HEADERS = instrument.hpp streaming_soundboard.hpp piano.hpp preset.hpp reverb.hpp \
          live_diagnostics.hpp live_bench.hpp piano_host.hpp piano_resampler.hpp

.PHONY: all clean playback
all: coopiano

coopiano: coopiano.cpp $(HEADERS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -pthread $(HOST_FLAGS) coopiano.cpp -o $@ $(LDFLAGS) $(LDLIBS) $(HOST_LIBS)

playback: $(PLAYBACK_LIBRARY)

$(PLAYBACK_LIBRARY): playback_bridge.cpp $(HEADERS)
	mkdir -p native
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -pthread $(PLAYBACK_FLAGS) playback_bridge.cpp -o $@ $(LDFLAGS) $(LDLIBS)

clean:
	rm -f coopiano
