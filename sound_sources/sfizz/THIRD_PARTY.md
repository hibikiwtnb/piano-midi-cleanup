# Third-party notices for the bundled sfizz runtime

`native/libsfizz.dylib` is sfizz 1.2.3 (BSD-2-Clause; see `../LICENSE`). The
library statically includes the third-party components below. Their full
license texts are kept in `third_party/`.

| Component | License | Notice file |
| --- | --- | --- |
| Abseil | Apache-2.0 | `third_party/abseil.txt` |
| atomic_queue | MIT | `third_party/atomic_queue.txt` |
| cephes | Netlib/BSD-compatible; permission documented in the notice | `third_party/cephes.txt` |
| cpuid | BSD-3-Clause | `third_party/cpuid.txt` |
| dr_libs | MIT-0 / public-domain choice | `third_party/dr_libs.txt` |
| ghc::filesystem | MIT | `third_party/filesystem.txt` |
| fmidi | Boost Software License 1.0 | `third_party/fmidi.txt` |
| hiir | WTFPL v2 | `third_party/hiir.txt` |
| Kiss FFT | BSD-3-Clause | `third_party/kiss_fft.txt` |
| libaiff | MIT-style | `third_party/libaiff.txt` |
| pugixml | MIT | `third_party/pugixml.txt` |
| SIMDe | MIT | `third_party/simde.txt` |
| spline | BSD-3-Clause | `third_party/spline.txt` |
| st_audiofile | BSD-2-Clause | `third_party/st_audiofile.txt` |
| stb_vorbis | MIT / public-domain choice | `third_party/stb_vorbis.txt` |
| tunings | MIT | `third_party/tunings.txt` |
| WavPack | BSD-3-Clause | `third_party/wavpack.txt` |

The list follows the sfizz 1.2.3 source tree. Rebuild the dylib from a newer
sfizz revision and refresh these notices if the bundled dependency set changes.
