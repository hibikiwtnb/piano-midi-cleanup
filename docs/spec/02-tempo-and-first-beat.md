# 2. 速度與第一拍

## 目的

轉譜 MIDI 的時間軸是秒，沒有正確的拍子與小節。本步驟把 MIDI 改成正確的 BPM，並讓第一拍落在小節線上，之後的步驟才能用拍、小節思考。

輸出：`song_bpmfix.mid`。後續所有後處理都從這個檔案重建。

## BPM 修正

程式：`src/bpmfix_utils.py`（`apply_bpm_fix`、`fix_midi_bpm`）。

### BPM 來源

- **手動指定**：使用者給定 BPM，直接採用（四捨五入到小數 2 位）。
- **從音訊偵測**：Essentia `RhythmExtractor2013(method='multifeature')`。macOS／Linux 以共用環境的 Python 子程序執行，預設環境同 `~/.local/share/piano-midi-cleanup-env`，可用 `PIANO_MIDI_CLEANUP_BPM_ENV`／`PIANO_MIDI_CLEANUP_BPM_PYTHON` 指定其他環境；Windows 用 essentia.js 經 Node 執行。

偵測後的修正：

1. **相位校正**：在偵測值 ±1 BPM 內，以 0.0005 BPM 為步長，找使拍點在半拍解析度上最一致（相位相干性最高）的 BPM。半拍解析度可容忍拍點追蹤偶爾跳到反拍。
2. **取整**：若取整數 BPM 造成的曲尾漂移不超過 50 ms（`ROUND_MAX_END_DRIFT_MS`），用整數；否則保留小數 2 位。漂移 = |真實 BPM ÷ 套用 BPM − 1| × 曲長。

### 改寫 MIDI

依 MIDI 原本的速度（第一個 `set_tempo`，沒有則 120 BPM）把每個事件的 tick 換算到新速度，播放時間不變；`ticks_per_beat` 不變，全曲只留一個速度。

假設：轉譜 MIDI 為單一速度。

## 第一拍對齊

程式：`src/bpmfix_utils.py`（`estimate_first_beat`）、`src/first_beat.py`（`auto_align_first_beat`、`build_bpm_fixed_midi`）。

### 自動估計

| 步驟 | 方法 | 參數 |
|---|---|---|
| 八分音符相位 | 以力度加權的起音時間，在八分音符週期上求平均相位 | 力度相對全曲最大值加權 |
| 正拍或反拍 | 兩個候選相位中，落在四分音符網格附近（30 ms 內）的加權起音較多者為正拍 | `ON_BEAT_WINDOW_SECONDS = 0.03` |
| 第一拍 | 4 個拍位中，低音起音（左手或音高 ≤ 54）落在小節網格附近（40 ms 內）最多者為第一拍 | `DOWNBEAT_WINDOW_SECONDS = 0.04`、`BASS_MAX_PITCH = 54` |
| 位置 | 取最靠近 0 秒、在半小節內的小節線，已對齊的 MIDI 幾乎不會移動 | — |

報告記錄八分音符網格的相干性與 4 個拍位的低音比例，供判斷估計是否可信。

### 改寫 MIDI

把第一拍移到小節線上。若有音符因此移到 0 tick 之前，前面補整數個空小節（報告的 `Padding bars`）。

### 手動調整

主面板的鋼琴捲簾（第 8 章）可邊播放邊調 BPM 與第一拍，網格隨輸入即時預覽。捲簾顯示目前選取的檔案或明確產生的預覽檔，不會自動改用 `song_bpmfix_cleaned.mid`。

- 顯示原始 `song.mid` 時儲存：建立或覆寫同曲的 `song_bpmfix.mid`，不修改原檔，儲存後預覽切到 `song_bpmfix.mid`。
- 顯示 `song_bpmfix.mid` 或 `song_bpmfix_cleaned.mid` 時儲存：寫入 `song_bpmfix.mid` 並重寫調號；顯示的是 cleaned 時，它也以同一個位移量與調號改寫，修改痕跡紀錄跟著平移，不必重新處理；儲存後預覽切到 `song_bpmfix.mid`。
- 檔名以 `_bpmfix` 或 `_bpmfix_cleaned` 結尾時，對應的 `song_bpmfix.mid` 必須存在才能調整。

之後重跑時選「沿用 `song_bpmfix.mid`」可保留手動對齊；若從原始 `song.mid` 重算並執行自動第一拍，手動結果可能被取代。是否自動調整第一拍依使用者選擇的處理步驟執行；不根據報告判斷是否曾手動調整，也不因此阻擋重建。

## 報告

`MIDI BPMFix stats`、`MIDI auto first beat stats`、`MIDI first beat stats`（手動）。

## 已知限制

- 只支援單一速度；漸快、漸慢、自由速度段落的拍子會漂移。
- 只支援 4/4。
- 第一拍以低音起音判斷；低音不在正拍的編曲（如切分低音）可能判錯一拍。
- BPM 偵測可能得到半速或倍速，需要人工確認。
