# 8. 執行器、報告與檢查工具

## 統一執行器

程式：`src/postprocess.py`。依固定順序執行後處理，每一步可開關。

| 順序 | 步驟 | 預設 | 開關 |
|:---:|---|:---:|---|
| 1 | BPM 修正、第一拍對齊、調號 | 給 `--bpm` 或 `--audio` 時執行 | `--no-first-beat`、`--no-key-signature` |
| 2 | 規則清理 | 開 | `--no-clean`、`--semitone-pairs` |
| 3 | 可彈性清理 | 關 | `--playable` |
| 4 | 分手 | 開 | `--no-split`、`--hands 2/3/4` |
| 5 | 截斷重疊 | 開 | `--no-trim`、`--fast-figure-hands` |
| 6 | 捲簾截圖 | 關 | `--snapshot landscape/portrait/both` |

### 重建語義

- 不給 `--bpm`／`--audio` 時沿用現有的 `song_bpmfix.mid`。
- 每次都從 `song_bpmfix.mid` 重新產生 `song_bpmfix_cleaned.mid`；關閉清理時直接複製。改設定重跑不會疊加前次結果，也不會多出 MIDI。
- 輸入可以是同一首的 `song.mid`、`song_bpmfix.mid` 或 `song_bpmfix_cleaned.mid`。

### 不成立的組合

執行前檢查，不成立時不執行：

- 可彈性清理只適用兩隻手。
- 截斷必須在分手之後。
- `song_bpmfix_cleaned.mid` 上有手動調整的第一拍時，重建會讓它消失；需加 `--discard-cleaned-edits` 才繼續。

### 參數覆蓋

`--set 名稱=值`（可重複）覆蓋門檻，`--list-parameters` 列出全部可調參數與預設值。有改的值記在報告的步驟清單。各參數的意義與依據見各章參數表。

## GUI 面板

程式：`src/postprocess_panel.py`（tkinter）。選 MIDI、BPM 來源、各步驟開關與按主題分頁的參數；面板把選擇轉成 `postprocess.py` 的命令列執行，並先印出該命令。

- 開關與參數可存成預設組合（例如「一人彈」「三手」）。上次的選擇與預設組合存在 `~/.piano-midi-cleanup-panel.json`。
- 「詳細報告」以項目／值兩欄顯示 `song_stats.txt`，介面語言為英文、繁體中文、簡體中文（`src/gui_i18n.py`）。

### 鋼琴捲簾

程式：`src/pianoroll_canvas.py`（`PianoRollView`），主面板的一個分頁，不另開視窗。

| 項目 | 內容 |
|---|---|
| 載入 | 顯示目前選的 MIDI；切換到此頁、改選 MIDI 或處理完成時重新載入，檔案沒變就不重讀；同一檔重新載入時保留捲動位置與縮放 |
| 音符 | 依聲道上色：右手藍、第二右手橙、第二左手紫、左手綠（與 `pianoroll_snapshot.py` 共用顏色）；深淺表示力度，可關閉 |
| 軸 | 上方小節號，左側音名，網格為小節、拍、十六分音符 |
| 踏板 | 底部畫 CC64 踩下的區間；各手踏板相同時合併為一列 |
| 播放 | `src/midi_playback.py` 送到 MIDI 輸出埠；可依手靜音；處理開始前停止播放；選的輸出埠記在面板設定 |
| 第一拍 | 速度與第一拍位置的網格即時預覽；儲存寫入 `song_bpmfix.mid` 並重寫調號（第 2 章） |
| 操作 | 點擊定位；Ctrl／Alt + 滾輪縮放，Shift + 滾輪水平捲動 |

捲簾只用於檢查與調第一拍，不編輯音符。

## 報告

程式：`src/report_utils.py`。所有步驟的統計寫進同一份 `song_stats.txt`，每一步一個區塊，標題為 `MIDI … stats`。

| 區塊 | 步驟 |
|---|---|
| `MIDI quality stats` | 原始 MIDI 統計 |
| `MIDI BPMFix stats` | BPM 修正 |
| `MIDI auto first beat stats`、`MIDI first beat stats` | 自動、手動第一拍 |
| `MIDI key signature stats` | 調號 |
| `MIDI rule cleaning stats` | 規則清理 |
| `MIDI playable reduction stats` | 可彈性清理 |
| `MIDI provisional hand split stats` | 分手 |
| `MIDI overlap trim stats` | 截斷 |
| `MIDI post-processing run` | 本次執行的步驟清單與參數覆蓋 |

更新規則：

- 同一區塊逐行更新：相同的行保留，值變更時更新，新增的項目加入，新版已不存在的項目移除。
- 其他區塊保留；重複的舊區塊只留最新一份；內容完全相同時不寫檔。
- 重跑 BPM 修正時整份報告重寫。沿用 `song_bpmfix.mid` 時，保留原始統計、BPM 修正、第一拍、調號區塊，其餘重建。

## 檢查工具

都不修改 MIDI，也不屬於預設流程。

| 工具 | 用途 |
|---|---|
| `src/midi_stats.py` | 音符數、極短音（< 0.05 秒）、低力度音（≤ 30）、音域、平均力度、最多同時按鍵數 |
| `src/note_text_review.py` | 每個起音組一行文字：小節、拍位、每個音的手、音名與長度（拍） |
| `src/pianoroll_snapshot.py` | 每 4 小節一張捲簾 PNG，顏色區分手；橫向或直向 |
| `src/generate_chord_review.py` | 開發中。輸出每拍的和弦候補表（Tonal.js 判斷） |

「最多同時按鍵數」依 MIDI 音長計算，不加踏板延音；上游音長已含延音時，這個數字會高估實際按鍵，不能單憑它判定彈不到。

## 開發用腳本

| 腳本 | 用途 |
|---|---|
| `windows/compare_pedal_trim.py` | 以既有測試曲比較舊版截斷與目前截斷，並檢查聲音提前量是否在容差內 |
| `windows/capture_gui.py` | 擷取 GUI 畫面供文件使用 |
