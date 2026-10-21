# Pianotrans Run Tools
[English README](README_EN.md)

這個目錄包含增加了 Apple GPU / MPS 支持的 Pianotrans 轉錄入口，以及圍繞轉錄結果新增的 BPM 修正、MIDI 統計、保守規則清理、初步分手 / MIDI channel 標記、MIDI review 匯出工具。所有 `.command` 檔都可以直接雙擊執行。

## Main Pipeline

### `pianotrans.command`

完整轉錄流程入口。雙擊後直接選擇一個或多個音訊檔；MIDI 會寫在各音訊檔旁邊。

設備選擇順序：

```text
CUDA -> CPU
```

流程：

```text
Pianotrans inference
-> write original MIDI
-> write MIDI stats
-> detect BPM from source audio
-> write BPM-fixed MIDI
-> rule-based conservative cleanup
-> provisional hand split by MIDI channel
```

輸出檔案：

```text
song.mid
song_stats.txt
song_bpmfix.mid
song_bpmfix_cleaned.mid
```

`song_stats.txt` 會包含原始 MIDI 品質統計、BPMFix stats、cleaning stats，以及 hand-split stats。

如果 `song.mid` 已存在，流程不會重新轉錄；只會補缺失的 stats、BPM-fixed MIDI、cleaned MIDI。

### `pianotrans_mps.command`

保留的 Apple GPU / MPS 支持版入口，流程與 `pianotrans.command` 相同，但設備選擇順序為：

```text
MPS -> CUDA -> CPU
```

目前在 M2 MacBook Air 實測中，這個模型使用 MPS 反而比 CPU 慢，因此日常使用主入口 `pianotrans.command`。

## Standalone Tools

### `bpmfix.command`

BPM 修正，再在瀏覽器工作台調整 BPM 和第一拍。

使用方式：

1. 如果已知 BPM，在終端輸入 BPM；不知道就直接按 Return，再選擇原音訊檔讓 Essentia 偵測固定 BPM。
2. 選擇要修正的 MIDI，輸出 `*_bpmfix.mid`。
3. 瀏覽器自動打開 MIDI 工作台（沿用 vsti-webui-wes7-pcm 的介面）：鋼琴捲簾、BPM / 第一拍 / 畫面延遲輸入、跟隨、左右手獨奏與靜音、88 鍵 Pianometer。播放時 MIDI 即時送到 S-MU2000 發聲。
4. 調好 BPM 和第一拍後按「寫入 MIDI」，直接覆寫 `*_bpmfix.mid`；按「取消」則不修改。

工作台由 `first_beat_ui.py` 在 `127.0.0.1` 開一個暫時的本機 HTTP server，寫入或取消後自動結束，不會產生額外的 MIDI 檔。播放由 `mu2000_playback.py` 建立名為 `pianotrans-tools` 的虛擬 MIDI 埠，並在背景啟動 S-MU2000 的 `live`（無視窗）接收它，聲音從系統預設輸出播出，不需要先開 MU2000.app 或設定 IAC。

寫入沿用原 workbench 的修正：依頁面上的 BPM 重算 tick，再把第一拍移到第 1 小節開頭；有弱起音落到 0 之前時，在前面補整數小節。channel 不變。結果會追加到 `*_stats.txt`。

`pianotrans.command` pipeline 只做 BPM 修正，不開工作台。

自動偵測會用 Essentia 拍點的半拍相位一致性估計實際 BPM（對漏拍、反拍跳動不敏感），再檢查四捨五入成整數後曲尾的格線漂移：

```text
end_drift_ms = duration * |beat_phase_bpm / applied_bpm - 1| * 1000
```

漂移 <= 50 ms 時套用整數 BPM；超過時保留兩位小數。報告會輸出 Essentia 原始 BPM、beat-phase BPM 與 coherence、是否取整，以及 `Estimated end drift`。手動輸入的 BPM 會直接使用。

修正邏輯保留實際播放速度，只重算 MIDI ticks 和 tempo metadata：

```text
new_ticks = old_ticks * detected_bpm / 120
```

