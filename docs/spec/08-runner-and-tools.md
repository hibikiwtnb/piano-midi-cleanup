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
| 6 | 捲簾截圖 | 關 | `--snapshot landscape/portrait` |

### 重建語義

- 不給 `--bpm`／`--audio` 時沿用現有的 `song_bpmfix.mid`。
- 每次都從 `song_bpmfix.mid` 重新產生 `song_bpmfix_cleaned.mid`；關閉清理時直接複製。改設定重跑不會疊加前次結果，也不會多出 MIDI。
- 輸入可以是同一首的 `song.mid`、`song_bpmfix.mid` 或 `song_bpmfix_cleaned.mid`。

### 不成立的組合

執行前檢查，不成立時不執行：

- 可彈性清理只適用兩隻手。
- 截斷必須在分手之後。

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

主面板的「關於」分頁位於詳細報告右側，提供開發版本、專案首頁、問題回報、本專案 AGPLv3 授權全文，以及隨程式附帶的 Coopiano、Tonal.js、Essentia.js 的來源和本地授權全文。`src/app_info.py` 統一保存軟體名稱、版本、授權識別碼及元件資訊；目前版本為 `0.1.0.dev0`，不代表正式發布。本專案原創程式碼採用 `AGPL-3.0-only`，完整條款位於根目錄 `LICENSE`；第三方元件保留原有授權。

鋼琴捲簾優先載入同首歌的 `song_bpmfix_cleaned.mid` 供預覽及播放；尚無處理結果時才載入選取的輸入檔。「修改痕跡」下拉選單提供不顯示、被刪除的音符、被截短的音尾、全部顯示，預設不顯示。刪音以淡紅虛線方塊顯示，截掉的音尾以淡橙色顯示；保留的音符仍依手別著色，播放內容不受疊圖影響。滑鼠停在痕跡上時，底部狀態欄提示其處理環節、音名與力度。

`postprocess.py` 會將規則清理、雙手簡化及重疊截斷的逐環節音符差異寫入最終 MIDI 旁的同名 `.changes.json`，每次完整處理覆寫。紀錄綁定最終 MIDI 的 SHA-256；MIDI 經其他工具修改或缺少紀錄時，卷簾提示無對應紀錄，不推測舊版的刪音位置。重新處理會建立新紀錄，不會修改既有 MIDI 來補歷史。

捲簾播放預設使用 Coopiano，輸出選單亦保留 MIDI 埠；兩者共用 `midi_playback.py` 的事件排程。`coopiano_output.py` 在背景初始化音源，透過原生橋接與 sounddevice 將 PCM 送到系統預設音訊裝置；初始化完成前不開始播放時鐘。暫停與切換輸出會停止事件並清除內建音源的延音。選擇以 `playback_output` 儲存，未設定時為 Coopiano。

| 腳本 | 用途 |
|---|---|
| `windows/compare_pedal_trim.py` | 以既有測試曲比較舊版截斷與目前截斷，並檢查聲音提前量是否在容差內 |
| `windows/capture_gui.py` | 擷取 GUI 畫面供文件使用 |
| `windows/build_coopiano.cmd` | 建置隨專案分發的 Windows x64 Coopiano 播放 DLL；使用靜態 MSVC runtime |
