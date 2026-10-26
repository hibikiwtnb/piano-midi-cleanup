# 6. 分手

## 目的

把每顆音分配給一隻手，以 MIDI channel 標記，讓樂譜軟體依手別分譜表，並讓截斷可以按手處理。支援二到四隻手；實際譜表配置需在樂譜軟體中設定。

程式：`src/hand_split_channels.py`。直接覆寫 `song_bpmfix_cleaned.mid`。

| 手數 | 由低到高 |
|---|---|
| 2 | 左手、右手 |
| 3 | 左手、右手、第二右手 |
| 4 | 左手、右手、第二左手、第二右手 |

三、四手時，前兩隻手固定屬於第一位演奏者；第二位演奏者坐在右側，因此其手只會出現在更高音域，不會插進第一位演奏者的左右手之間。

聲道：右手 1、第二右手 2、左手 3、第二左手 4。

> **本程式特性，不要改：** 以下是本專案固定的音樂語義 invariant，不是可順手重構的實作細節。除非另立設計變更並補上行為回歸測試，不得因為 hand naming、channel、ownership、topology 或資料結構調整而修改。
>
> - 三手 topology：`left → right → second_right`。
> - 四手 topology：`left → right → second_left → second_right`。
> - Blob line 是「lower/middle musical mass vs separated upper material」的結構分界，不是第一位演奏者的 left/right 分界。
> - 三手：`left + right` 在 blob line 下方，`second_right` 在上方。
> - 四手：`left + right` 在下方，`second_left + second_right` 在上方。
> - 三手第二演奏者以整小節的 n 小節區段啟用，不可改成逐個 onset 自由加入；四手不受這個開關影響。

## 方法

### 1. 起音組

起音相差 50 ms 內的音為一組。每組的音由低到高排列，切成連續的幾段，一段給一隻手，所以同一時刻手不會交叉。

### 2. 高低兩團的分界線

每拍求一條音高分界線：

- 兩手時，下方給 `left`、上方給 `right`。
- 三手時，下方給第一位演奏者的 `left + right`，上方給第二位演奏者的 `second_right`。
- 四手時，下方給第一位演奏者的 `left + right`，上方給第二位演奏者的 `second_left + second_right`。

分界線的求法：

- 每拍取前後各一拍內的起音，在每個候選音高計算「空白音區的半寬」（上下最近音的距離，上限 12 半音）。
- 以 Viterbi 沿時間選分界線，總空白最大，分界線每移動一個半音扣 0.5。
- 空白音區越窄，分界線的約束越弱（clarity：空白半寬達 4 半音為完整約束，`BLOB_CLEAR_GAP`）。
- 三、四手時，第二位演奏者的手是 upper blob 的承擔者；不可把 blob 改回只約束第一位演奏者的 `left/right`。

### 2.1 三手第二演奏者的 n 小節區段

三手時，`second_right` 不是逐個起音自由加入，而是以整小節為單位決定是否啟用：

- 每個小節第一個起音組決定 ON／OFF；同一小節內維持不變。
- OFF：`second_right` 必須為空，該組只能由 `left + right` 分配。
- ON：`second_right` 可用，但不強迫；仍受跨度、手指數、移動、音區、快速音群與 `SECOND_HAND_NOTE_COST` 限制。
- OFF → ON 付一次 `SECOND_PLAYER_ACTIVATION_COST`；ON 期間每小節付 `SECOND_PLAYER_BAR_COST`。
- ON 至少持續 `SECOND_PLAYER_MIN_BARS` 小節（預設 4）；未滿不得 OFF，期滿可繼續 ON 而不重收 activation。
- OFF 後再 ON 重新收 activation；ON → OFF 時清除 `second_right` 的移動與快速音群歷史，`left`、`right` 的歷史保留。
- 小節以 `start_ticks // (ticks_per_beat * 4)` 計算；本程式固定 4/4。
- 四手模式不使用這個開關。

### 2.1.1 三手稀疏小節的整小節休止

束搜尋完成手部分配後、改寫聲道與踏板之前，三手會再做一次整小節的實際參與檢查。這不改變 2.1 的 ON／OFF 區段（ON 仍是樂句層級的可用性），只決定第二位演奏者在該小節是否實際休息：

