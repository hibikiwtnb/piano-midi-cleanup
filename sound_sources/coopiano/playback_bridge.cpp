// Small C ABI for the existing physical model. Audio devices belong to Python.
#include "instrument.hpp"
#include "piano_host.hpp"
#include "preset.hpp"
#include <fstream>
#include <filesystem>
#include <string>
#include <atomic>

#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C" __attribute__((visibility("default")))
#endif

namespace {
thread_local std::string last_error;
constexpr size_t maximum_frames = 2048;
template <typename T, size_t N>
struct SpscRing {
    std::array<T, N> items{};
    std::atomic<size_t> write{0}, read{0};
    bool has_space() const {
        return write.load(std::memory_order_relaxed) - read.load(std::memory_order_acquire) < N;
    }
    bool push(const T& value) {
        const auto index = write.load(std::memory_order_relaxed);
        if (index - read.load(std::memory_order_acquire) >= N) return false;
        items[index % N] = value;
        write.store(index + 1, std::memory_order_release);
        return true;
    }
    bool pop(T& value) {
        const auto index = read.load(std::memory_order_relaxed);
        if (index == write.load(std::memory_order_acquire)) return false;
        value = items[index % N];
        read.store(index + 1, std::memory_order_release);
        return true;
    }
};
struct Command {
    std::array<unsigned char, 3> data{};
    instrument::PreparedStrike* strike = nullptr;
};
struct Piano {
    instrument::Engine engine;
    piano_host::Adapter adapter;
    std::array<float, maximum_frames> left{}, right{};
    uint64_t sequence = 1;
    SpscRing<Command, 1024> commands;
    SpscRing<instrument::PreparedStrike*, 1024> retired;
    Piano(const instrument::Settings& settings, double sample_rate) : engine(settings), adapter(engine) {
        adapter.prepare(sample_rate, maximum_frames);
    }
    ~Piano() {
        Command command;
        while (commands.pop(command)) delete command.strike;
        reclaim();
    }
    void reclaim() {
        instrument::PreparedStrike* strike;
        while (retired.pop(strike)) delete strike;
    }
};
}

API const char* coopiano_error() { return last_error.c_str(); }

API void* coopiano_create(const char* preset_path, double sample_rate) {
    try {
        last_error.clear();
        std::ifstream input(std::filesystem::u8path(preset_path));
        if (!input) throw std::runtime_error("Could not open the Coopiano preset");
        auto preset = preset::read(input);
        instrument::Settings settings;
        settings.voicing = preset.voicing;
        settings.reverb = preset.reverb;
        return new Piano(settings, sample_rate);
    } catch (const std::exception& error) {
        last_error = error.what();
        return nullptr;
    } catch (...) {
        last_error = "Could not initialize Coopiano";
        return nullptr;
    }
}

API void coopiano_destroy(void* handle) { delete static_cast<Piano*>(handle); }

// One preparation worker owns this call; allocation and contact solving stay off audio.
API int coopiano_send(void* handle, const unsigned char* commands, unsigned command_count) {
    try {
        auto& piano = *static_cast<Piano*>(handle);
        piano.reclaim();
        for (unsigned i = 0; i < command_count; ++i) {
            if (!piano.commands.has_space()) return 0;
            const auto* data = commands + i * 3;
            Command command{{data[0], data[1], data[2]}, nullptr};
            if ((data[0] & 0xf0) == 0x90 && data[2])
                command.strike = new instrument::PreparedStrike(
                    piano.engine.prepare(data[1], data[2] / 127.0, piano.sequence++));
            if (!piano.commands.push(command)) { delete command.strike; return 0; }
        }
        return 1;
    } catch (...) { return 0; }
}

// One audio owner consumes prepared strikes; their storage is reclaimed by the worker.
API int coopiano_render(void* handle, float* interleaved, unsigned frames) {
    try {
        if (!handle || !interleaved || frames > maximum_frames)
            return 0;
        auto& piano = *static_cast<Piano*>(handle);
        Command command;
        for (unsigned i = 0; i < 1024 && piano.retired.has_space() && piano.commands.pop(command); ++i) {
            const auto* data = command.data.data();
            const unsigned channel = data[0] & 15;
            if (data[0] == 0xff) { piano.adapter.reset(); continue; }
            switch (data[0] & 0xf0) {
                case 0x90:
                    if (command.strike) {
                        piano.engine.start(*command.strike, channel);
                        piano.retired.push(command.strike);
                    } else piano.engine.note_off(data[1], channel);
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