### `midi_stats.command`

只做 MIDI 品質統計。

使用方式：

1. 選擇 MIDI。
2. 輸出 `*_stats.txt`。

統計項目：

```text
Total notes
Very short notes (< 0.050s)
Low velocity notes (<= 30)
Lowest note
Highest note
Pitch span
Average velocity
Max polyphony
```

音高會同時輸出 MIDI note number 和英文音名，例如 `24 (C1)`。

### `clean_midi_rules.command`

只做第一階段保守規則清理。

使用方式：

1. 選擇 MIDI。
2. 輸出 `*_cleaned.mid`。
3. 輸出 `*_clean_report.txt`。

注意：獨立執行 `clean_midi_rules.command` 時會輸出 `*_clean_report.txt`；`pianotrans.command` pipeline 中，清理報告會追加寫入原本的 `song_stats.txt`。

清理規則只刪除高度疑似 artifact 的 note，不做音樂語義判斷、不分手、不量化、不判斷和弦/調性/旋律。

目前規則：

```text
duration < 0.03s
velocity < 20
velocity < 35 and duration < 0.08s
pitch outside 21-108
```

清理報告示例：

```text
MIDI rule cleaning stats
Input: song_bpmfix.mid
Output: song_bpmfix_cleaned.mid
Original notes: 1000
Removed notes: 35 (3.50%)
Kept notes: 965 (96.50%)
Rule removals:
- Duration < 0.030s: 12 (1.20%)
- Velocity < 20: 3 (0.30%)
- Velocity < 35 and duration < 0.080s: 18 (1.80%)
- Pitch outside 21-108: 2 (0.20%)
```

所有百分比都以 `Original notes` 為分母。

### `hand_split_channels.command`

只做初步左右手分配，直接在清理後 MIDI 上修改 note channel。

它不拆成多個 instrument track，也不刪除 note。

分配方式是成本最小化的 beam search：起音相差 50 ms 內的音視為同一組，每組由低到高排序後，只考慮「最低 k 個音給左手、其餘給右手」的分法，再在整首曲子上選總成本最低的路徑。成本包括：

```text
單手跨度：12 半音內不計，超過 14 半音大幅加重
音域：左手高於 D4、右手低於 G3 時依距離計算
移動速度：手的位置變化除以該手距離上一個起音的時間（空閒的手移動較便宜）
快速段落：該手已連續彈 3 個以上間隔 < 0.25 s 的單音時，每多一個同時音都加成本（右手快速單音中的雙音傾向交給空閒的左手）
交叉：左手最高音高於右手最低音
```

報告會輸出單手跨度超過 12 半音的組數，以及快速段落中仍為多音的次數。所有權重都是檔案開頭的常數。

channel 對應：

```text
MIDI channel 1 = right hand
MIDI channel 3 = left hand
```

對 `mido` 這類 0-based channel 的 library 來說：

```text
channel 0 = MIDI channel 1
channel 2 = MIDI channel 3
```

這一步主要是為了讓 Logic Pro 樂譜視圖可以直接用 Piano 1 / Piano 3 顯示左右手，方便人工檢查和編輯。

### `midi_workbench.command`

不經過 BPM 修正，直接用瀏覽器 MIDI 工作台打開任一 MIDI，調整 BPM 和第一拍（介面、S-MU2000 即時播放與寫入方式同 `bpmfix.command` 的工作台）。

使用方式：

1. 選擇 MIDI（通常是 `*_bpmfix.mid` 或 `*_bpmfix_cleaned.mid`）。
2. 在工作台調整後按「寫入 MIDI」，直接覆寫所選檔案；按「取消」則不修改。

結果只在終端和完成視窗顯示，不寫入 stats 檔。

### `generate_midi_review.py`

開發中：輸出內容尚未定案，不屬於預設流程，只在明確需要時手動執行。

只讀取已完成後處理的 MIDI，輸出英文 Markdown 格式的 score facts table。它不修改 MIDI。

命令：

```bash
python generate_midi_review.py input.mid
```

