// Small C ABI for the existing physical model. Audio devices belong to Python.
#include "instrument.hpp"
#include "piano_host.hpp"
#include "preset.hpp"
#include <fstream>
#include <filesystem>
#include <string>

#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C" __attribute__((visibility("default")))
#endif

namespace {
thread_local std::string last_error;
constexpr size_t maximum_frames = 2048;
struct Piano {
    instrument::Engine engine;
    piano_host::Adapter adapter;
    std::array<float, maximum_frames> left{}, right{};
    uint64_t sequence = 1;
    Piano(const instrument::Settings& settings, double sample_rate) : engine(settings), adapter(engine) {
        engine.build_strike_cache();
        adapter.prepare(sample_rate, maximum_frames);
    }
};
}

API const char* coopiano_error() { return last_error.c_str(); }

API void* coopiano_create(const char* preset_path, double sample_rate) {
    try {
        last_error.clear();
        std::ifstream input(std::filesystem::u8path(preset_path));
        if (!input) throw std::runtime_error("Could not open the CoPiano preset");
        auto preset = preset::read(input);
        instrument::Settings settings;
        settings.voicing = preset.voicing;
        settings.reverb = preset.reverb;
        return new Piano(settings, sample_rate);
    } catch (const std::exception& error) {
        last_error = error.what();
        return nullptr;
    } catch (...) {
        last_error = "Could not initialize CoPiano";
        return nullptr;
    }
}

API void coopiano_destroy(void* handle) { delete static_cast<Piano*>(handle); }

// One audio owner calls this function; each command is three MIDI bytes.
API int coopiano_render(void* handle, float* interleaved, unsigned frames,
                        const unsigned char* commands, unsigned command_count) {
    try {
        if (!handle || !interleaved || frames > maximum_frames || (command_count && !commands))
            return 0;
        auto& piano = *static_cast<Piano*>(handle);
        for (unsigned i = 0; i < command_count; ++i) {
            const auto* data = commands + i * 3;
            const unsigned channel = data[0] & 15;
            if (data[0] == 0xff) { piano.adapter.reset(); continue; }
            switch (data[0] & 0xf0) {
                case 0x90:
                    piano.engine.note_on(data[1], data[2], piano.sequence++, channel);
                    break;
                case 0x80:
                    piano.engine.note_off(data[1], channel);
                    break;
                case 0xb0:
                    if (data[1] == 64) piano.engine.pedal(channel, data[2] / 127.0);
                    else if (data[1] == 120 || data[1] == 123) {
                        piano.engine.pedal(channel, 0.0);
                        for (int key = 0; key < 128; ++key) piano.engine.note_off(key, channel);
                    }
                    break;
            }
        }
        if (!piano.adapter.process(piano.left.data(), piano.right.data(), frames)) return 0;
        for (unsigned i = 0; i < frames; ++i) {
            interleaved[2*i] = piano.left[i];
            interleaved[2*i+1] = piano.right[i];
        }
        return 1;
    } catch (...) {
        return 0;
    }
}
