# Piano MIDI Cleanup
[English README](README_EN.md)

把鋼琴 AI 轉錄出來的 MIDI 整理成能直接看譜、編輯的 MIDI：BPM 修正、第一拍對齊（主面板鋼琴捲簾即時播放）、調號、保守規則清理、分手（兩到四隻手，以 MIDI channel 標記）、踏板重疊裁短，和文字、捲簾截圖等檢查工具。這些步驟都以 MIDI 為輸入，不限定轉錄模型。

也附轉錄入口（`src/transcribe.py`，含 Apple GPU / MPS 版），使用 ByteDance 的 [piano_transcription_inference](https://github.com/qiuqiangkong/piano_transcription_inference)（MIT）與其模型權重（[Zenodo 4034264](https://zenodo.org/record/4034264)，CC BY 4.0；Kong et al., "High-resolution Piano Transcription with Pedals by Regressing Onsets and Offsets Times"）。本專案是個人專案，與 ByteDance 無關。

macOS 的 `macos/*.command` 和 Windows 的 `windows/*.cmd` 都可以直接雙擊執行；每個工具也都有不開對話框的命令列用法，方便 agent 執行。

固定假設 4/4：第一拍對齊、調號、文字 review、捲簾截圖都以 4 拍一小節計算。

各步驟的規則、門檻依據與已知限制見[設計規格](docs/spec/README.md)。

## Main Pipeline

### `transcribe.command`

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

這個 pipeline 只做 BPM 修正，不對齊第一拍、不寫調號，分手固定兩隻手，也不裁短重疊。

### 建議的後處理順序

轉錄完成後，對 `song_bpmfix_cleaned.mid` 依序執行（全部直接覆寫同一個檔案，不會多出 MIDI）：

| 順序 | 工具 / 腳本 | 處理目的 | 備註 |
|:---:|:---|:---|:---|
| **Step 0** | `transcribe.command` | 音訊轉錄、BPM 修正、基礎清理與初步分手 | 輸出 `song_bpmfix_cleaned.mid` |
| **Step 1** | 主面板「鋼琴捲簾」頁 | 對齊第一拍（手動） | 寫入 `song_bpmfix.mid` 並重寫調號，之後重新執行後處理 |
| **Step 2** | `key_signature.command` | 補寫調號 | 若第一拍沒移動則在此補寫調號 |
| 可選 | `playable_reduction.command` | 刪到兩隻手彈得到 | 做一個人彈的譜時，在分手前執行 |
| **Step 3** | `hand_split_channels.command` | 重新多手分離 | 需要三隻手/四隻手時執行（如 `--hands 3`） |
| **Step 4** | `trim_overlaps.command` | 裁短踏板造成的重疊 | 消除長音重疊，使五線譜與樂譜更整齊 |
| **Step 5** | `pianoroll_snapshot.command` / `note_text_review.py` | 檢查結果 | 輸出捲簾截圖或文字拍位以供確認 |

> [!NOTE]
> 裁短只移動音尾，重跑分手不會恢復原本的音長；要回到裁短前的狀態，需從 `song_bpmfix.mid` 重新執行清理。

非交互執行（agent 用）：

```bash
python src/midi_workbench.py song_bpmfix.mid
python src/key_signature.py song_bpmfix_cleaned.mid
python src/playable_reduction.py song_bpmfix_cleaned.mid
python src/hand_split_channels.py song_bpmfix_cleaned.mid --hands 3
python src/trim_overlaps.py song_bpmfix_cleaned.mid
python src/pianoroll_snapshot.py song_bpmfix_cleaned.mid --layout portrait
```

### `postprocess.py`（統一執行器）

把上面的步驟依序一次跑完，每一步都可以開關。每次都從 `song_bpmfix.mid` 重新產生 `song_bpmfix_cleaned.mid`，所以改了開關重跑，不會疊加上一次的結果，也不會多出 MIDI。輸入可以是同一首的 `song.mid`、`song_bpmfix.mid` 或 `song_bpmfix_cleaned.mid`。

| 順序 | 步驟 | 預設 | 開關與參數 |
|:---:|---|:---:|---|
| 1 | BPMFix、自動對齊第一拍、調號 | 有 `--bpm` / `--audio` 時執行 | `--no-first-beat`、`--no-key-signature`；都不給時沿用現有的 `song_bpmfix.mid` |
| 2 | 規則清理 | 開 | `--no-clean`、`--semitone-pairs` |
| 3 | 可彈性清理 | 關 | `--playable`；每手音數和跨度用 `--set` |
| 4 | 分手 | 開 | `--no-split`、`--hands` |
| 5 | 截斷重疊 | 開 | `--no-trim`、`--fast-figure-hands` |
| 6 | 捲簾截圖 | 關 | `--snapshot landscape / portrait / both` |

- 可彈性清理只能配兩隻手，截斷重疊必須在分手之後；組合不成立時不會執行。
- 統計報告：重跑 BPMFix 時整份重寫；沿用 `song_bpmfix.mid` 時，保留它的品質、BPMFix、第一拍、調號段落，其餘重建。最後附上這次執行的步驟清單。

報告以同一區塊內的項目逐行更新：相同行保留，值變更時更新，新增項目加入，新版已不存在的項目移除。其他區塊保留，舊重複區塊整理為最新一份；完全相同時不寫檔。
- 主面板的鋼琴捲簾把手動調整的第一拍寫入 `song_bpmfix.mid`，後續處理沿用這份檔案。是否自動調整第一拍依處理步驟的勾選執行，不另外判斷手動修改紀錄。

閾值用 `--set 名稱=值` 覆蓋（可重複），`--list-parameters` 列出全部可調的閾值和預設值；有改的值會記在報告的步驟清單裡。可彈性清理的每手音數和跨度也在這裡（`playable.max_notes`、`playable.max_span`）。

```bash
python src/postprocess.py song.mid --audio song.flac
python src/postprocess.py song.mid --bpm 135 --hands 3
python src/postprocess.py song_bpmfix_cleaned.mid --playable --snapshot portrait
python src/postprocess.py song_bpmfix_cleaned.mid --set clean.min_velocity=25 --set playable.max_notes=5
python src/postprocess.py --list-parameters
```

### GUI 啟動入口

本機 Windows 使用 repo 根目錄已建立的 `Piano MIDI Cleanup.lnk` 快捷方式開啟面板，不會顯示命令列視窗。快捷方式屬本機設定，不隨 repo 分發；其他環境可執行 `python src/postprocess_panel.py`。macOS 使用 `macos/postprocess_panel.command`。

統一執行器的 tkinter 面板：選 MIDI、BPM 來源（沿用已修正速度、手動指定、從音訊偵測）、每一步的開關，以及按主題排列的設定分頁。「詳細報告」以項目／值兩欄顯示已生成的 TXT，開啟面板或切換 MIDI 時讀入；底部狀態欄顯示最新狀態。

- 開關和閾值可以存成預設組合（Preset），例如「一人彈」「三手」；Defaults 回到預設值。
- 上次的選擇和預設組合存在 `~/.piano-midi-cleanup-panel.json`。
- 面板只是把選擇轉成 `postprocess.py` 的命令列，執行時會先印出這行命令。

「鋼琴捲簾」頁顯示目前選的 MIDI，切換到這頁或處理完成時自動重新載入，保留捲動位置與縮放：

- 音條依手上色（右手藍、第二右手橙、第二左手紫、左手綠，與 PNG 截圖一致），深淺表示力度；上方標小節號，左側標音名，底部是延音踏板（CC64）踩下的區間。
- 播放、暫停、點擊定位；Ctrl／Alt + 滾輪縮放，Shift + 滾輪水平捲動。可依手關閉聲音。
- 調整速度與第一拍位置時網格即時預覽；按「儲存第一拍」寫入同一首的 `song_bpmfix.mid` 並重寫調號，之後重新執行後處理即可重建 `song_bpmfix_cleaned.mid`。顯示的檔案沒有對應的 `song_bpmfix.mid` 時不能調整。
- 播放預設使用內建 Coopiano 物理建模鋼琴，直接送到系統預設音訊裝置。輸出選單保留本機 MIDI 埠（Windows 包含可用的 `Microsoft GS Wavetable Synth`），macOS／Linux 亦可選 `piano-midi-cleanup` 虛擬埠。選擇會記住；新增此預設時，舊的 MIDI 專用設定不會覆蓋 Coopiano，之後可自行改選並儲存。首次播放會在背景準備音源，完成後自動開始。錯誤會顯示在捲簾頁，不會靜默換成其他音源。

### `transcribe_mps.command`

保留的 Apple GPU / MPS 支持版入口，流程與 `transcribe.command` 相同，但設備選擇順序為：

```text
MPS -> CUDA -> CPU
```

目前在 M2 MacBook Air 實測中，這個模型使用 MPS 反而比 CPU 慢，因此日常使用主入口 `transcribe.command`。

## Standalone Tools

### `bpmfix.py`

BPM 修正、自動對齊第一拍、寫入調號，輸出 `*_bpmfix.mid`。只有命令列用法（給 agent 或腳本），一般使用請用統一執行器或主面板；手動調整第一拍在主面板的鋼琴捲簾。

寫入第一拍時依 BPM 重算 tick，再把第一拍移到第 1 小節開頭；有弱起音落到 0 之前時，在前面補整數小節。channel 不變。結果會更新至 `*_stats.txt`。

`transcribe.command` pipeline 只做 BPM 修正，不對齊第一拍、不寫調號。

自動對齊第一拍只看 BPM 修正後的 MIDI（不需要原音訊），假設固定 4/4：

```text
1. 起音依 velocity 加權，在八分音符週期上取平均相位，得到格線位移
2. 在八分音符的兩個位置中，選落在四分音符格線上權重較大的作為正拍
3. 四拍中左手（channel 3）或低音（<= F#3）起音權重最大的一拍作為第 1 拍
4. 取離 0 秒最近的小節線，已對齊的 MIDI 再跑一次幾乎不動
```

結果（估計位置、八分音符格線一致性、第 1 到 4 拍的低音比例、平移 tick）更新至 `*_stats.txt`。低音大量切分、前奏無低音、3/4 拍時第 1 拍可能選錯，可在主面板的鋼琴捲簾用 ±1 拍修正。

命令列用法：

```bash
python src/bpmfix.py song.mid --audio song.flac
```

```bash
python src/bpmfix.py song.mid --bpm 135 --no-auto-first-beat --no-key-signature
```

自動偵測會用 Essentia 拍點的半拍相位一致性估計實際 BPM（對漏拍、反拍跳動不敏感），再檢查四捨五入成整數後曲尾的格線漂移：

```text
end_drift_ms = duration * |beat_phase_bpm / applied_bpm - 1| * 1000
```

漂移 <= 50 ms 時套用整數 BPM；超過時保留兩位小數。報告會輸出 Essentia 原始 BPM、beat-phase BPM 與 coherence、是否取整，以及 `Estimated end drift`。手動輸入的 BPM 會直接使用。

修正邏輯保留實際播放速度，只重算 MIDI ticks 和 tempo metadata：

```text
new_ticks = old_ticks * applied_bpm / source_bpm
```

`source_bpm` 取自 MIDI 本身的 tempo（轉錄輸出為 120；沒有 tempo event 時視為 120），所以對已修正過的 MIDI 再跑一次也不會改變播放時間。

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
Max simultaneous keys
```

音高會同時輸出 MIDI note number 和英文音名，例如 `24 (C1)`。

「最多同時按鍵數」依 MIDI 音符的按下至放開區間估計，不額外計入延音踏板（CC64）留下的聲音。若來源轉錄把踏板延音直接寫入音長，此值會高估實際按鍵數，不能單憑它判定手指不夠。

### `clean_midi_rules.command`

只做第一階段保守規則清理。

使用方式：

1. 選擇 MIDI。
2. 輸出 `*_cleaned.mid`。
3. 輸出 `*_clean_report.txt`。

注意：獨立執行 `clean_midi_rules.command` 時會輸出 `*_clean_report.txt`；`transcribe.command` pipeline 中，清理報告會更新至原本的 `song_stats.txt`。

清理規則只刪除高度疑似 artifact 的 note，不做音樂語義判斷、不分手、不量化、不判斷和弦/調性/旋律。

目前規則：

```text
duration < 0.03s
velocity < 30
velocity < 45 and duration <= a 16th note (0.25 beat)
pitch outside 21-108
```

可選：`--semitone-pairs` 另外處理半音雙音（預設關閉）。相差一個半音、起音相差 50ms 以內的兩顆音，刪較弱的那顆；力度相同時刪較短的。要判斷哪些半音是和弦本來的音，已經是音樂判斷，所以這裡只做簡單處理，見 `docs/cleaning-rules.md`。

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
- Velocity < 30: 3 (0.30%)
- Velocity < 45 and duration <= 0.25 beat: 18 (1.80%)
- Pitch outside 21-108: 2 (0.20%)
```

所有百分比都以 `Original notes` 為分母。

### `hand_split_channels.command`

只做初步分手，直接在清理後 MIDI 上修改 note channel。預設兩隻手；需要三隻手的曲子加 `--hands 3`（多一個第二右手），四隻手加 `--hands 4`（再多一個第二左手）。一隻手對應一個 channel，不在單手內再分聲部。

它不拆成多個 instrument track，也不刪除 note。

先找每一拍的分團線：鋼琴捲簾上低音、中音常擠成一團，高音的分解和弦在上方另成一團，兩團之間是一段空白音域。對每一拍（含前後各一拍的起音）計算每條分界線到上下最近音的距離，再用 Viterbi 讓分界線盡量走在最寬的空白中、且每移動一個半音付 0.5 的成本。

分配方式是成本最小化的 beam search：起音相差 50 ms 內的音視為同一組，每組由低到高排序後切成每隻手一段（兩隻手：左手、右手；三隻手：左手、第二右手、右手；四隻手：左手、第二左手、第二右手、右手），再在整首曲子上選總成本最低的路徑。成本包括：

```text
單手跨度：12 半音內不計，超過 14 半音大幅加重
音域：左手高於 D4、右手低於 G3 時依距離計算
移動速度：手的位置變化除以該手距離上一個起音的時間（空閒的手移動較便宜）
快速段落：該手已連續彈 3 個以上間隔 < 0.25 s 的單音時，每多一個同時音都加成本（右手快速單音中的雙音傾向交給空閒的左手）
分團：最上面那隻手（右手）拿到分團線以下的音、其他手拿到分團線以上的音，每個音 4；分界線上下的空白小於 4 半音時按比例減輕
第二隻手：三或四隻手時，每個交給第二右手或第二左手的音 1，只在需要時才用
```

報告會輸出單手跨度超過 12 半音的組數，以及快速段落中仍為多音的次數。所有權重都是檔案開頭的常數。

channel 對應：

```text
MIDI channel 1 = right hand
MIDI channel 2 = second right hand（只在 --hands 3）
MIDI channel 3 = left hand
MIDI channel 4 = second left hand（只在 --hands 4）
```

對 `mido` 這類 0-based channel 的 library 來說，channel 0、1、2、3 分別是 MIDI channel 1、2、3、4。

這一步主要是為了讓 Logic Pro 樂譜視圖可以直接用 Piano 1 / Piano 3 顯示左右手，方便人工檢查和編輯；三隻手時需要另外在 Logic 設定含 channel 2 的譜表樣式。

非交互執行：

```bash
python src/hand_split_channels.py song_bpmfix_cleaned.mid
python src/hand_split_channels.py song_bpmfix_cleaned.mid --hands 3
```

### `playable_reduction.command`

可選，給一個人彈的譜用。分手之前執行：刪掉最少的音，讓每個起音組兩隻手都彈得到，直接修改 MIDI。起音相差 50 ms 內的音視為同一組。

```text
彈得到：把組裡的音由低到高切成兩段（低的給左手、高的給右手），有一種切法讓每隻手都不超過 4 個音、跨度不超過 12 半音
刪哪幾個：先求刪的數量最少，再求代價最低；有八度重複的音最便宜，只出現一次的較貴，最高音（旋律）和最低音（低音）最貴，同樣代價時刪較弱的
一組最多刪 3 個；還彈不到的組不動，列在報告裡
```

只看同時起音的音，不處理還按著的音、超過 50 ms 的琶音、兩組之間手移動的距離。分三手、四手的曲子不適用。刪除數和每一處刪除的位置（小節、拍、刪掉的音和原本的組），更新至來源轉錄的 `*_stats.txt`。

非交互執行：

```bash
python src/playable_reduction.py song_bpmfix_cleaned.mid
python src/playable_reduction.py song_bpmfix_cleaned.mid --max-notes-per-hand 5 --max-span 14
```

### `trim_overlaps.command`

分手之後執行：把踏板延音造成、同一隻手內重疊的音裁短，直接修改 MIDI。只移動音尾，不刪音、不動起音。每隻手（channel）分開處理，起音相差 50 ms 內的音視為同一組。

截斷前依同聲道的踏板（CC64 = 127 踩下、0 鬆開）推算止音點：note-off 落在踩踏板期間時，聲音延到下一次鬆踏板。只有截斷後的止音點比原本提前不超過 0.1 秒（`SUSTAIN_TOLERANCE_SECONDS`），且沒有同音重疊／重彈，才接受截斷；踏板與音尾同 tick 時按最不利的先後順序計算。半踏板、其他持音控制或音符配對不完整的聲道保留原音尾。這是延音保護，不保證所有音源聽感完全相同，也不保證處理後所有重疊都消除。分手會同步映射相容的踏板控制；不同來源的踏板若會在目的聲道衝突，則停止分手並保留該次分手前的檔案。

```text
快速音型（預設右手和第二右手）：連續 3 組以上、每組間隔 0.5 拍以內的起音；音型內每個音最晚在同一隻手下一組起音結束
換和聲：某個起音時刻所有手合起來有 3 個以上不同音名時，這隻手在 MIDI 中尚未放開、音名不在其中的音在此結束
換和弦：一隻手彈新的和弦或八度（2 個音以上）時，上一個和弦在 MIDI 中尚未放開的音在此結束
按鍵需求過大：按 MIDI 音長估計，前音加新音超過 5 個或跨度超過 14 半音時，前音在此結束
跨節拍線：一隻手在節拍線前後 1/8 拍內有新起音時，跨過該線仍未放開的音在此結束；線前 1/4 拍內才起音的音不算
```

各規則的裁短數，以及每隻手「最多同時按鍵數」的處理前後值，更新至來源轉錄的 `*_stats.txt`。這些規則整理的是 MIDI 譜面上的按鍵需求，不限制踏板延續的實際發聲數。

非交互執行：

```bash
python src/trim_overlaps.py song_bpmfix_cleaned.mid
python src/trim_overlaps.py song_bpmfix_cleaned.mid --fast-figure-hands right second_right left
```

`--fast-figure-hands` 指定套用快速音型的手。左手預設不套，因為它也會截斷分解和弦下按著的低音。

### `midi_workbench.py`

不經過 BPM 修正，依音符自動把第一拍對齊到小節線，直接覆寫所選檔案（通常是 `song_bpmfix.mid`）；第一拍有移動時重新寫入調號（`--no-key-signature` 關閉）。只有命令列用法，手動調整請用主面板的鋼琴捲簾。

```bash
python src/midi_workbench.py song_bpmfix.mid
```

### `key_signature.command`

依每 4 小節一段判斷調號並寫入 MIDI 的 key signature event（直接覆寫），讓 Logic 樂譜在轉調處換調號。需要已對齊小節的 MIDI（BPM 修正加第一拍對齊之後）。

判斷方式：

```text
1. 從弱起之後的第一個完整小節開始，每 4 小節一段（弱起音算進第一段）
2. 每段對 12 個大調調號計算需要臨時記號的音符數（關係小調共用調號）
3. Viterbi 選臨時記號總數最少的路徑；每次換調額外計 4 個音的成本，只在段落交界換調
4. 只有臨時記號超過該段音符 10% 的 4 小節段才逐小節檢查：用該段與前後段的調號，在臨時記號最少的小節線切開，讓換調可以落在段內
5. 6 / 7 個升降號的同音異名調用 F# 和 Db
```

原有的 key signature event 會全部替換；每段的調號、臨時記號數更新至來源轉錄的 `*_stats.txt`。固定假設 4/4。

非交互執行：

```bash
python src/key_signature.py song_bpmfix_cleaned.mid
```

### `generate_chord_review.command`

開發中：輸出內容尚未定案，不屬於預設流程，只在明確需要時手動執行。

只讀取已完成後處理的 MIDI，輸出英文 Markdown 格式的 vertical chord chart。它不修改 MIDI。

命令：

```bash
python src/generate_chord_review.py input.mid
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

### `note_text_review.py`

只讀取已對齊小節的 MIDI，把音符印成文字，每個起音群組一行：小節、拍位，再由低到高列出每個音的手（`R` 右手、`R2` 第二右手、`L2` 第二左手、`L` 左手，未分手為 `?`）、音名和長度（拍）。它不修改 MIDI，也不屬於預設流程，用於檢查分手、調號等問題時手動執行。固定假設 4/4。

```bash
python src/note_text_review.py song_bpmfix_cleaned.mid --bars 95-97
python src/note_text_review.py song_bpmfix_cleaned.mid -o song_notes.txt
```

輸出範例：

```text
m96 beat 2.51  LA#3(0.99b) LC#4(1.22b) RG#4(1.00b)
```

### `pianoroll_snapshot.command`

把已對齊小節的 MIDI 輸出成鋼琴捲簾 PNG，每張 4 小節，不修改 MIDI。顏色區分手：藍 = 右手、橙 = 第二右手、紫 = 第二左手、綠 = 左手。每張的音域依那 4 小節的音自動決定。

```text
landscape：4 小節排成一行，1920 寬
portrait：橫向圖順時針旋轉 90 度，4 小節由上往下連續排列，適合手機查看
```

預設兩種都輸出到 MIDI 旁邊的 `<MIDI 名>_pianoroll/`（已加入 .gitignore）。

```bash
python src/pianoroll_snapshot.py song_bpmfix_cleaned.mid
python src/pianoroll_snapshot.py song_bpmfix_cleaned.mid --bars 17-32 --layout portrait -o out_dir
```

## Python Entrypoints

這些 `.command` 背後呼叫的 Python 腳本：

```text
src/transcribe.py
src/transcribe_mps.py
postprocess.py
postprocess_panel.py
first_beat.py
pianoroll_canvas.py
bpmfix.py
midi_stats_command.py
clean_midi_rules.py
hand_split_channels.py
playable_reduction.py
trim_overlaps.py
midi_workbench.py
key_signature.py
generate_chord_review.py
note_text_review.py
pianoroll_snapshot.py
```

共用 helper：

```text
bpmfix_utils.py
midi_playback.py
midi_stats.py
detect_chords_tonal.js
vendor/tonal.min.js
```

`hand_split_channels.py` 也提供讀取音符、分組和 channel 對應給其他工具使用。

## Dependencies

所有 `.command` 和 BPM 偵測共用同一個使用者層級 Python 環境（需要 tkinter，Homebrew 用 `python@3.11` 加 `python-tk@3.11`）：

```text
$HOME/.local/share/piano-midi-cleanup-env
```

`.command` 找不到這個環境時，才依序嘗試 `./.venv` 和系統 Python。

建立環境（Intel Mac 版本鎖定：PyTorch 最後的 macOS x86_64 版本是 2.2.2，需搭配 `numpy<2`；numba / llvmlite 新版沒有 x86_64 wheel）：

```bash
python3.11 -m venv ~/.local/share/piano-midi-cleanup-env
```

```bash
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install "torch==2.2.2" "numpy<2" "numba==0.62.1" "llvmlite==0.45.1" "essentia==2.1b6.dev1389" mido librosa piano_transcription_inference "python-rtmidi==1.5.8" pillow sounddevice
```

Apple Silicon（arm64）不需要上面的 Intel 版本鎖定，用 uv 建立後安裝同一組套件即可（已驗證：torch 2.14.1、numpy 2.4.6、essentia 2.1b6-dev）：

```bash
uv venv --python 3.11 ~/.local/share/piano-midi-cleanup-env
VIRTUAL_ENV=~/.local/share/piano-midi-cleanup-env uv pip install torch numpy numba llvmlite essentia mido librosa piano_transcription_inference python-rtmidi pillow sounddevice
```

`pianoroll_snapshot.py` 用 Pillow 畫圖。

ffmpeg 需在 PATH 上（librosa 讀取 m4a / mp3 等格式時使用）。

Tonal.js chord detection and Windows automatic BPM detection require Node.js. The launcher searches `PATH` and common Node locations; `PIANO_MIDI_CLEANUP_NODE` can override it. 本機 Node 放在 `$HOME/.local/share/node-<version>-darwin-x64`，並以 `$HOME/.local/bin/node` 連結加入 PATH。

鋼琴捲簾預設用 Coopiano 直接發聲，音訊輸出使用 `sounddevice`，外部 MIDI 輸出使用 `python-rtmidi`（均列在安裝指令中）。Windows x64 的原生音源 DLL 已隨 repo 提供；macOS／Linux 需先在 `sound_sources/coopiano` 執行 `make playback`。音源、授權與建置方式見 [sound_sources/README.md](sound_sources/README.md)。

Vendored JS:

```text
vendor/tonal.min.js
vendor/essentia-wasm.umd.js
vendor/essentia.js-core.umd.js
```

Tonal.js 用於和弦偵測；essentia.js（WebAssembly）用於自動 BPM 偵測。授權檔分別是 `vendor/TONAL_LICENSE.txt` 與 `vendor/ESSENTIA_LICENSE.txt`。

模型權重預設位置：

```text
$HOME/piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth
```

可覆蓋的環境變數：

```text
PIANO_MIDI_CLEANUP_BPM_ENV
PIANO_MIDI_CLEANUP_BPM_PYTHON
```

## Windows

各工具在倉庫的 `windows/` 目錄有同名的 `.cmd` 啟動檔（例如 `windows/transcribe.cmd`；只有命令列用法的 `bpmfix.py`、`midi_workbench.py` 沒有），macOS 啟動檔則存放在 `macos/` 目錄（例如 `macos/transcribe.command`），可以直接雙擊，也能在 PowerShell 執行。啟動檔會先切換到自己的資料夾並設定 `PYTHONUTF8=1`，再依序尋找：

```text
%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe
.venv\Scripts\python.exe
py -3.11
PATH 上的 python
```

找不到 Python 時會顯示訊息並暫停。所有 Python CLI 都可以直接用 PowerShell 執行，例如 `python src/bpmfix.py song.mid --bpm 135`。

建立共用的 Python 3.11 環境：

```bat
py -3.11 -m venv %USERPROFILE%\.local\share\piano-midi-cleanup-env
%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe -m pip install mido librosa piano_transcription_inference python-rtmidi pillow sounddevice
```

需要 NVIDIA GPU 時，把第一行 pip 換成 PyTorch 官網目前的 CUDA wheel index（例如 `--index-url https://download.pytorch.org/whl/cu126`）。Essentia 沒有 Windows wheel，因此不安裝；自動 BPM 偵測改用倉庫內建的 essentia.js（WebAssembly），需要 Node.js。因為音訊解碼與重取樣方式不同，結果可能與 Python Essentia 版本略有差異。

`ffmpeg` 需在 PATH 上（librosa 讀取 m4a / mp3 等格式時使用）。

鋼琴捲簾預設使用 Coopiano 直接發聲，不需 VST、虛擬 MIDI 線或外部播放器；仍可在輸出選單改選 GS 或其他 MIDI 埠。Coopiano 使用系統預設音訊裝置及其取樣率；遠端桌面工作階段須有可用的音訊播放裝置。本次整合已完成原生 DLL 建置，尚未實際驗證 GUI／RDP 試聽。Node.js 會從 PATH 或 `%ProgramFiles%\nodejs` 尋找，也可用 `PIANO_MIDI_CLEANUP_NODE` 指定；自動 BPM 偵測與和弦偵測都需要它。

Windows 上不支援：

```text
transcribe_mps.command / src/transcribe_mps.py（Apple MPS）
```

## Notes

- `src/transcribe.py` 會依序嘗試 CUDA、CPU。
- `src/transcribe_mps.py` 會依序嘗試 MPS、CUDA、CPU；在 Apple Silicon 且 PyTorch MPS 可用時會使用 Apple GPU。
- BPM 修正以固定 BPM 為前提，不建立 tempo map。
- 小節計算一律假設 4/4，3/4 等拍號尚未支援；調號只寫大調，小調以關係大調表示。
- 規則清理是低風險 preprocessing；後續更複雜的判斷應放在 feature extraction 或 ML classifier 等更高階流程中。