預設輸出：

```text
song_midi_review.md
```

目前輸出結構：

```text
Global Info
Notes
```

詳細規格見：

```text
MIDI_REVIEW_SPEC.md
```

### `generate_chord_review.command`

開發中：輸出內容尚未定案，不屬於預設流程，只在明確需要時手動執行。

只讀取已完成後處理的 MIDI，輸出英文 Markdown 格式的 vertical chord chart。它不修改 MIDI。

命令：

```bash
python generate_chord_review.py input.mid
```

預設輸出：

```text
song_chord_review.md
```

表格欄位：

```text
Bar
Beat
Chord
Bass
LH
RH
Range
NoteCount
```

每小節固定 4 行；如果同一拍內有多個起音事件，`Beat` 和 `Chord` 會用 `;` 分隔。和弦推定使用 vendored Tonal.js，邏輯對齊 pianometer 的 `Tonal.Chord.detect(notes, { assumePerfectFifth: true })`，每個事件最多保留前 3 個候補；無法推定時寫 `-`。

## Python Entrypoints

這些 `.command` 背後呼叫的 Python 腳本：

```text
pianotrans.py
pianotrans_mps.py
bpmfix.py
midi_stats_command.py
clean_midi_rules.py
hand_split_channels.py
midi_workbench.py
generate_midi_review.py
generate_chord_review.py
```

共用 helper：

```text
bpmfix_utils.py
first_beat_ui.py
mu2000_playback.py
web/result.js
web/result.css
midi_stats.py
detect_chords_tonal.js
```

## Dependencies

所有 `.command` 和 BPM 偵測共用同一個使用者層級 Python 環境（需要 tkinter，Homebrew 用 `python@3.11` 加 `python-tk@3.11`）：

```text
$HOME/.local/share/pianotrans-tools-env
```

`.command` 找不到這個環境時，才依序嘗試 `./.venv` 和系統 Python。

建立環境（Intel Mac 版本鎖定：PyTorch 最後的 macOS x86_64 版本是 2.2.2，需搭配 `numpy<2`；numba / llvmlite 新版沒有 x86_64 wheel）：

```bash
python3.11 -m venv ~/.local/share/pianotrans-tools-env
```

```bash
~/.local/share/pianotrans-tools-env/bin/python -m pip install "torch==2.2.2" "numpy<2" "numba==0.62.1" "llvmlite==0.45.1" "essentia==2.1b6.dev1389" mido librosa piano_transcription_inference "python-rtmidi==1.5.8"
```

ffmpeg 需在 PATH 上（librosa 讀取 m4a / mp3 等格式時使用）。

Tonal.js chord detection requires Node.js. The launcher searches `PATH` and common Node locations; `PIANOTRANS_NODE` can override it. 本機 Node 放在 `$HOME/.local/share/node-<version>-darwin-x64`，並以 `$HOME/.local/bin/node` 連結加入 PATH。

工作台的即時播放需要 S-MU2000 模擬器（含 `app/bin/live` 和 `roms`），預設位置 `$HOME/Music/S-MU2000-mac`，可用 `PIANOTRANS_MU2000_DIR` 覆蓋。虛擬 MIDI 埠需要 `python-rtmidi`（已列在安裝指令中）。

Vendored Tonal.js:

```text
vendor/tonal.min.js
```

模型權重預設位置：

```text
$HOME/piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth
```

可覆蓋的環境變數：

```text
PIANOTRANS_BPM_ENV
PIANOTRANS_BPM_PYTHON
PIANOTRANS_MU2000_DIR
```

## Notes

- `pianotrans.py` 會依序嘗試 CUDA、CPU。
- `pianotrans_mps.py` 會依序嘗試 MPS、CUDA、CPU；在 Apple Silicon 且 PyTorch MPS 可用時會使用 Apple GPU。
- BPM 修正以固定 BPM 為前提，不建立 tempo map。
- 規則清理是低風險 preprocessing；後續更複雜的判斷應放在 feature extraction 或 ML classifier 等更高階流程中。