- 僅當該小節全部 `second_right` 音共 1–2 個（`SECOND_PLAYER_SPARSE_NOTES = 2`）時考慮。
- 逐一檢查該小節所有含 `second_right` 的起音組：每組都必須已有一個至少兩個音的 `right` 和弦，且該組 `right + second_right` 的合併音數與跨度符合 `playable.max_notes`、`playable.max_span`（預設 4、12）。
- 全部符合時，將該小節所有 `second_right` 音改回 `right`，第二位演奏者整小節休息；任一組不符合則整個小節維持原分配，不做部分吸收。
- 不會吸收獨立的單音上聲部旋律、密集參與，或超音數／超跨度的和弦。
- 只改手別，不增刪音、不改時間、手部順序、Blob 語義或 2.1 的最短小節區段；兩手與四手不受影響。

### 2.2 第二右手音域下界（軟性偏好）

`split.second_right_min_pitch` 是三、四手時 `second_right` 的軟性音域下界，不是候選切法的硬性過濾；低於此音高的音仍可分給 `second_right`，只是會被課成本。此偏好不改變手部拓撲、Blob 分界線語義或三手小節啟用規則，也不會刪除任何音。

- `second_right` 每個低於下界的音，付 `10 ×（下界 − 音高）²`；越界越深成本越高，少量越界仍可能出現。
- 三手且該小節第二位演奏者為 ON 時，第一位演奏者的 `right` 每個達到或高於下界的音，另付 `10 ×（音高 − 下界 + 1）²`；OFF 小節不套用此項，右手可留在高音區。
- 預設 0 表示關閉此偏好。
- 可輸入音名（如 `C5`）或 MIDI 數字（如 `72`）；中央 C 為 `C4 = 60`。
- GUI 會自動列在分手分頁；這是使用者可設定的額外偏好，不改變 Blob 分界線的語義或手部順序。

### 2.3 三手的短暫置信度缺口

Blob 分界線的 clarity 是每拍實際量到的空白音區證據。clarity 為 0 表示該拍沒有可用的分界證據，不一定代表分界線已改變。

三手時（`hands == 3`），若連續 0 置信度的拍數不超過 `2 * BLOB_CONTEXT_BEATS` 拍（預設 2 拍），前後又緊鄰正置信度的拍，且 Viterbi 在兩端選到完全相同音高的分界線，則此缺口視為同一 blob 的短暫休息或漏起音，而不是第二位演奏者的新邊界；缺口內的各拍沿用兩端置信度的較小值。

此繼承值是**推論的**置信度，不是該拍實際量到的證據；它只表示分界線在短缺口內沿用兩端的相同分界。此修正只調整 clarity，不改變 Viterbi 選出的分界線音高，也不改變手部 ownership。此機制刻意只套用於三手；兩手與四手的置信度處理維持不變。缺口過長、缺口未被正置信度證據包夾，或前後分界線不同時，仍維持原本處理，包含保留 0 置信度。此機制用於修正單一 blob 短暫休息且漏起音時，upper voice 換手位置被誤判的問題。

### 2.4 旋律換手抑制

`split.voice_continuity_cost` 預設 0（關閉），可先試 1。啟用時：

- 在起音組之間建立旋律連結：以音高與時間距離互為最近鄰（同時看前後方向）的音配對。
- 只連結音高相差不超過 5 半音、間隔小於 2 個四分音符拍（`MELODIC_MAX_STEP = 5`，沿用兩拍上下文）的音；同一組起音內同時發聲的音（含同音重複）不提供連結。
- 若連結兩端分屬不同手，加收 `split.voice_continuity_cost × 權重`；權重隨音高與時間距離增加而遞減，越近的音越傾向由同一隻手接續。
- 正向與反向匹配都會納入；束搜尋在路徑上累積連結兩端（來源與目標）的手別差異成本。這是局部啟發式，不保證全域最佳。
- 此項只疊加成本，不改動音符、手部拓撲、Blob 分界線或三手小節啟用規則。

### 3. 束搜尋

依時間逐組考慮所有切法，保留成本最低的 32 個狀態（`BEAM_WIDTH`），最後回溯成本最低的路徑。每組每種切法的成本是下列各項之和：

束搜尋可接受每手最多音數與最大跨度的限制（`limits`）：套用時，每組只考慮兩手都符合限制的切法；若該組沒有符合的切法，該組全部原本不受限制的候選切法仍然可用，由一般束搜尋成本決定分配；保留全部音符，不中斷也不刪音。未解決的組由可彈性清理列在報告中。統一執行器在 `--playable` 且兩手時，以 `playable.max_notes`、`playable.max_span` 作為此限制。

