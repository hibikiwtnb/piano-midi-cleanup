# 鋼琴捲簾整合進主面板：調查與設計

狀態：設計定案中，未實作。已決定：第一拍編輯固定寫入 `song_bpmfix.mid`；不做對照疊圖。待決定事項見最後一節。

## 1. 現狀調查

### 相關模組

| 模組 | 角色 |
|---|---|
| `src/pianoroll_canvas.py` | `PianoRollWorkbench`：Tk Canvas 捲簾 + BPM／第一拍編輯 + 播放。建構時接管傳入的視窗（設標題、尺寸，關閉時 `destroy`） |
| `src/first_beat_ui.py` | 命令列入口用的包裝，自己建 `Tk()` 跑 mainloop |
| `src/midi_playback.py` | `MidiPlayback`：背景執行緒按時間送 MIDI 到輸出埠；`set_mix()` 可依手靜音／獨奏，目前沒有介面使用 |
| `src/postprocess_panel.py` | 主面板：`Notebook` 有「處理」「詳細報告」兩頁；「Piano Roll / First Beat」按鈕另開 `Toplevel` 視窗 |
| `src/pianoroll_snapshot.py` | 輸出 PNG 捲簾（PIL），與 Canvas 捲簾沒有共用程式 |

呼叫者：主面板按鈕、`bpmfix.py` 互動流程、`midi_workbench.py`（後兩者經 `first_beat_ui.run_first_beat_ui`）。

### 現有捲簾的問題

1. **改 offset 看不到效果**：拍線永遠從 0 秒畫起，輸入的第一拍 offset 不會反映在畫面上，只能按 Apply 寫檔後重開才知道對不對。
2. **只分兩種手色**：`hand_for_channel()` 把聲道 1、2 都當右手，3、4 都當左手；三手、四手的第二隻手看不出來。顏色也和 PNG 截圖不一致。
3. **沒有音名**：左側預留 64 px 但沒有鍵盤或音名，看不出音高。
4. **拍號標示**：每拍標的是累計拍數，不是小節號。
5. **看不到踏板**：CC64 完全不顯示；研究截斷、踏板時最需要看的資訊不在畫面上。
6. **寫入目標**：Apply 直接覆寫目前選的檔案。主面板通常選的是 `song_bpmfix_cleaned.mid`，在它上面調第一拍，下次重建就會消失（執行器會擋下，但使用者要再回頭處理）。
7. **與面板分離**：另開視窗；執行後處理之後不會自動重新載入。

### 播放輸出

Windows 預設送到 Microsoft GS Wavetable Synth，也可選其他 MIDI 輸出埠（例如 VST MIDI System Synth）。macOS／Linux 建立虛擬埠。CoPiano 的即時播放尚未接入（`docs/open-issues.md`），整合不改變這部分。

## 2. 設計

### 目標

主面板新增第三個分頁「鋼琴捲簾」，顯示目前選擇的 MIDI，處理完自動重新載入，可播放、檢查分手與截斷結果，並可在同一處調 BPM 與第一拍。

### 架構

把 `PianoRollWorkbench` 拆成兩層：

| 元件 | 內容 |
|---|---|
| `PianoRollView(ttk.Frame)` | 讀檔、繪製、播放、縮放捲動、第一拍編輯；不碰視窗。對外提供 `load(path)`、`stop()`，以及 Apply 完成的回呼 |
| 視窗包裝 | `first_beat_ui` 建一個視窗放 `PianoRollView`，維持命令列入口的行為與回傳值 |

主面板在 `Notebook` 加一頁放 `PianoRollView`，原本另開視窗的按鈕改為切換到這一頁。

資料讀取由捲簾自己按 tick 讀（音符、聲道、CC64），換算秒數用現有的 `tick_seconds()`，不再依賴 `read_midi_events()` 的兩手分類。

### 顯示

| 項目 | 設計 |
|---|---|
| 手色 | 與 `pianoroll_snapshot.py` 一致：右手藍、第二右手橙、第二左手紫、左手綠；未分手單色 |
| 網格 | 小節線、拍線、十六分音符細線，依 MIDI 的拍子網格；上方標小節號 |
| 第一拍預覽 | 編輯 BPM／offset 時，網格即時按新值重畫，音符不動；確認位置後才寫檔 |
| 音名 | 左側固定鍵盤欄，C 標音名；捲動時不跟著水平移動 |
| 踏板 | 底部一條踏板列，畫出各手 CC64 踩下的區間 |
| 力度 | 音條深淺表示力度（可關） |

### 播放

- 沿用 `MidiPlayback` 與輸出埠選擇；記住上次選的輸出埠。
- 依手靜音／獨奏：擴充 `set_mix()` 的分組，從兩組改為四手。
- 後處理開始執行前停止播放；執行完重新載入檔案，保留目前的捲動位置與縮放。

### 第一拍編輯

- 編輯一律寫入 `song_bpmfix.mid`（第一拍屬於它的報告區塊，第 2 章），寫入後提示重新執行後處理以重建清理結果。
- 目前選的若是 `song_bpmfix_cleaned.mid`，捲簾顯示它，但編輯對象是同一首的 `song_bpmfix.mid`。

### 效能

一首約 3,000–5,000 個音、數千條網格線，Canvas 全部重畫可接受。縮放時只重算座標；若之後遇到長曲卡頓，再改為只畫可見範圍。

### 不做

- 不做音符編輯（拖曳、刪除、改長度）；工具仍以規則處理為主，捲簾只負責檢查與第一拍。
- 不改 PNG 截圖；兩者只共用顏色定義。
- 不做與其他檔案的對照疊圖。

## 3. 實作分段

1. 拆出 `PianoRollView`，視窗包裝維持現有命令列行為；修正 offset 預覽。
2. 主面板加分頁、選檔與執行後自動重新載入。
3. 四手顏色、音名欄、小節號、踏板列。
4. 依手靜音／獨奏。

每段完成都可獨立使用。

## 4. 待決定事項

1. 整合後是否保留獨立視窗入口（`bpmfix.py` 互動流程與 `midi_workbench.py`），或全部改走主面板。這與 MIDI 工作台是否整合進主 GUI 的決定相關。
