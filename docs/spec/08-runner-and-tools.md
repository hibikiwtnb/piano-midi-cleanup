# 8. 執行器、報告與檢查工具

## 統一執行器

程式：`src/postprocess.py`。依固定順序執行後處理，每一步可開關。

| 順序 | 步驟 | 預設 | 開關 |
|:---:|---|:---:|---|
| 1 | BPM 修正、第一拍對齊、調號 | 給 `--bpm` 或 `--audio` 時執行 | `--no-first-beat`、`--no-key-signature` |
| 2 | 規則清理 | 開 | `--no-clean`、`--semitone-pairs` |
| 3 | 可彈性清理（兩手時；三手改在分手後） | 關 | `--playable` |
| 4 | 分手 | 開 | `--no-split`、`--hands 2/3/4` |
| 5 | 截斷重疊 | 開 | `--no-trim`、`--fast-figure-hands`、`--no-pedal-protection` |
| 6 | 捲簾截圖 | 關 | `--snapshot landscape/portrait` |

### 重建語義

- 不給 `--bpm`／`--audio` 時沿用現有的 `song_bpmfix.mid`。
- 每次都從 `song_bpmfix.mid` 重新產生 `song_bpmfix_cleaned.mid`；關閉清理時直接複製。改設定重跑不會疊加前次結果，也不會多出 MIDI。
- 輸入可以是同一首的 `song.mid`、`song_bpmfix.mid` 或 `song_bpmfix_cleaned.mid`。

### 不成立的組合

執行前檢查，不成立時不執行：

- `--bpm` 必須是正數、有限，且能表示為 MIDI tempo；無效值在執行前報錯。
- 可彈性清理：兩隻手時在分手前；三隻手時在分手後，只處理第一個人的 left 和 second_right；四隻手不支援。兩手時，清理的每手音數與跨度限制也會套用到分手，未解決的組保留原樣。
- 截斷必須在分手之後。

### 參數覆蓋

`--set 名稱=值`（可重複）覆蓋門檻，`--list-parameters` 列出全部可調參數與預設值。有改的值記在報告的步驟清單。各參數的意義與依據見各章參數表。

## GUI 面板

程式：`src/postprocess_panel.py`（tkinter）。選 MIDI、BPM 來源、各步驟開關與按主題分頁的參數；面板把選擇轉成 `postprocess.py` 的命令列執行，並先印出該命令。

- 開關與參數可存成預設組合（例如「一人彈」「三手」）。上次的選擇與預設組合存在 `~/.piano-midi-cleanup-panel.json`。
- 選擇四隻手時，自動取消並停用可彈性清理；切回兩或三隻手時恢復可選，不自動勾選。載入上次設定或預設組合時同樣檢查。其他不成立的步驟組合在啟動背景處理前提示錯誤。BPM 為空白、非數字、非有限或超出 MIDI tempo 範圍時同樣顯示錯誤，不會結束面板。
- 「詳細報告」以項目／值兩欄顯示 `song_stats.txt`，介面語言為英文、繁體中文、簡體中文（`src/gui_i18n.py`）。
- 首次開啟內容區為 1280 × 720；之後記住一般視窗大小、位置與最大化狀態。處理頁左右面板填滿可用高度，「開始處理」只在處理步驟框的右下角。
- 「依踏板狀態限制截斷」在面板預設關閉，選擇隨預設組合保存；命令列預設不同，見第 7 章。

### 鋼琴捲簾

程式：`src/pianoroll_canvas.py`（`PianoRollView`），主面板的一個分頁，不另開視窗。

| 項目 | 內容 |
|---|---|
| 載入 | 優先顯示同曲的 `song_bpmfix_cleaned.mid`，尚無處理結果時才顯示選取的輸入檔；切換到此頁或檔案變更時重載，同一檔重載保留捲動與縮放，新曲依可見音域置中 |
| 音符 | 依聲道上色：右手藍、第二右手橙、第二左手紫、左手綠（與 `pianoroll_snapshot.py` 共用顏色）；深淺表示力度，可關閉 |
| 軸 | 上方小節號，首次調號與轉調處標示 `Key=C Major`／`Key=A minor`；左側音名，網格為小節、拍、十六分音符 |
| 和弦 | 小節尺下方的和弦軌，工具列可收起；背景分析，不改寫 MIDI；滑鼠提示其他讀法、低音與組成音 |
| 踏板 | 底部畫 CC64 踩下的區間；各手踏板相同時合併為一列 |
| 播放 | 單一播放／暫停按鈕；預設由 Coopiano 合成至系統預設音訊輸出，仍可選本機 MIDI 埠；可依手靜音，順序為左手、右手、第二左手、第二右手；處理開始前停止播放，輸出選擇保存於面板設定 |
| 第一拍 | 速度與第一拍位置的網格即時預覽；儲存寫入 `song_bpmfix.mid` 與顯示中的 `song_bpmfix_cleaned.mid`，並重寫調號（第 2 章） |
| 操作 | 點擊定位；焦點在卷簾時空白鍵播放／暫停；切換分頁不自動聚焦輸入框；Ctrl／Alt + 滾輪縮放，Shift + 滾輪水平捲動 |
| 提示 | 所有音符懸停時，底部狀態欄顯示音名與力度；刪除／截短理由另用滑鼠提示框，現有理由只記錄處理環節 |