| 成本 | 計算 | 參數 |
|---|---|---|
| 跨度 | 每手跨度超過 12 半音，每半音 3；超過 14 半音，每半音 20 | `COMFORT_SPAN = 12`、`HARD_SPAN = 14` |
| 手指數 | 每手超過 5 個音，每個 20 | `MAX_FINGERS = 5`、`EXTRA_FINGER_COST = 20` |
| 音區 | 第一位演奏者左手高於 62（D4）、右手低於 55（G3），每半音 1 | `LEFT_COMFORT_MAX`、`RIGHT_COMFORT_MIN` |
| 移動 | 0.01 × 該手中心音高變化 ÷ 距上次起音的秒數（最少 0.05 秒） | `MOVE_COST_WEIGHT`、`MIN_MOVE_SECONDS` |
| 快速音群中的和弦 | 連續 3 個以上單音、每個間隔 < 0.25 秒時，該手多彈的每個音 3 ×（1 − 間隔 ÷ 0.25） | `FAST_RUN_*` |
| 分界線 | 兩手時 `left` 在線下、`right` 在線上；三、四手時第一位演奏者的 `left/right` 在線下、第二位演奏者的手在線上。每個錯側音 4 × clarity | `BLOB_SIDE_COST = 4` |
| 第二位演奏者 | 三、四手時，每個分給第二右手或第二左手的音 1，只在需要時使用 | `SECOND_HAND_NOTE_COST = 1` |
| 第二演奏者區段 | 三手時 OFF → ON 一次 8；ON 期間每小節 1；至少 N 小節 | `SECOND_PLAYER_ACTIVATION_COST = 8`、`SECOND_PLAYER_BAR_COST = 1`、`SECOND_PLAYER_MIN_BARS = 4` |
| 第二右手音域 | 三、四手時，`second_right` 低於下界的音 10 × 距離²；三手 ON 時，第一位演奏者 `right` 達到或高於下界的音 10 ×（超出量 + 1）² | `SECOND_RIGHT_MIN_PITCH`、`SECOND_RIGHT_REGISTER_COST = 10` |
| 旋律換手 | 旋律連結兩端換手時，加收成本 × 隨音高與時間遞減的權重 | `MELODIC_CONTINUITY_COST`、`MELODIC_MAX_STEP = 5` |

### 4. 踏板映射

CC64 是逐聲道的控制。改寫音符聲道之前，先把來源聲道的踏板類控制（CC64、66、69 與 channel mode 120–127）複製到所有使用該來源的手：

- 單一來源聲道分成多手：每隻手都得到相同的踏板序列。
- 多個來源聲道要併入同一隻手，而它們的踏板序列不同；或目的聲道原本就有不同的踏板：報錯，不儲存分手結果，不擅自合併。

## 參數

| 參數 | 預設 | `--set` |
|---|---|---|
| `GROUP_START_WINDOW_SECONDS` | 0.05 | `split.group_window`（分手、可彈性清理、截斷共用） |
| `COMFORT_SPAN` | 12 | `split.comfort_span` |
| `HARD_SPAN` | 14 | `split.hard_span` |
| `REPEAT_NOTE_COST` | 0（關閉） | `split.repeat_cost`（實驗性；可先試 1） |
| `MELODIC_CONTINUITY_COST` | 0（關閉） | `split.voice_continuity_cost`（可先試 1） |
| `SECOND_PLAYER_MIN_BARS` | 4 | `split.second_player_bars`（三手；正整數，最小 1） |
| `SECOND_RIGHT_MIN_PITCH` | 0（關閉偏好） | `split.second_right_min_pitch`（三、四手；軟性下界；音名或 MIDI，中央 C 為 C4 = 60） |

其餘成本權重為程式常數，見上表。

`split.repeat_cost` 是選用的同音換手成本。同一音高在兩拍（四分音符拍）內換手時，依距上次起音的時間線性遞減加收成本，兩拍後歸零；同時重複的同音無法判斷連續性，不作為證據。此項只疊加在既有成本與手部限制之上，屬實驗性，不保證修正結果。

`split.voice_continuity_cost` 是選用的旋律換手成本，機制見 2.4。它與 `split.repeat_cost` 同屬軟性偏好，只影響成本排序，不保證全域最佳，也不改變音符數量、手部拓撲、Blob 語義或三手小節啟用規則。

## 報告

`MIDI provisional hand split stats`：各手音數與比例、起音組數、手跨度超過 12 半音的組數、快速音群中多音的次數。三手時另列第二位演奏者的最短啟用小節數。

## 已知限制

- 結果是初步分配；快速交錯、交叉手、不按常規的編曲可能需要人工修正。
- 同一時刻手不交叉，無法表示交叉手。
- 手指數與跨度只看同一組起音的音，不考慮前面仍按著的音；不使用音長。
- 三手第二演奏者區段以 4/4 小節計算；變拍號、自由速度或小節線偏移時，區段界線會失準。