捲簾只用於檢查與調第一拍，不編輯音符。

### 修改痕跡

「修改痕跡」下拉選單位於播放控制列，提供不顯示、被刪除的音符、被截短的音尾、全部顯示，預設不顯示。刪音以淡紅虛線方塊顯示，截掉的音尾以淡橙色顯示；保留的音符仍依手別著色，疊圖不影響播放。

`postprocess.py` 將規則清理、雙手簡化及重疊截斷的逐環節差異寫入最終 MIDI 旁的同名 `.changes.json`，每次完整處理覆寫。紀錄綁定最終 MIDI 的 SHA-256；外部修改或缺少紀錄時不推測舊版刪音位置。卷簾同步儲存第一拍時會平移痕跡並更新指紋，其他工具修改造成的不符仍拒絕載入。

### 和弦分析

`src/chord_analysis.py` 以半拍為格累計音級發聲長度，納入低音與踏板延音，再由移植自 Magenta note-seq 的 `src/chord_inference.py` 以 HMM／Viterbi 推斷和弦段落。段落由 Tonal.js 命名；弱起不標和弦，低音非根音時可顯示轉位。需要 Node.js，缺少時在和弦軌提示，其他功能照常。分析在背景執行，依檔案與修改時間快取；結果只供檢視，未接入刪音規則。

### 音源與關於

`src/coopiano_output.py` 在背景初始化 Coopiano，按需準備擊弦資料，透過原生橋接與 sounddevice 輸出；初始化完成前不開始播放時鐘。停止或切換輸出會清除內建音源的延音。Windows x64 DLL 與 macOS 通用庫已附帶，Linux 需本機建置；建置與授權詳見 `sound_sources/README.md`。RDP 需有可用的系統播放裝置，不能把編譯成功視為已驗證聽感。

「關於」分頁位於詳細報告右側，提供版本、專案首頁、問題回報、AGPLv3 授權全文，以及 Coopiano、Tonal.js、note-seq、Essentia.js 的來源和本地授權全文。`src/app_info.py` 統一保存名稱、版本、授權及元件資訊；目前為 `1.0.1`，分頁顯示 `Version: {}`。本專案原創程式碼採用 `AGPL-3.0-only`，第三方元件保留原有授權。

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
- 執行器先在最終報告旁建立暫存檔並寫入，全部步驟成功後才替換最終報告；任何步驟失敗時保留原報告並刪除暫存檔。
- 重跑 BPM 修正時整份報告重寫。沿用 `song_bpmfix.mid` 時，保留原始統計、BPM 修正、第一拍、調號區塊，其餘重建。

## 檢查工具

都不修改 MIDI，也不屬於預設流程。

| 工具 | 用途 |
|---|---|
| `src/midi_stats.py` | 音符數、極短音（< 0.05 秒）、低力度音（≤ 30）、音域、平均力度、最多同時按鍵數 |
| `src/note_text_review.py` | 每個起音組一行文字：小節、拍位、每個音的手、音名與長度（拍） |
| `src/pianoroll_snapshot.py` | 每 4 小節一張捲簾 PNG，顏色區分手；橫向或直向 |
| `src/generate_chord_review.py` | 開發中。輸出每拍的和弦候補表（Tonal.js 判斷） |
| `src/chord_analysis.py` | 現行卷簾和弦分析，也可由命令列輸出和弦段落；與舊候補表工具不同 |

「最多同時按鍵數」依 MIDI 音長計算，不加踏板延音；上游音長已含延音時，這個數字會高估實際按鍵，不能單憑它判定彈不到。

## 開發用腳本

| 腳本 | 用途 |
|---|---|
| `windows/compare_pedal_trim.py` | 以既有測試曲比較舊版截斷與目前截斷，並檢查聲音提前量是否在容差內 |
| `windows/capture_gui.py` | 擷取 GUI 畫面供文件使用 |
| `windows/build_coopiano.cmd` | 建置隨專案分發的 Windows x64 Coopiano 播放 DLL；使用靜態 MSVC runtime |
| `windows/create_shortcut.cmd` | 產生本機的根目錄啟動快捷方式，使用 pythonw 不顯示命令列；快捷方式不納入版本控制 |
| `macos/create_app.command` | 產生本機的 macOS App 啟動入口，不開 Terminal；產生前先檢查面板能否載入，執行中的輸出寫入 `~/Library/Logs/Piano MIDI Cleanup/panel.log`，失敗時顯示錯誤對話框；入口不納入版本控制 |
